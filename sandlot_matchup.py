"""Deterministic matchup projection helpers for Sandlot snapshots."""

from __future__ import annotations

import math
from datetime import date
from typing import Any


INACTIVE_SLOTS = {"BN", "IL", "IR", "RES", "RESERVE", "BE", "BENCH"}
UNAVAILABLE_INJURIES = {"OUT", "IL", "IL10", "IL60", "IR"}
MODEL_VERSION = "matchup_projection_v1"


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
