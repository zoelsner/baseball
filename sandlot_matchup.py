"""Deterministic matchup projection helpers for Sandlot snapshots."""

from __future__ import annotations

import math
from datetime import date
from typing import Any


INACTIVE_SLOTS = {"BN", "IL", "IR", "RES", "RESERVE", "BE", "BENCH"}
BENCH_SLOTS = {"BN", "BE", "BENCH", "RES", "RESERVE"}
UNAVAILABLE_INJURIES = {"OUT", "IL", "IL10", "IL60", "IR"}
MODEL_VERSION = "matchup_projection_v1"
HITTER_POSITIONS = {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF"}
PITCHER_POSITIONS = {"P", "SP", "RP"}
POSITION_ALIASES = {
    "LF": "OF",
    "CF": "OF",
    "RF": "OF",
    "STARTING": "SP",
    "RELIEF": "RP",
}
GENERIC_POSITIONS = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "HIT", "PIT", "ALL"}
SLOT_COMPATIBILITY = {
    "UTIL": HITTER_POSITIONS,
    "MI": {"2B", "SS"},
    "CI": {"1B", "3B"},
    "P": PITCHER_POSITIONS,
    "OF": {"OF", "LF", "CF", "RF"},
}


def compute_projection(
    snapshot: dict[str, Any],
    data_quality: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Project the current matchup final score from existing snapshot data."""
    if isinstance(data_quality, dict) and not data_quality.get("projection_ready"):
        return None

    matchup = snapshot.get("matchup")
    if not isinstance(matchup, dict) or not matchup:
        return None

    my_score = _number(matchup.get("my_score"))
    opp_score = _number(matchup.get("opponent_score"))
    if my_score is None or opp_score is None:
        return None

    complete = bool(matchup.get("complete"))
    if complete:
        projected_my = round(my_score, 1)
        projected_opp = round(opp_score, 1)
        win_probability = _deterministic_prob(my_score - opp_score)
        return {
            "model_version": MODEL_VERSION,
            "projected_my": projected_my,
            "projected_opp": projected_opp,
            "my_remaining_games": 0,
            "opp_remaining_games": 0,
            "win_probability": win_probability,
            "drivers": _drivers(
                my_score=my_score,
                opp_score=opp_score,
                projected_my=projected_my,
                projected_opp=projected_opp,
                my_games=0,
                opp_games=0,
                win_probability=win_probability,
                data_quality=data_quality,
            ),
            "complete": True,
        }

    period_end = _parse_date(matchup.get("end"))
    if period_end is None:
        return None

    roster = snapshot.get("roster") or {}
    my_rows = roster.get("rows") if isinstance(roster, dict) else None
    opp_rows = _opponent_rows(snapshot, matchup)
    if not isinstance(my_rows, list) or not isinstance(opp_rows, list):
        return None

    mu_my, var_my, my_games = _team_projection(my_rows, my_score, period_end)
    mu_opp, var_opp, opp_games = _team_projection(opp_rows, opp_score, period_end)
    if my_games + opp_games <= 0:
        return None

    projected_my = round(mu_my, 1)
    projected_opp = round(mu_opp, 1)
    win_probability = round(_win_prob(mu_my, var_my, mu_opp, var_opp), 4)
    return {
        "model_version": MODEL_VERSION,
        "projected_my": projected_my,
        "projected_opp": projected_opp,
        "my_remaining_games": my_games,
        "opp_remaining_games": opp_games,
        "win_probability": win_probability,
        "drivers": _drivers(
            my_score=my_score,
            opp_score=opp_score,
            projected_my=projected_my,
            projected_opp=projected_opp,
            my_games=my_games,
            opp_games=opp_games,
            win_probability=win_probability,
            data_quality=data_quality,
        ),
        "complete": False,
    }


def projection_log_payload(
    snapshot_id: int,
    snapshot: dict[str, Any],
    data_quality: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if data_quality is None:
        try:
            import sandlot_data_quality

            data_quality = sandlot_data_quality.snapshot_data_quality(snapshot)
        except Exception:
            data_quality = None
    projection = compute_projection(snapshot, data_quality)
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    if not projection or not matchup:
        return None

    predicted_my = _number(projection.get("projected_my"))
    predicted_opp = _number(projection.get("projected_opp"))
    win_probability = _number(projection.get("win_probability"))
    if predicted_my is None or predicted_opp is None or win_probability is None:
        return None

    return {
        "snapshot_id": snapshot_id,
        "model_version": projection["model_version"],
        "matchup_key": _matchup_key(snapshot, matchup),
        "period_id": _period_id(matchup),
        "my_team_id": _text(matchup.get("my_team_id") or snapshot.get("team_id")),
        "opponent_team_id": _text(matchup.get("opponent_team_id")),
        "predicted_my": predicted_my,
        "predicted_opp": predicted_opp,
        "predicted_margin": round(predicted_my - predicted_opp, 1),
        "win_probability": win_probability,
        "data_quality": data_quality or {},
    }


def simulate_lineup_move_impact(
    snapshot: dict[str, Any],
    data_quality: dict[str, Any] | None = None,
    *,
    limit: int = 5,
    min_points_delta: float = 0.0,
) -> dict[str, Any]:
    """Compare legal bench-to-active lineup moves against the base projection."""
    data_quality = _recommendation_quality(snapshot, data_quality)
    if isinstance(data_quality, dict) and not data_quality.get("recommendations_ready", True):
        return _no_action_result(
            base_projection=compute_projection(snapshot, data_quality),
            reason="Recommendation data incomplete: " + _quality_reason(data_quality),
        )

    base_projection = compute_projection(snapshot, data_quality)
    if not base_projection:
        return _no_action_result(
            base_projection=None,
            reason="No matchup projection is available for lineup move simulation.",
        )

    matchup = snapshot.get("matchup")
    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    rows = roster.get("rows") if isinstance(roster, dict) else None
    if not isinstance(matchup, dict) or not isinstance(rows, list):
        return _no_action_result(
            base_projection=base_projection,
            reason="Roster or matchup data is missing from the snapshot.",
        )

    active_rows = [_indexed_row(row) for row in rows if _is_active_lineup_row(row)]
    bench_rows = [_indexed_row(row) for row in rows if _is_bench_row(row)]
    active_rows = [row for row in active_rows if row]
    bench_rows = [row for row in bench_rows if row]
    if not active_rows or not bench_rows:
        return _no_action_result(
            base_projection=base_projection,
            reason="No active-and-bench roster combination is available to simulate.",
        )

    actions: list[dict[str, Any]] = []
    best_rejected_delta: float | None = None
    seen: set[tuple[tuple[str, str, str], ...]] = set()

    for bench in bench_rows:
        bench_slot = _slot(bench) or "BN"
        for active in active_rows:
            target_slot = _slot(active)
            if not target_slot or not _can_play_slot(bench, target_slot):
                continue
            chain = [
                _move_step(bench, bench_slot, target_slot),
                _move_step(active, target_slot, bench_slot),
            ]
            action, best_rejected_delta = _evaluate_move_chain(
                snapshot=snapshot,
                data_quality=data_quality,
                base_projection=base_projection,
                rows=rows,
                chain=chain,
                move_shape="direct_swap",
                min_points_delta=min_points_delta,
                best_rejected_delta=best_rejected_delta,
            )
            if action:
                key = _chain_key(action["chain"])
                if key not in seen:
                    seen.add(key)
                    actions.append(action)

    for bench in bench_rows:
        bench_slot = _slot(bench) or "BN"
        for target in active_rows:
            target_slot = _slot(target)
            if not target_slot or not _can_play_slot(bench, target_slot):
                continue
            for bridge in active_rows:
                bridge_slot = _slot(bridge)
                if not bridge_slot or _player_id(bridge) == _player_id(target):
                    continue
                if bridge_slot == target_slot:
                    continue
                if not _can_play_slot(target, bridge_slot):
                    continue
                chain = [
                    _move_step(target, target_slot, bridge_slot),
                    _move_step(bench, bench_slot, target_slot),
                    _move_step(bridge, bridge_slot, bench_slot),
                ]
                action, best_rejected_delta = _evaluate_move_chain(
                    snapshot=snapshot,
                    data_quality=data_quality,
                    base_projection=base_projection,
                    rows=rows,
                    chain=chain,
                    move_shape="freeing_up_swap",
                    min_points_delta=min_points_delta,
                    best_rejected_delta=best_rejected_delta,
                )
                if action:
                    key = _chain_key(action["chain"])
                    if key not in seen:
                        seen.add(key)
                        actions.append(action)

    actions.sort(key=lambda action: (action["points_delta"], action["win_probability_delta"]), reverse=True)
    actions = actions[: max(0, limit)]
    if actions:
        return {
            "model_version": MODEL_VERSION,
            "base_projection": base_projection,
            "actions": actions,
            "no_action": None,
        }
    return _no_action_result(
        base_projection=base_projection,
        reason="No legal bench-to-active move improves projected points from the current snapshot.",
        best_rejected_delta=best_rejected_delta,
    )


def _evaluate_move_chain(
    *,
    snapshot: dict[str, Any],
    data_quality: dict[str, Any] | None,
    base_projection: dict[str, Any],
    rows: list[dict[str, Any]],
    chain: list[dict[str, Any]],
    move_shape: str,
    min_points_delta: float,
    best_rejected_delta: float | None,
) -> tuple[dict[str, Any] | None, float | None]:
    moved_rows = _apply_chain(rows, chain)
    moved_snapshot = _with_roster_rows(snapshot, moved_rows)
    moved_projection = compute_projection(moved_snapshot, data_quality)
    if not moved_projection:
        return None, best_rejected_delta

    base_points = _number(base_projection.get("projected_my"))
    moved_points = _number(moved_projection.get("projected_my"))
    base_win = _number(base_projection.get("win_probability"))
    moved_win = _number(moved_projection.get("win_probability"))
    if base_points is None or moved_points is None or base_win is None or moved_win is None:
        return None, best_rejected_delta

    points_delta = round(moved_points - base_points, 1)
    win_probability_delta = round(moved_win - base_win, 4)
    if best_rejected_delta is None or points_delta > best_rejected_delta:
        best_rejected_delta = points_delta
    if points_delta <= min_points_delta or win_probability_delta < 0:
        return None, best_rejected_delta

    return {
        "move_type": "lineup_swap",
        "move_shape": move_shape,
        "chain": chain,
        "points_delta": points_delta,
        "win_probability_delta": win_probability_delta,
        "new_win_probability": round(moved_win, 4),
        "new_projected_my": moved_projection.get("projected_my"),
        "new_projected_opp": moved_projection.get("projected_opp"),
        "reason_chips": _reason_chips(chain, rows, snapshot, move_shape),
    }, best_rejected_delta


def _apply_chain(rows: list[dict[str, Any]], chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [dict(row) for row in rows]
    by_id = {_player_id(row): row for row in out if _player_id(row)}
    for step in chain:
        row = by_id.get(str(step.get("player_id") or ""))
        if row is not None:
            row["slot"] = step.get("to_slot")
    return out


def _with_roster_rows(snapshot: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    return {
        **snapshot,
        "roster": {
            **roster,
            "rows": rows,
        },
    }


def _move_step(row: dict[str, Any], from_slot: str, to_slot: str) -> dict[str, Any]:
    return {
        "player_id": _player_id(row),
        "player_name": row.get("name"),
        "from_slot": from_slot,
        "to_slot": to_slot,
    }


def _reason_chips(
    chain: list[dict[str, Any]],
    before_rows: list[dict[str, Any]],
    snapshot: dict[str, Any],
    move_shape: str,
) -> list[str]:
    before_by_id = {_player_id(row): row for row in before_rows if _player_id(row)}
    promoted = next((step for step in chain if _is_bench_slot(step.get("from_slot")) and not _is_bench_slot(step.get("to_slot"))), None)
    demoted = next((step for step in chain if not _is_bench_slot(step.get("from_slot")) and _is_bench_slot(step.get("to_slot"))), None)
    chips: list[str] = []
    if promoted and demoted:
        promoted_before = before_by_id.get(str(promoted.get("player_id")))
        demoted_before = before_by_id.get(str(demoted.get("player_id")))
        matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
        period_end = _parse_date(matchup.get("end"))
        if period_end and promoted_before and demoted_before:
            promoted_games = _games_remaining(promoted_before, period_end)
            demoted_games = _games_remaining(demoted_before, period_end)
            if promoted_games > demoted_games:
                chips.append("more remaining games")
        if promoted_before and demoted_before and _row_fppg(promoted_before) > _row_fppg(demoted_before):
            chips.append("higher FP/G")
    if move_shape == "freeing_up_swap":
        first = chain[0]
        chips.append(f"legal {first.get('from_slot')}/{first.get('to_slot')} chain")
    elif promoted:
        chips.append(f"legal {promoted.get('to_slot')} swap")
    return chips or ["legal lineup move"]


def _indexed_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not _player_id(row):
        return None
    return row


def _is_active_lineup_row(row: dict[str, Any]) -> bool:
    slot = _slot(row)
    return isinstance(row, dict) and bool(slot) and slot not in INACTIVE_SLOTS and not _is_unavailable(row)


def _is_bench_row(row: dict[str, Any]) -> bool:
    return isinstance(row, dict) and _is_bench_slot(_slot(row)) and not _is_unavailable(row)


def _is_bench_slot(value: Any) -> bool:
    return str(value or "").strip().upper() in BENCH_SLOTS


def _slot(row: dict[str, Any] | None) -> str | None:
    if not isinstance(row, dict):
        return None
    value = str(row.get("slot") or "").strip().upper()
    return value or None


def _player_id(row: dict[str, Any]) -> str | None:
    value = row.get("id") or row.get("player_id")
    return str(value) if value not in (None, "") else None


def _can_play_slot(row: dict[str, Any], slot: str) -> bool:
    if not slot or _is_bench_slot(slot):
        return True
    tokens = _eligibility_tokens(row)
    if not tokens:
        return False
    slot = POSITION_ALIASES.get(str(slot).strip().upper(), str(slot).strip().upper())
    if slot in tokens:
        return True
    allowed = SLOT_COMPATIBILITY.get(slot)
    return bool(allowed and tokens & {POSITION_ALIASES.get(token, token) for token in allowed})


def _eligibility_tokens(row: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    for source in (row, player, raw):
        if not isinstance(source, dict):
            continue
        for key in ("all_positions", "positions", "multi_positions", "pos"):
            value = source.get(key)
            if value:
                values.append(value)

    tokens: set[str] = set()
    for value in values:
        if isinstance(value, list):
            parts = value
        else:
            parts = str(value).replace("/", ",").replace(" ", ",").split(",")
        for raw_value in parts:
            token = POSITION_ALIASES.get(str(raw_value or "").strip().upper(), str(raw_value or "").strip().upper())
            if token and token not in GENERIC_POSITIONS:
                tokens.add(token)
    return tokens


def _chain_key(chain: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            str(step.get("player_id") or ""),
            str(step.get("from_slot") or ""),
            str(step.get("to_slot") or ""),
        )
        for step in chain
    )


def _recommendation_quality(
    snapshot: dict[str, Any],
    data_quality: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if data_quality is not None:
        return data_quality
    try:
        import sandlot_data_quality

        return sandlot_data_quality.snapshot_data_quality(snapshot)
    except Exception:
        return None


def _quality_reason(data_quality: dict[str, Any]) -> str:
    try:
        import sandlot_data_quality

        return sandlot_data_quality.short_reason(data_quality, purpose="recommendation")
    except Exception:
        reasons = data_quality.get("recommendation_reasons") or data_quality.get("reasons") or []
        return str(reasons[0]) if reasons else "Required snapshot data is incomplete"


def _no_action_result(
    *,
    base_projection: dict[str, Any] | None,
    reason: str,
    best_rejected_delta: float | None = None,
) -> dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "base_projection": base_projection,
        "actions": [],
        "no_action": {
            "reason": reason,
            "best_rejected_delta": round(best_rejected_delta, 1) if best_rejected_delta is not None else None,
        },
    }


def _active_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("slot") or "").strip().upper() not in INACTIVE_SLOTS
    ]


def _games_remaining(row: dict[str, Any], period_end: date) -> int:
    if _is_unavailable(row):
        return 0

    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    future_games = raw.get("future_games") or row.get("future_games") or {}
    if isinstance(future_games, dict):
        games = future_games.values()
    elif isinstance(future_games, list):
        games = future_games
    else:
        return 0

    count = 0
    for game in games:
        if not isinstance(game, dict):
            continue
        game_date = _parse_date(game.get("date"))
        if game_date is not None and game_date <= period_end:
            count += 1
    return count


def _team_projection(
    rows: list[dict[str, Any]],
    current_score: float,
    period_end: date,
) -> tuple[float, float, int]:
    mean_delta = 0.0
    variance = 0.0
    games_remaining = 0
    for row in _active_rows(rows):
        games = _games_remaining(row, period_end)
        fppg = _row_fppg(row)
        delta = fppg * games
        mean_delta += delta
        # Fantasy FP/G can be negative; the projection mean should keep that,
        # but the Poisson-style uncertainty term must remain non-negative.
        variance += max(0.0, delta)
        games_remaining += games
    return current_score + mean_delta, variance, games_remaining


def _win_prob(mu_my: float, var_my: float, mu_opp: float, var_opp: float) -> float:
    total_var = var_my + var_opp
    if total_var <= 0:
        return _deterministic_prob(mu_my - mu_opp)

    z = (mu_my - mu_opp) / math.sqrt(total_var)
    return max(0.0, min(1.0, 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))))


def _drivers(
    *,
    my_score: float,
    opp_score: float,
    projected_my: float,
    projected_opp: float,
    my_games: int,
    opp_games: int,
    win_probability: float,
    data_quality: dict[str, Any] | None,
) -> dict[str, Any]:
    current_margin = round(my_score - opp_score, 1)
    projected_margin = round(projected_my - projected_opp, 1)
    rest_of_period_delta = round(projected_margin - current_margin, 1)
    game_volume_edge = my_games - opp_games
    risk_level = _risk_level(
        win_probability=win_probability,
        projected_margin=projected_margin,
        remaining_games=my_games + opp_games,
        data_quality=data_quality,
    )
    return {
        "current_margin": current_margin,
        "projected_margin": projected_margin,
        "rest_of_period_delta": rest_of_period_delta,
        "game_volume_edge": game_volume_edge,
        "risk_level": risk_level,
        "summary": _driver_summary(current_margin, projected_margin, rest_of_period_delta, game_volume_edge),
    }


def _risk_level(
    *,
    win_probability: float,
    projected_margin: float,
    remaining_games: int,
    data_quality: dict[str, Any] | None,
) -> str:
    score = 0
    probability_edge = abs(win_probability - 0.5)
    margin_abs = abs(projected_margin)

    if probability_edge < 0.10:
        score += 2
    elif probability_edge < 0.20:
        score += 1

    if margin_abs < 5:
        score += 2
    elif margin_abs < 15:
        score += 1

    if remaining_games >= 16:
        score += 2
    elif remaining_games >= 8:
        score += 1

    if isinstance(data_quality, dict) and not data_quality.get("projection_ready", True):
        score += 2

    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _driver_summary(
    current_margin: float,
    projected_margin: float,
    rest_of_period_delta: float,
    game_volume_edge: int,
) -> str:
    current = _margin_phrase(current_margin, "now")
    projected = _margin_phrase(projected_margin, "projected")
    swing = _signed(rest_of_period_delta)
    if game_volume_edge > 0:
        volume = f"You have {game_volume_edge:g} more remaining game{'s' if game_volume_edge != 1 else ''}."
    elif game_volume_edge < 0:
        volume = f"The opponent has {abs(game_volume_edge):g} more remaining game{'s' if game_volume_edge != -1 else ''}."
    else:
        volume = "Remaining game volume is even."
    return f"{current}; {projected}. Rest-of-period swing is {swing} points. {volume}"


def _margin_phrase(margin: float, label: str) -> str:
    if margin > 0:
        return f"You lead {label} by {abs(margin):g}"
    if margin < 0:
        return f"You trail {label} by {abs(margin):g}"
    return f"You are tied {label}"


def _signed(value: float) -> str:
    return f"+{value:g}" if value > 0 else f"{value:g}"


def _opponent_rows(snapshot: dict[str, Any], matchup: dict[str, Any]) -> list[dict[str, Any]] | None:
    opponent_id = matchup.get("opponent_team_id")
    all_rosters = snapshot.get("all_team_rosters")
    if not isinstance(all_rosters, dict):
        return None

    if opponent_id:
        opponent = all_rosters.get(str(opponent_id))
        if isinstance(opponent, dict) and isinstance(opponent.get("rows"), list):
            return opponent["rows"]

    opponent_name = str(matchup.get("opponent_team_name") or "").strip().casefold()
    if opponent_name:
        for team in all_rosters.values():
            if not isinstance(team, dict):
                continue
            names = {
                str(team.get("team_name") or "").strip().casefold(),
                str(team.get("team_short") or "").strip().casefold(),
            }
            if opponent_name in names and isinstance(team.get("rows"), list):
                return team["rows"]
    return None


def _matchup_key(snapshot: dict[str, Any], matchup: dict[str, Any]) -> str:
    return ":".join(
        part
        for part in (
            _text(snapshot.get("league_id")),
            _period_id(matchup),
            _text(matchup.get("my_team_id") or snapshot.get("team_id")),
            _text(matchup.get("opponent_team_id") or matchup.get("opponent_team_name")),
        )
        if part
    ) or "unknown-matchup"


def _period_id(matchup: dict[str, Any]) -> str:
    for key in ("period_id", "period_number", "period_name", "week", "start", "end"):
        value = _text(matchup.get(key))
        if value:
            return value
    return "unknown-period"


def _row_fppg(row: dict[str, Any]) -> float:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    value = _number(row.get("fppg"))
    if value is None:
        value = _number(raw.get("fantasy_points_per_game"))
    return value or 0.0


def _is_unavailable(row: dict[str, Any]) -> bool:
    status = str(row.get("injury") or row.get("status") or "").strip().upper()
    if status in UNAVAILABLE_INJURIES:
        return True
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    return bool(player.get("out") or player.get("injured_reserve"))


def _deterministic_prob(margin: float) -> float:
    if margin > 0:
        return 1.0
    if margin < 0:
        return 0.0
    return 0.5


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
