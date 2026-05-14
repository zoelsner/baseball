"""Deterministic matchup projection helpers for Sandlot snapshots."""

from __future__ import annotations

import math
from datetime import date
from typing import Any


INACTIVE_SLOTS = {"BN", "IL", "IR", "RES", "RESERVE", "BE", "BENCH"}
UNAVAILABLE_INJURIES = {"OUT", "IL", "IL10", "IL60", "IR"}


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
        return {
            "projected_my": round(my_score, 1),
            "projected_opp": round(opp_score, 1),
            "my_remaining_games": 0,
            "opp_remaining_games": 0,
            "win_probability": _deterministic_prob(my_score - opp_score),
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

    return {
        "projected_my": round(mu_my, 1),
        "projected_opp": round(mu_opp, 1),
        "my_remaining_games": my_games,
        "opp_remaining_games": opp_games,
        "win_probability": round(_win_prob(mu_my, var_my, mu_opp, var_opp), 4),
        "complete": False,
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


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
