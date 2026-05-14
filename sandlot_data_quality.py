"""Deterministic snapshot data-quality gates for Sandlot features."""

from __future__ import annotations

from typing import Any


INACTIVE_SLOTS = {"BN", "IL", "IR", "RES", "RESERVE", "BE", "BENCH", "INJ", "INJ RES", "MINORS"}
GENERIC_POSITIONS = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "HIT", "PIT", "ALL", "UTIL"}


def snapshot_data_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Describe whether a raw snapshot can support projections/recommendations."""
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    my_rows = roster.get("rows") if isinstance(roster, dict) else None
    all_rosters = snapshot.get("all_team_rosters") if isinstance(snapshot.get("all_team_rosters"), dict) else None
    opponent_rows = _opponent_rows(snapshot, matchup)

    my_rows_list = my_rows if isinstance(my_rows, list) else []
    opponent_rows_list = opponent_rows if isinstance(opponent_rows, list) else []
    active_rows = _active_rows(my_rows_list) + _active_rows(opponent_rows_list)

    matchup_quality = _matchup_quality(matchup)
    my_roster_quality = _row_section("my_roster", my_rows_list, "No my-roster rows in snapshot")
    all_rosters_quality = _all_rosters_quality(all_rosters)
    opponent_roster_quality = _opponent_roster_quality(matchup, all_rosters, opponent_rows)
    fppg_quality = _coverage_section("Active-player FP/G", active_rows, _has_fppg)
    future_games_quality = _future_games_quality(active_rows)
    eligibility_quality = _coverage_section("Eligibility/position", active_rows, _has_position)

    complete = bool(matchup and matchup.get("complete"))
    projection_reasons = _projection_reasons(
        complete=complete,
        sections={
            "matchup": matchup_quality,
            "my_roster": my_roster_quality,
            "opponent_roster": opponent_roster_quality,
            "fppg": fppg_quality,
            "future_games": future_games_quality,
        },
    )
    recommendation_reasons = _recommendation_reasons({
        "matchup": matchup_quality,
        "my_roster": my_roster_quality,
        "all_team_rosters": all_rosters_quality,
        "opponent_roster": opponent_roster_quality,
        "fppg": fppg_quality,
        "future_games": future_games_quality,
        "eligibility": eligibility_quality,
    })

    reasons = _dedupe([*projection_reasons, *recommendation_reasons])
    return {
        "matchup": matchup_quality,
        "my_roster": my_roster_quality,
        "all_team_rosters": all_rosters_quality,
        "opponent_roster": opponent_roster_quality,
        "fppg": fppg_quality,
        "future_games": future_games_quality,
        "eligibility": eligibility_quality,
        "projection_ready": not projection_reasons,
        "recommendations_ready": not recommendation_reasons,
        "projection_reasons": projection_reasons,
        "recommendation_reasons": recommendation_reasons,
        "reasons": reasons,
    }


def short_reason(data_quality: dict[str, Any] | None, *, purpose: str = "projection") -> str:
    if not isinstance(data_quality, dict):
        return "Data quality is unavailable"
    key = "recommendation_reasons" if purpose.startswith("recommend") else "projection_reasons"
    reasons = data_quality.get(key) or data_quality.get("reasons") or []
    if not reasons:
        return "Required snapshot data is available"
    first = str(reasons[0]).rstrip(".")
    if len(reasons) == 1:
        return first
    return f"{first}, plus {len(reasons) - 1} more issue{'s' if len(reasons) != 2 else ''}"


def _matchup_quality(matchup: dict[str, Any] | None) -> dict[str, Any]:
    if not matchup:
        return _section("missing", "Matchup missing")
    missing = []
    if _number(matchup.get("my_score")) is None:
        missing.append("my score")
    if _number(matchup.get("opponent_score")) is None:
        missing.append("opponent score")
    if not matchup.get("end") and not matchup.get("complete"):
        missing.append("period end")
    if not matchup.get("opponent_team_id") and not matchup.get("opponent_team_name") and not matchup.get("complete"):
        missing.append("opponent")
    if missing:
        return _section("partial", "Matchup missing " + ", ".join(missing))
    return _section("ok")


def _row_section(name: str, rows: list[dict[str, Any]], missing_reason: str) -> dict[str, Any]:
    row_count = len(rows)
    if row_count <= 0:
        return _section("missing", missing_reason, row_count=0)
    return _section("ok", row_count=row_count)


def _all_rosters_quality(all_rosters: dict[str, Any] | None) -> dict[str, Any]:
    team_count = len(all_rosters or {})
    if team_count <= 0:
        return _section("missing", "All-team rosters missing", team_count=0)
    return _section("ok", team_count=team_count)


def _opponent_roster_quality(
    matchup: dict[str, Any] | None,
    all_rosters: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if not matchup:
        return _section("missing", "No matchup to identify opponent", row_count=0)
    if not all_rosters:
        return _section("missing", "No all-team rosters to find opponent", row_count=0)
    if not rows:
        return _section("missing", "Opponent roster rows missing", row_count=0)
    return _section("ok", row_count=len(rows))


def _coverage_section(label: str, rows: list[dict[str, Any]], predicate) -> dict[str, Any]:
    total = len(rows)
    if total <= 0:
        return _section("missing", f"{label} coverage has no active players", covered_players=0, total_players=0)
    covered = sum(1 for row in rows if predicate(row))
    if covered == total:
        return _section("ok", covered_players=covered, total_players=total)
    state = "partial" if covered else "missing"
    return _section(
        state,
        f"{label} coverage {covered}/{total}",
        covered_players=covered,
        total_players=total,
    )


def _future_games_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality = _coverage_section("Future-game", rows, _has_future_games)
    game_count = sum(_future_game_count(row) for row in rows)
    quality["remaining_game_count"] = game_count
    if quality.get("state") == "ok" and game_count <= 0:
        quality["state"] = "missing"
        quality["reason"] = "No future games in snapshot"
    return quality


def _projection_reasons(*, complete: bool, sections: dict[str, dict[str, Any]]) -> list[str]:
    required = ["matchup", "my_roster"] if complete else ["matchup", "my_roster", "opponent_roster", "fppg", "future_games"]
    return _section_reasons(sections, required)


def _recommendation_reasons(sections: dict[str, dict[str, Any]]) -> list[str]:
    return _section_reasons(
        sections,
        ["matchup", "my_roster", "all_team_rosters", "opponent_roster", "fppg", "future_games", "eligibility"],
    )


def _section_reasons(sections: dict[str, dict[str, Any]], required: list[str]) -> list[str]:
    reasons = []
    for key in required:
        section = sections.get(key) or {}
        if section.get("state") != "ok":
            reasons.append(str(section.get("reason") or f"{key} incomplete"))
    return _dedupe(reasons)


def _opponent_rows(snapshot: dict[str, Any], matchup: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    all_rosters = snapshot.get("all_team_rosters")
    if not isinstance(all_rosters, dict) or not matchup:
        return None

    opponent_id = matchup.get("opponent_team_id")
    if opponent_id:
        team = all_rosters.get(str(opponent_id))
        if isinstance(team, dict) and isinstance(team.get("rows"), list):
            return team["rows"]

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


def _active_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows or []
        if isinstance(row, dict) and str(row.get("slot") or "").strip().upper() not in INACTIVE_SLOTS
    ]


def _has_fppg(row: dict[str, Any]) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    return _number(row.get("fppg")) is not None or _number(raw.get("fantasy_points_per_game")) is not None


def _has_future_games(row: dict[str, Any]) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    if "future_games" in row:
        return isinstance(row.get("future_games"), (dict, list))
    if "future_games" in raw:
        return isinstance(raw.get("future_games"), (dict, list))
    return False


def _future_game_count(row: dict[str, Any]) -> int:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    future_games = row.get("future_games") if "future_games" in row else raw.get("future_games")
    if isinstance(future_games, dict):
        return len(future_games)
    if isinstance(future_games, list):
        return len(future_games)
    return 0


def _has_position(row: dict[str, Any]) -> bool:
    return bool(_position_tokens(row))


def _position_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("all_positions", "positions", "multi_positions", "pos", "slot"):
        value = row.get(key)
        values = value if isinstance(value, list) else str(value or "").replace("/", ",").split(",")
        for raw in values:
            token = str(raw or "").strip().upper()
            if token and token not in GENERIC_POSITIONS:
                tokens.add(token)
    return tokens


def _section(state: str, reason: str | None = None, **extra: Any) -> dict[str, Any]:
    out = {"state": state, **extra}
    if reason:
        out["reason"] = reason
    return out


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
