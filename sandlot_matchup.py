"""Deterministic matchup projection helpers for Sandlot snapshots."""

from __future__ import annotations

import math
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

import sandlot_future_games


INACTIVE_SLOTS = {"BN", "IL", "IR", "RES", "RESERVE", "BE", "BENCH", "MIN", "MINORS"}
BENCH_SLOTS = {"BN", "BE", "BENCH", "RES", "RESERVE"}
PROTECTED_LINEUP_SLOTS = {"IL", "IR", "MIN", "MINORS"}
PROTECTED_PLAYER_FLAGS = {
    "protected",
    "is_protected",
    "keeper_protected",
    "minor_league",
    "minors",
    "is_minor_leaguer",
}
UNAVAILABLE_INJURIES = {"OUT", "IL", "IL10", "IL60", "IR"}
MODEL_VERSION = "matchup_projection_v3"
MIN_MEANINGFUL_POINTS_DELTA = 1.0
MIN_MEANINGFUL_WIN_PROBABILITY_DELTA = 0.01
HITTER_POSITIONS = {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF"}
PITCHER_POSITIONS = {"P", "SP", "RP"}
POSITION_ALIASES = {
    "LF": "OF",
    "CF": "OF",
    "RF": "OF",
    "STARTING": "SP",
    "RELIEF": "RP",
}
GENERIC_POSITIONS = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "MIN", "MINORS", "HIT", "PIT", "ALL"}
SLOT_COMPATIBILITY = {
    "UTIL": HITTER_POSITIONS,
    "MI": {"2B", "SS"},
    "CI": {"1B", "3B"},
    "P": PITCHER_POSITIONS,
    "OF": {"OF", "LF", "CF", "RF"},
}
PITCHER_APPEARANCE_FLAGS = {
    "confirmed_start",
    "confirmedStart",
    "expected_start",
    "expectedStart",
    "is_probable_starter",
    "isProbableStarter",
    "is_starting_pitcher",
    "isStartingPitcher",
    "probable_start",
    "probableStart",
    "projected_start",
    "projectedStart",
    "relief_appearance",
    "reliefAppearance",
    "scheduled_appearance",
    "scheduledAppearance",
    "scheduled_start",
    "scheduledStart",
}
PITCHER_APPEARANCE_FIELDS = {
    "pitcher",
    "pitcher_id",
    "pitcherId",
    "probable_pitcher",
    "probablePitcher",
    "scheduled_starter",
    "scheduledStarter",
    "starter",
    "starting_pitcher",
    "startingPitcher",
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
    period_start = _parse_date(matchup.get("start"))

    roster = snapshot.get("roster") or {}
    my_rows = roster.get("rows") if isinstance(roster, dict) else None
    opp_rows = _opponent_rows(snapshot, matchup)
    if not isinstance(my_rows, list) or not isinstance(opp_rows, list):
        return None

    mu_my, var_my, my_games = _team_projection(my_rows, my_score, period_end, period_start)
    mu_opp, var_opp, opp_games = _team_projection(opp_rows, opp_score, period_end, period_start)
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
        "drivers": projection.get("drivers") or {},
    }


def actual_result_payload(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    if not matchup or not matchup.get("complete"):
        return None
    actual_my = _number(matchup.get("my_score"))
    actual_opp = _number(matchup.get("opponent_score"))
    if actual_my is None or actual_opp is None:
        return None
    return {
        "matchup_key": _matchup_key(snapshot, matchup),
        "period_id": _period_id(matchup),
        "actual_my": actual_my,
        "actual_opp": actual_opp,
        "actual_winner": _actual_winner(snapshot, matchup, actual_my, actual_opp),
    }


def calibration_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows or []:
        if not _has_evaluation_fields(row):
            continue
        model_version = str(row.get("model_version") or "unknown")
        surface = str(row.get("surface") or "unknown")
        groups.setdefault((model_version, surface), []).append(row)

    return {
        "minimum_actual_fields": [
            "actual_my",
            "actual_opp",
            "actual_winner",
            "predicted_my",
            "predicted_opp",
            "win_probability",
            "model_version",
        ],
        "sample_size": sum(len(values) for values in groups.values()),
        "groups": [
            _calibration_group(model_version, surface, values)
            for (model_version, surface), values in sorted(groups.items())
        ],
    }


def _calibration_group(model_version: str, surface: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    score_errors: list[float] = []
    margin_errors: list[float] = []
    margin_abs_errors: list[float] = []
    brier_scores: list[float] = []
    game_edge_errors: dict[str, list[float]] = {"positive": [], "negative": [], "even": []}

    for row in rows:
        predicted_my = _number(row.get("predicted_my")) or 0.0
        predicted_opp = _number(row.get("predicted_opp")) or 0.0
        predicted_margin = _number(row.get("predicted_margin"))
        if predicted_margin is None:
            predicted_margin = predicted_my - predicted_opp
        actual_my = _number(row.get("actual_my")) or 0.0
        actual_opp = _number(row.get("actual_opp")) or 0.0
        actual_margin = actual_my - actual_opp
        margin_error = predicted_margin - actual_margin
        probability = _number(row.get("win_probability")) or 0.0
        outcome = _actual_probability_outcome(actual_my, actual_opp)

        score_errors.append((abs(predicted_my - actual_my) + abs(predicted_opp - actual_opp)) / 2.0)
        margin_errors.append(margin_error)
        margin_abs_errors.append(abs(margin_error))
        brier_scores.append((probability - outcome) ** 2)

        drivers = row.get("drivers") if isinstance(row.get("drivers"), dict) else {}
        edge = _number(drivers.get("game_volume_edge")) or 0.0
        if edge > 0:
            game_edge_errors["positive"].append(margin_error)
        elif edge < 0:
            game_edge_errors["negative"].append(margin_error)
        else:
            game_edge_errors["even"].append(margin_error)

    metrics = {
        "score_mae": _mean(score_errors),
        "margin_mae": _mean(margin_abs_errors),
        "margin_bias": _mean(margin_errors),
        "brier_score": _mean(brier_scores),
        "game_volume_bias": {
            key: _mean(values)
            for key, values in game_edge_errors.items()
            if values
        },
    }
    return {
        "model_version": model_version,
        "surface": surface,
        "count": len(rows),
        "metrics": metrics,
        "flags": _calibration_flags(len(rows), metrics),
    }


def _has_evaluation_fields(row: dict[str, Any]) -> bool:
    required = ("actual_my", "actual_opp", "actual_winner", "predicted_my", "predicted_opp", "win_probability")
    return all(row.get(key) is not None for key in required)


def _actual_winner(snapshot: dict[str, Any], matchup: dict[str, Any], actual_my: float, actual_opp: float) -> str:
    if actual_my > actual_opp:
        return _text(matchup.get("my_team_id") or snapshot.get("team_id")) or "me"
    if actual_opp > actual_my:
        return _text(matchup.get("opponent_team_id") or matchup.get("opponent_team_name")) or "opponent"
    return "tie"


def _actual_probability_outcome(actual_my: float, actual_opp: float) -> float:
    if actual_my > actual_opp:
        return 1.0
    if actual_opp > actual_my:
        return 0.0
    return 0.5


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _calibration_flags(count: int, metrics: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if count < 3:
        flags.append("insufficient_sample")
    margin_bias = metrics.get("margin_bias")
    if isinstance(margin_bias, (int, float)) and abs(margin_bias) >= 5:
        flags.append("positive_margin_bias" if margin_bias > 0 else "negative_margin_bias")
    volume_bias = metrics.get("game_volume_bias") if isinstance(metrics.get("game_volume_bias"), dict) else {}
    positive_edge_bias = volume_bias.get("positive")
    negative_edge_bias = volume_bias.get("negative")
    if isinstance(positive_edge_bias, (int, float)) and positive_edge_bias >= 3:
        flags.append("game_volume_edge_may_be_overrated")
    if isinstance(negative_edge_bias, (int, float)) and negative_edge_bias <= -3:
        flags.append("opponent_game_volume_edge_may_be_overrated")
    return flags


def simulate_lineup_move_impact(
    snapshot: dict[str, Any],
    data_quality: dict[str, Any] | None = None,
    *,
    limit: int = 5,
    min_points_delta: float = 0.0,
) -> dict[str, Any]:
    """Compare legal bench-to-active lineup moves against the base projection."""
    data_quality = _recommendation_quality(snapshot, data_quality)
    preflight_reason = _lineup_simulation_preflight_reason(data_quality)
    if preflight_reason:
        return _no_action_result(
            base_projection=compute_projection(snapshot, data_quality),
            reason="Recommendation data incomplete: " + preflight_reason,
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
    blocked_reasons: list[str] = []
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
                blocked_reasons=blocked_reasons,
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
                    blocked_reasons=blocked_reasons,
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
    reason = "No legal bench-to-active move improves projected points from the current snapshot."
    if blocked_reasons and best_rejected_delta is None:
        reason = "No safe lineup move is available: " + blocked_reasons[0]
    return _no_action_result(
        base_projection=base_projection,
        reason=reason,
        best_rejected_delta=best_rejected_delta,
    )


def rank_matchup_improvement_actions(
    snapshot: dict[str, Any],
    data_quality: dict[str, Any] | None = None,
    *,
    limit: int = 3,
    min_points_delta: float = MIN_MEANINGFUL_POINTS_DELTA,
    min_win_probability_delta: float = MIN_MEANINGFUL_WIN_PROBABILITY_DELTA,
) -> dict[str, Any]:
    """Rank simulated lineup moves and suppress noise below deterministic thresholds."""
    simulation = simulate_lineup_move_impact(
        snapshot,
        data_quality,
        limit=max(limit * 4, 10),
        min_points_delta=0.0,
    )
    thresholds = {
        "points_delta": min_points_delta,
        "win_probability_delta": min_win_probability_delta,
    }
    recommendations: list[dict[str, Any]] = []
    best_rejected_delta = (simulation.get("no_action") or {}).get("best_rejected_delta")

    for action in simulation.get("actions") or []:
        points_delta = _number(action.get("points_delta")) or 0.0
        win_delta = _number(action.get("win_probability_delta")) or 0.0
        if not _clears_meaningful_threshold(
            points_delta,
            win_delta,
            min_points_delta=min_points_delta,
            min_win_probability_delta=min_win_probability_delta,
        ):
            if best_rejected_delta is None or points_delta > best_rejected_delta:
                best_rejected_delta = points_delta
            continue
        confidence = _recommendation_confidence(points_delta, win_delta)
        risk_label = _recommendation_risk(action.get("new_win_probability"))
        replacement_card = _lineup_replacement_card(
            action=action,
            snapshot=snapshot,
            base_projection=simulation.get("base_projection"),
            confidence=confidence,
            risk_label=risk_label,
        )
        if replacement_card is None:
            continue
        recommendations.append({
            "rank": len(recommendations) + 1,
            "action": {
                "move_type": action.get("move_type"),
                "move_shape": action.get("move_shape"),
                "chain": action.get("chain") or [],
            },
            "replacement_card": replacement_card,
            "points_delta": action.get("points_delta"),
            "win_probability_delta": action.get("win_probability_delta"),
            "new_win_probability": action.get("new_win_probability"),
            "confidence": confidence,
            "risk_label": risk_label,
            "reason_chips": action.get("reason_chips") or [],
        })
        if len(recommendations) >= max(0, limit):
            break

    if recommendations:
        return {
            "model_version": MODEL_VERSION,
            "base_projection": simulation.get("base_projection"),
            "thresholds": thresholds,
            "recommendations": recommendations,
            "no_action": None,
        }

    no_action = simulation.get("no_action") or {}
    reason = no_action.get("reason")
    if not reason or str(reason).startswith("No legal bench-to-active move"):
        reason = "No lineup move clears the meaningful-gain threshold from this snapshot."
    return {
        "model_version": MODEL_VERSION,
        "base_projection": simulation.get("base_projection"),
        "thresholds": thresholds,
        "recommendations": [],
        "no_action": {
            "reason": reason,
            "best_rejected_delta": round(best_rejected_delta, 1) if best_rejected_delta is not None else None,
            "threshold": min_points_delta,
            "win_probability_threshold": min_win_probability_delta,
        },
    }


def _clears_meaningful_threshold(
    points_delta: float,
    win_probability_delta: float,
    *,
    min_points_delta: float,
    min_win_probability_delta: float,
) -> bool:
    if points_delta <= 0 or win_probability_delta < 0:
        return False
    return points_delta >= min_points_delta and win_probability_delta >= min_win_probability_delta


def _recommendation_confidence(points_delta: float, win_probability_delta: float) -> str:
    if win_probability_delta >= 0.05 or points_delta >= 5:
        return "high"
    if win_probability_delta >= 0.02 or points_delta >= 2:
        return "medium"
    return "light"


def _recommendation_risk(new_win_probability: Any) -> str:
    probability = _number(new_win_probability)
    if probability is None:
        return "unknown"
    edge = abs(probability - 0.5)
    if edge < 0.08:
        return "high"
    if edge < 0.18:
        return "medium"
    return "low"


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
    blocked_reasons: list[str],
) -> tuple[dict[str, Any] | None, float | None]:
    participant_blocker = _participant_blocker(chain, rows, snapshot)
    if participant_blocker:
        if participant_blocker not in blocked_reasons and len(blocked_reasons) < 5:
            blocked_reasons.append(participant_blocker)
        return None, best_rejected_delta

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
        period_start = _parse_date(matchup.get("start"))
        period_end = _parse_date(matchup.get("end"))
        if period_end and promoted_before and demoted_before:
            promoted_games = _games_remaining(promoted_before, period_end, period_start)
            demoted_games = _games_remaining(demoted_before, period_end, period_start)
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


def _lineup_replacement_card(
    *,
    action: dict[str, Any],
    snapshot: dict[str, Any],
    base_projection: dict[str, Any] | None,
    confidence: str,
    risk_label: str,
) -> dict[str, Any] | None:
    chain = action.get("chain") if isinstance(action.get("chain"), list) else []
    promoted = next((step for step in chain if _is_bench_slot(step.get("from_slot")) and not _is_bench_slot(step.get("to_slot"))), None)
    demoted = next((step for step in chain if not _is_bench_slot(step.get("from_slot")) and _is_bench_slot(step.get("to_slot"))), None)
    if not promoted or not demoted:
        return None

    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    rows = roster.get("rows") if isinstance(roster, dict) else []
    by_id = {_player_id(row): row for row in rows if isinstance(row, dict) and _player_id(row)}
    move_in_row = by_id.get(str(promoted.get("player_id") or ""))
    move_out_row = by_id.get(str(demoted.get("player_id") or ""))
    if not move_in_row or not move_out_row:
        return None
    if _is_protected_lineup_row(move_in_row) or _is_protected_lineup_row(move_out_row):
        return None

    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
    period_start = _parse_date(matchup.get("start"))
    period_end = _parse_date(matchup.get("end"))
    movability = _lineup_movability(move_in_row, move_out_row, now=_movability_now(snapshot))
    move_in = _player_card_summary(move_in_row, promoted, period_end, period_start)
    move_out = _player_card_summary(move_out_row, demoted, period_end, period_start)
    points_delta = _number(action.get("points_delta")) or 0.0
    win_delta = _number(action.get("win_probability_delta")) or 0.0
    base_win = _number((base_projection or {}).get("win_probability"))
    new_win = _number(action.get("new_win_probability"))
    chips = action.get("reason_chips") if isinstance(action.get("reason_chips"), list) else []
    reason_chip_text = ", ".join(str(chip) for chip in chips[:2] if chip) or "lineup simulation edge"
    execution = _lineup_execution_state(movability)

    return {
        "type": "lineup_hot_swap",
        "proposal": _lineup_swap_proposal(
            move_in,
            move_out,
            movability,
            snapshot=snapshot,
            chain=chain,
            points_delta=points_delta,
            win_delta=win_delta,
        ),
        "move_in": move_in,
        "move_out": move_out,
        "movability": movability,
        "projected_benefit": {
            "points": round(points_delta, 1),
            "win_probability_delta": round(win_delta, 4),
            "base_win_probability": round(base_win, 4) if base_win is not None else None,
            "new_win_probability": round(new_win, 4) if new_win is not None else None,
            "new_projected_my": action.get("new_projected_my"),
            "new_projected_opp": action.get("new_projected_opp"),
        },
        "reason": (
            f"Move {move_in['name']} into {move_in['to_slot']} and {move_out['name']} "
            f"to {move_out['to_slot']} because the lineup-only simulation sees {reason_chip_text}."
        ),
        "short_term_outlook": _short_term_outlook(move_in, move_out),
        "risk": (
            f"{risk_label.title()} risk: this is a lineup-only projection. "
            "Confirm Fantrax lock status, actual starts, and late scratches before acting."
        ),
        "confidence": confidence,
        "risk_label": risk_label,
        "provenance": {
            "source": "latest Fantrax snapshot",
            "model_version": MODEL_VERSION,
            "slot_provenance": "trusted",
            "move_in_slot_source": move_in.get("slot_source"),
            "move_out_slot_source": move_out.get("slot_source"),
            "movability_source": movability.get("source"),
            "scoring": "snapshot FP/G and remaining-games projection",
        },
        "safety": {
            "lineup_only": True,
            "add_drop": False,
            "live_writes": False,
            "protected_players_excluded": True,
            "movability": movability.get("state"),
        },
        "execution": execution,
        "blocked_reason": execution["reason"],
    }


def _lineup_swap_proposal(
    move_in: dict[str, Any],
    move_out: dict[str, Any],
    movability: dict[str, Any] | None = None,
    *,
    snapshot: dict[str, Any] | None = None,
    chain: list[dict[str, Any]] | None = None,
    points_delta: float = 0.0,
    win_delta: float = 0.0,
) -> dict[str, Any]:
    proposal_id = "lineup-swap:{out_id}:{in_id}:{slot}".format(
        out_id=move_out.get("id") or "unknown-out",
        in_id=move_in.get("id") or "unknown-in",
        slot=move_in.get("to_slot") or "slot",
    )
    return {
        "id": proposal_id,
        "type": "lineup_swap",
        "status": "blocked",
        "executable": False,
        "writes_enabled": False,
        "confirmation_required": True,
        "summary": f"Move {move_out.get('name', 'OUT player')} out and {move_in.get('name', 'IN player')} in.",
        "contract": _lineup_swap_contract(
            proposal_id=proposal_id,
            move_in=move_in,
            move_out=move_out,
            movability=movability,
            snapshot=snapshot or {},
            chain=chain or [],
            points_delta=points_delta,
            win_delta=win_delta,
        ),
        "safety_checks": [
            {
                "key": "trusted_slots",
                "label": "Trusted slot data",
                "state": "passed",
                "detail": "Recommendation is only emitted after lineup slot provenance is trusted.",
            },
            {
                "key": "lineup_only",
                "label": "Lineup-only move",
                "state": "passed",
                "detail": "No add, drop, trade, or roster-pool mutation is attached to this proposal.",
            },
            {
                "key": "protected_players",
                "label": "Protected players excluded",
                "state": "passed",
                "detail": "Minors, IL/IR, and other protected rows are not eligible swap targets.",
            },
            _lineup_movability_safety_check(movability),
            {
                "key": "executor_ready",
                "label": "Execution safety",
                "state": "blocked",
                "detail": "Fantrax write execution still needs a separate confirmed executor contract.",
            },
        ],
    }


def _lineup_movability(
    move_in_row: dict[str, Any],
    move_out_row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    participants = {
        "move_in": _player_movability(move_in_row, now=now),
        "move_out": _player_movability(move_out_row, now=now),
    }
    locked = [item for item in participants.values() if item["state"] == "locked"]
    unknown = [item for item in participants.values() if item["state"] == "unknown"]
    if locked:
        names = _name_list(item.get("name") for item in locked)
        detail = str(locked[0].get("reason") or "one or more participants are unavailable")
        return {
            "state": "locked",
            "label": "Locked",
            "reason": f"Lineup movability is blocked for {names}: {detail}",
            "source": "fantrax.raw.scorer.disableLineupChange+mlb_schedule.gameDate",
            "participants": participants,
        }
    if unknown:
        names = _name_list(item.get("name") for item in unknown)
        detail = str(unknown[0].get("reason") or "movability data is incomplete")
        return {
            "state": "unknown",
            "label": "Movability unknown",
            "reason": f"Lineup movability is uncertain for {names}: {detail}",
            "source": "fantrax.raw.scorer.disableLineupChange+mlb_schedule.gameDate",
            "participants": participants,
        }
    return {
        "state": "movable",
        "label": "Movable",
        "reason": "Fantrax raw data and MLB game-start timing do not mark either participant unavailable.",
        "source": "fantrax.raw.scorer.disableLineupChange+mlb_schedule.gameDate",
        "participants": participants,
    }


def _player_movability(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    name = str(row.get("name") or row.get("id") or "unknown player")
    now = now or datetime.now(timezone.utc)
    provider_value = _raw_disable_lineup_change(row)
    schedule = _schedule_movability(row, now)
    if provider_value is True:
        state = "locked"
        label = "Locked"
        reason = f"{name} is marked unavailable for lineup changes by Fantrax."
    elif schedule["state"] == "locked":
        state = "locked"
        label = "Locked"
        reason = schedule["reason"]
    elif provider_value is False and schedule["state"] == "movable":
        state = "movable"
        label = "Movable"
        reason = f"{name} is not marked unavailable by Fantrax and has no started MLB game in this snapshot."
    else:
        state = "unknown"
        label = "Movability unknown"
        if provider_value is None:
            reason = f"{name} is missing a boolean Fantrax lineup-change flag."
        else:
            reason = schedule["reason"]
    return {
        "id": _player_id(row),
        "name": name,
        "state": state,
        "label": label,
        "reason": reason,
        "source": "fantrax.raw.scorer.disableLineupChange+mlb_schedule.gameDate",
        "provider": {
            "source": "fantrax.raw.scorer.disableLineupChange",
            "raw_value": provider_value,
        },
        "schedule": schedule,
        "raw_value": provider_value,
    }


def _raw_disable_lineup_change(row: dict[str, Any]) -> bool | None:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    scorer = raw.get("scorer") if isinstance(raw.get("scorer"), dict) else {}
    value = scorer.get("disableLineupChange")
    return value if isinstance(value, bool) else None


def _lineup_swap_contract(
    *,
    proposal_id: str,
    move_in: dict[str, Any],
    move_out: dict[str, Any],
    movability: dict[str, Any] | None,
    snapshot: dict[str, Any],
    chain: list[dict[str, Any]],
    points_delta: float,
    win_delta: float,
) -> dict[str, Any]:
    movability_state = str((movability or {}).get("state") or "unknown")
    blocked_by = ["executor_ready"]
    if movability_state != "movable":
        blocked_by.insert(0, "fantrax_movability")
    slot_moves = _contract_slot_moves(chain)

    contract = {
        "version": 1,
        "proposal_id": proposal_id,
        "type": "lineup_swap",
        "executable": False,
        "writes_enabled": False,
        "action": "change_slot",
        "snapshot_id": snapshot.get("snapshot_id") or snapshot.get("id"),
        "league_id": snapshot.get("league_id"),
        "team_id": snapshot.get("team_id"),
        "move_out": _contract_player(move_out),
        "move_in": _contract_player(move_in),
        "target_slot": move_in.get("to_slot"),
        "fallback_slot": move_out.get("to_slot"),
        "slot_moves": slot_moves,
        "requires_multi_step": len(slot_moves) > 2,
        "projected_benefit": {
            "points": round(points_delta, 1),
            "win_probability_delta": round(win_delta, 4),
        },
        "movability": {
            "state": movability_state,
            "source": (movability or {}).get("source"),
            "reason": (movability or {}).get("reason"),
        },
        "blocked_by": blocked_by,
        "confirmation_copy": (
            f"Confirm lineup-only swap: move {move_out.get('name', 'OUT player')} "
            f"from {move_out.get('from_slot') or '?'} to {move_out.get('to_slot') or '?'} "
            f"and move {move_in.get('name', 'IN player')} from {move_in.get('from_slot') or '?'} "
            f"to {move_in.get('to_slot') or '?'}."
        ),
    }
    contract["input_hash"] = _contract_input_hash(contract)
    return contract


def _contract_slot_moves(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for index, step in enumerate(chain, start=1):
        if not isinstance(step, dict):
            continue
        moves.append({
            "order": index,
            "player_id": step.get("player_id"),
            "player_name": step.get("player_name"),
            "from_slot": step.get("from_slot"),
            "to_slot": step.get("to_slot"),
        })
    return moves


def _contract_player(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": player.get("id"),
        "name": player.get("name"),
        "team": player.get("team"),
        "positions": player.get("positions"),
        "from_slot": player.get("from_slot"),
        "to_slot": player.get("to_slot"),
        "fppg": player.get("fppg"),
        "remaining_games": player.get("remaining_games"),
        "slot_source": player.get("slot_source"),
    }


def _contract_input_hash(contract: dict[str, Any]) -> str:
    payload = {key: value for key, value in contract.items() if key != "input_hash"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _movability_now(snapshot: dict[str, Any]) -> datetime:
    for key in ("movability_now", "_movability_now", "_now"):
        parsed = _parse_game_start(snapshot.get(key))
        if parsed:
            return parsed
    return datetime.now(timezone.utc)


def _schedule_movability(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    games = list(_iter_lock_games(row))
    if not games:
        return {
            "state": "movable",
            "source": "mlb_schedule.gameDate",
            "reason": f"{row.get('name') or row.get('id') or 'player'} has no scheduled game rows that block movement.",
            "game": None,
        }

    unknown_games: list[dict[str, Any]] = []
    for game in games:
        if not isinstance(game, dict):
            continue
        starts_at = _parse_game_start(_game_start_value(game))
        if starts_at:
            if starts_at <= now:
                return {
                    "state": "locked",
                    "source": "mlb_schedule.gameDate",
                    "reason": f"MLB schedule shows {_game_label(game)} already started.",
                    "game": game,
                    "started_at": starts_at.isoformat(),
                }
            continue

        game_date = _parse_date(game.get("date") or game.get("officialDate") or game.get("gameDate"))
        if game_date is None:
            unknown_games.append(game)
            continue
        if game_date < now.date():
            return {
                "state": "locked",
                "source": "mlb_schedule.date",
                "reason": f"MLB schedule shows {_game_label(game)} is from an earlier date.",
                "game": game,
            }
        if game_date == now.date():
            unknown_games.append(game)

    if unknown_games:
        return {
            "state": "unknown",
            "source": "mlb_schedule.gameDate",
            "reason": f"MLB schedule is missing a start time for {_game_label(unknown_games[0])}.",
            "game": unknown_games[0],
        }
    return {
        "state": "movable",
        "source": "mlb_schedule.gameDate",
        "reason": f"{row.get('name') or row.get('id') or 'player'} has no started MLB game in this snapshot.",
        "game": None,
    }


def _iter_lock_games(row: dict[str, Any]):
    seen: set[str] = set()
    for source in (_future_games(row), row.get("team_future_games") if isinstance(row.get("team_future_games"), list) else []):
        for game in source:
            if not isinstance(game, dict):
                continue
            key = str(game.get("game_pk") or game.get("gamePk") or game.get("eventId") or game.get("gameDate") or game.get("date") or id(game))
            if key in seen:
                continue
            seen.add(key)
            yield game


def _game_start_value(game: dict[str, Any]) -> Any:
    for key in ("gameDate", "game_date", "game_datetime", "dateTime", "startTime", "start_time"):
        value = game.get(key)
        if value:
            return value
    return None


def _parse_game_start(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _game_label(game: dict[str, Any]) -> str:
    date_text = _text(game.get("date") or game.get("officialDate") or game.get("gameDate")) or "a scheduled game"
    opponent = _text(game.get("opponent") or game.get("away") or game.get("home"))
    if opponent:
        return f"{date_text} vs {opponent}"
    return date_text


def _lineup_movability_safety_check(movability: dict[str, Any] | None) -> dict[str, Any]:
    state = str((movability or {}).get("state") or "unknown")
    check_state = "passed" if state == "movable" else ("blocked" if state == "locked" else "warning")
    return {
        "key": "fantrax_movability",
        "label": "Fantrax movability",
        "state": check_state,
        "detail": (movability or {}).get("reason") or "Fantrax movability data is unavailable.",
    }


def _lineup_execution_state(movability: dict[str, Any]) -> dict[str, str]:
    state = str(movability.get("state") or "unknown")
    if state == "locked":
        reason = (
            f"{movability.get('reason')} Fantrax write execution also remains disabled until "
            "the lineup executor has separate confirmation safety."
        )
    elif state == "unknown":
        reason = (
            f"{movability.get('reason')} Fantrax write execution remains disabled until "
            "movability is trusted and the lineup executor has separate confirmation safety."
        )
    else:
        reason = (
            "Fantrax raw data does not mark the participants unavailable, but write execution "
            "remains disabled until the lineup executor has separate confirmation safety."
        )
    return {
        "state": "blocked",
        "label": "Propose swap",
        "reason": reason,
    }


def _name_list(values) -> str:
    names = [str(value) for value in values if value]
    if not names:
        return "one or more participants"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _player_card_summary(
    row: dict[str, Any],
    step: dict[str, Any],
    period_end: date | None,
    period_start: date | None = None,
) -> dict[str, Any]:
    games = _games_remaining(row, period_end, period_start) if period_end else None
    return {
        "id": _player_id(row),
        "name": row.get("name") or step.get("player_name") or "Unknown player",
        "team": row.get("team") or "",
        "positions": _positions_label(row),
        "from_slot": step.get("from_slot"),
        "to_slot": step.get("to_slot"),
        "fppg": round(_row_fppg(row), 2),
        "remaining_games": games,
        "slot_source": row.get("slot_source") or "unknown",
    }


def _positions_label(row: dict[str, Any]) -> str:
    positions = row.get("all_positions")
    if isinstance(positions, list) and positions:
        return "/".join(str(position) for position in positions if position) or "UT"
    return str(row.get("positions") or row.get("pos") or "UT")


def _short_term_outlook(move_in: dict[str, Any], move_out: dict[str, Any]) -> str:
    in_games = move_in.get("remaining_games")
    out_games = move_out.get("remaining_games")
    if in_games is not None and out_games is not None:
        return (
            f"{move_in['name']} has {in_games} remaining game"
            f"{'' if in_games == 1 else 's'} at {move_in.get('fppg', 0):.1f} FP/G; "
            f"{move_out['name']} has {out_games} remaining game"
            f"{'' if out_games == 1 else 's'} at {move_out.get('fppg', 0):.1f} FP/G."
        )
    return (
        f"{move_in['name']} carries {move_in.get('fppg', 0):.1f} FP/G in this snapshot; "
        f"{move_out['name']} carries {move_out.get('fppg', 0):.1f} FP/G."
    )


def _indexed_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not _player_id(row):
        return None
    return row


def _is_active_lineup_row(row: dict[str, Any]) -> bool:
    slot = _slot(row)
    return (
        isinstance(row, dict)
        and bool(slot)
        and slot not in INACTIVE_SLOTS
        and not _is_unavailable(row)
        and not _is_protected_lineup_row(row)
    )


def _is_bench_row(row: dict[str, Any]) -> bool:
    return isinstance(row, dict) and _is_bench_slot(_slot(row)) and not _is_unavailable(row) and not _is_protected_lineup_row(row)


def _is_bench_slot(value: Any) -> bool:
    return str(value or "").strip().upper() in BENCH_SLOTS


def _slot(row: dict[str, Any] | None) -> str | None:
    if not isinstance(row, dict):
        return None
    value = str(row.get("slot") or "").strip().upper()
    return value or None


def _is_protected_lineup_row(row: dict[str, Any]) -> bool:
    slot = _slot(row)
    if slot in PROTECTED_LINEUP_SLOTS:
        return True
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    for source in (row, raw, player):
        if not isinstance(source, dict):
            continue
        if any(_truthy(source.get(flag)) for flag in PROTECTED_PLAYER_FLAGS):
            return True
    return False


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


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

        return sandlot_data_quality.short_reason(data_quality, purpose="lineup_recommendations")
    except Exception:
        reasons = data_quality.get("recommendation_reasons") or data_quality.get("reasons") or []
        return str(reasons[0]) if reasons else "Required snapshot data is incomplete"


def _lineup_simulation_preflight_reason(data_quality: dict[str, Any] | None) -> str | None:
    if not isinstance(data_quality, dict):
        return "Data quality is unavailable"
    if data_quality.get("recommendations_ready") is False:
        return _quality_reason(data_quality)
    if "lineup_slots" not in data_quality and data_quality.get("lineup_recommendations_ready") is not True:
        return "Lineup recommendation readiness is not explicitly trusted"
    required_sections = ["matchup", "my_roster", "all_team_rosters", "opponent_roster", "fppg", "future_games", "eligibility"]
    for key in required_sections:
        section = data_quality.get(key) if isinstance(data_quality.get(key), dict) else None
        if section and section.get("state") != "ok":
            return str(section.get("reason") or f"{key} incomplete")
    return None


def _participant_blocker(
    chain: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> str | None:
    by_id = {_player_id(row): row for row in rows if isinstance(row, dict) and _player_id(row)}
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
    period_start = _parse_date(matchup.get("start"))
    period_end = _parse_date(matchup.get("end"))

    for step in chain:
        row = by_id.get(str(step.get("player_id") or ""))
        if not isinstance(row, dict):
            return "proposal participant row is missing from the roster snapshot"
        name = str(row.get("name") or row.get("id") or "unknown player")
        if _is_protected_lineup_row(row):
            return f"{name} is protected and cannot be used in a hot swap"
        if not _has_trusted_slot_source(row):
            return f"slot provenance is untrusted for {name}"
        if period_end and not _has_proposal_future_game_provenance(row, period_end, period_start):
            return f"future-game provenance is not trusted for {name}"
    return None


def _has_trusted_slot_source(row: dict[str, Any]) -> bool:
    source = str(row.get("slot_source") or "").strip().casefold()
    return bool(source) and source not in {"", "position_fallback", "unknown", "fallback"}


def _has_proposal_future_game_provenance(
    row: dict[str, Any],
    period_end: date,
    period_start: date | None,
) -> bool:
    status = str(row.get("future_games_status") or "").strip()
    source = str(row.get("future_games_source") or "").strip()
    if status in sandlot_future_games.FAILED_FUTURE_GAME_STATUSES:
        return False
    if source == sandlot_future_games.SCHEDULE_SOURCE:
        if _is_pitcher_row(row):
            return (
                status == "ok"
                and str(row.get("future_games_scope") or "") == "pitcher_probable_starts"
                and _games_remaining(row, period_end, period_start) > 0
            )
        return status in sandlot_future_games.OK_FUTURE_GAME_STATUSES
    return _games_remaining(row, period_end, period_start) > 0


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


def _games_remaining(row: dict[str, Any], period_end: date, period_start: date | None = None) -> int:
    if _is_unavailable(row):
        return 0

    count = 0
    is_pitcher = _is_pitcher_row(row)
    for game in _future_games(row):
        if not isinstance(game, dict):
            continue
        game_date = _parse_date(game.get("date"))
        if game_date is None or game_date > period_end:
            continue
        if period_start is not None and game_date < period_start:
            continue
        if is_pitcher and not _has_pitcher_specific_appearance(row, game):
            continue
        count += 1
    return count


def _future_games(row: dict[str, Any]) -> list[Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    future_games = row.get("future_games") if "future_games" in row else raw.get("future_games")
    if isinstance(future_games, dict):
        return list(future_games.values())
    if isinstance(future_games, list):
        return future_games
    return []


def _is_pitcher_row(row: dict[str, Any]) -> bool:
    slot = _slot(row)
    if slot in PITCHER_POSITIONS:
        return True
    tokens = _eligibility_tokens(row)
    return bool(tokens & PITCHER_POSITIONS) and not bool(tokens & HITTER_POSITIONS)


def _has_pitcher_specific_appearance(row: dict[str, Any], game: dict[str, Any]) -> bool:
    for key in PITCHER_APPEARANCE_FLAGS:
        if _truthy_appearance_marker(game.get(key)):
            return True

    ids, names = _player_identity(row)
    for key in PITCHER_APPEARANCE_FIELDS:
        if _value_matches_player(game.get(key), ids, names):
            return True
    return False


def _truthy_appearance_marker(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if not isinstance(value, str):
        return False
    return value.strip().casefold() in {"1", "true", "yes", "y", "start", "starter", "probable", "confirmed"}


def _player_identity(row: dict[str, Any]) -> tuple[set[str], set[str]]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    ids = {
        _text(row.get("id")),
        _text(row.get("player_id")),
        _text(raw.get("id")),
        _text(raw.get("player_id")),
        _text(player.get("id")),
        _text(player.get("player_id")),
        _text(player.get("fantrax_id")),
    }
    names = {
        _text(row.get("name")),
        _text(raw.get("name")),
        _text(player.get("name")),
        _text(player.get("full_name")),
    }
    return {value for value in ids if value}, {value.casefold() for value in names if value}


def _value_matches_player(value: Any, ids: set[str], names: set[str]) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        candidates = [
            value.get("id"),
            value.get("player_id"),
            value.get("playerId"),
            value.get("fantrax_id"),
            value.get("name"),
            value.get("full_name"),
            value.get("fullName"),
        ]
        return any(_value_matches_player(candidate, ids, names) for candidate in candidates)
    if isinstance(value, list):
        return any(_value_matches_player(item, ids, names) for item in value)

    text = str(value).strip()
    if not text:
        return False
    return text in ids or text.casefold() in names


def _team_projection(
    rows: list[dict[str, Any]],
    current_score: float,
    period_end: date,
    period_start: date | None = None,
) -> tuple[float, float, int]:
    mean_delta = 0.0
    variance = 0.0
    games_remaining = 0
    for row in _active_rows(rows):
        games = _games_remaining(row, period_end, period_start)
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
        "opportunity_scope": "hitters plus pitcher-specific starts/appearances",
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
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
