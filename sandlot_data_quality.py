"""Deterministic snapshot data-quality gates for Sandlot features."""

from __future__ import annotations

import math
import re
from typing import Any

import sandlot_future_games


INACTIVE_SLOTS = {"BN", "IL", "IR", "RES", "RESERVE", "BE", "BENCH", "INJ", "INJ RES", "MIN", "MINORS"}
GENERIC_POSITIONS = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "MIN", "MINORS", "HIT", "PIT", "ALL", "UTIL"}
UNTRUSTED_SLOT_SOURCES = {"", "position_fallback", "unknown", "fallback"}
EXPLICITLY_UNTRUSTED_SLOT_SOURCES = UNTRUSTED_SLOT_SOURCES - {""}
TRUE_FPG_KEYS = ("FP/G", "FPG", "FPts/G", "FP/Gm", "FP/Game", "Fantasy Points/Game")
FANTRAX_AVG_KEYS = ("Avg", "Average")
SCORE_CONTEXT_KEYS = ("Score", "FPts", "ProjFPts", "FP", "Fantasy Points")
MAX_ABS_FPPG = 100.0
UNAVAILABLE_STATUSES = {"OUT", "SUSP", "SUSPENDED", "IL", "IL10", "IL60", "IR"}


def snapshot_data_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Describe whether a raw snapshot can support projections/recommendations."""
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    my_rows = roster.get("rows") if isinstance(roster, dict) else None
    all_rosters = snapshot.get("all_team_rosters") if isinstance(snapshot.get("all_team_rosters"), dict) else None
    free_agents = snapshot.get("free_agents") if isinstance(snapshot.get("free_agents"), dict) else {}
    opponent_rows = _opponent_rows(snapshot, matchup)

    my_rows_list = my_rows if isinstance(my_rows, list) else []
    opponent_rows_list = opponent_rows if isinstance(opponent_rows, list) else []
    free_agent_rows = free_agents.get("players") if isinstance(free_agents.get("players"), list) else []
    active_rows = _active_rows(my_rows_list) + _active_rows(opponent_rows_list)

    matchup_quality = _matchup_quality(matchup)
    my_roster_quality = _row_section("my_roster", my_rows_list, "No my-roster rows in snapshot")
    all_rosters_quality = _all_rosters_quality(all_rosters)
    opponent_roster_quality = _opponent_roster_quality(matchup, all_rosters, opponent_rows)
    fppg_quality = _coverage_section("Active-player FP/G", active_rows, _has_fppg)
    future_games_quality = _future_games_quality(active_rows)
    projection_future_games_quality = _future_games_quality(active_rows, projection=True)
    eligibility_quality = _coverage_section("Eligibility/position", active_rows, _has_position)
    lineup_slots_quality = _lineup_slots_quality(my_rows_list)
    projection_slots_quality = _projection_slots_quality(my_rows_list + opponent_rows_list)
    free_agent_pool_quality = _candidate_pool_quality(
        "Dynasty-safe free-agent",
        free_agent_rows,
        _has_actionable_free_agent,
    )
    lineup_change_policy_quality = _lineup_change_policy_quality(snapshot.get("league_rules"))

    complete = bool(matchup and matchup.get("complete"))
    projection_reasons = _projection_reasons(
        complete=complete,
        sections={
            "matchup": matchup_quality,
            "my_roster": my_roster_quality,
            "opponent_roster": opponent_roster_quality,
            "fppg": fppg_quality,
            "projection_future_games": projection_future_games_quality,
            "projection_slots": projection_slots_quality,
        },
    )
    recommendation_sections = {
        "matchup": matchup_quality,
        "my_roster": my_roster_quality,
        "all_team_rosters": all_rosters_quality,
        "opponent_roster": opponent_roster_quality,
        "fppg": fppg_quality,
        "future_games": future_games_quality,
        "eligibility": eligibility_quality,
        "lineup_slots": lineup_slots_quality,
    }
    recommendation_reasons = _recommendation_reasons(recommendation_sections)
    action_recommendation_reasons = _action_recommendation_reasons(recommendation_sections)
    add_drop_recommendation_reasons = _add_drop_recommendation_reasons(
        {**recommendation_sections, "free_agent_pool": free_agent_pool_quality}
    )

    reasons = _dedupe(
        [
            *projection_reasons,
            *recommendation_reasons,
            *action_recommendation_reasons,
            *add_drop_recommendation_reasons,
        ]
    )
    return {
        "matchup": matchup_quality,
        "my_roster": my_roster_quality,
        "all_team_rosters": all_rosters_quality,
        "opponent_roster": opponent_roster_quality,
        "fppg": fppg_quality,
        "future_games": future_games_quality,
        "projection_future_games": projection_future_games_quality,
        "eligibility": eligibility_quality,
        "lineup_slots": lineup_slots_quality,
        "projection_slots": projection_slots_quality,
        "free_agent_pool": free_agent_pool_quality,
        "lineup_change_policy": lineup_change_policy_quality,
        "projection_ready": not projection_reasons,
        "recommendations_ready": not recommendation_reasons,
        "lineup_recommendations_ready": not action_recommendation_reasons,
        "add_drop_recommendations_ready": not add_drop_recommendation_reasons,
        # The exact solver is intentionally not present in this evidence-only
        # slice. A future fixture-backed mapping and solver must set this true.
        "schedule_optimizer_ready": False,
        "schedule_optimizer_reasons": [lineup_change_policy_quality["reason"]],
        "projection_reasons": projection_reasons,
        "recommendation_reasons": recommendation_reasons,
        "lineup_recommendation_reasons": action_recommendation_reasons,
        "add_drop_recommendation_reasons": add_drop_recommendation_reasons,
        "reasons": reasons,
    }


def _lineup_change_policy_quality(league_rules: Any) -> dict[str, Any]:
    rules = league_rules if isinstance(league_rules, dict) else {}
    policy = (
        rules.get("lineup_change_policy")
        if isinstance(rules.get("lineup_change_policy"), dict)
        else {}
    )
    candidates = policy.get("candidates") if isinstance(policy.get("candidates"), list) else []
    candidate_hints = sorted({
        str(candidate.get("hint"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("hint")
    })
    observed = policy.get("state") == "observed_unclassified" and bool(candidates)
    reason = str(
        policy.get("reason")
        or "Fantrax lineup cadence and lock semantics are not present in the snapshot."
    )
    return {
        "state": "observed_unclassified" if observed else "missing",
        "trusted": False,
        "cadence": None,
        "lock_scope": None,
        "change_limit": None,
        "source": policy.get("source"),
        "reason": reason,
        "candidate_count": len(candidates),
        "candidate_hints": candidate_hints,
    }


def short_reason(data_quality: dict[str, Any] | None, *, purpose: str = "projection") -> str:
    if not isinstance(data_quality, dict):
        return "Data quality is unavailable"
    if purpose.startswith("lineup"):
        key = "lineup_recommendation_reasons"
    elif purpose.startswith("add_drop") or purpose.startswith("waiver"):
        key = "add_drop_recommendation_reasons"
    else:
        key = "recommendation_reasons" if purpose.startswith("recommend") else "projection_reasons"
    reasons = data_quality.get(key) or data_quality.get("reasons") or []
    if not reasons:
        if purpose.startswith("lineup") and data_quality.get("lineup_recommendations_ready") is not True:
            return "Lineup recommendation readiness is not explicitly trusted"
        if (purpose.startswith("add_drop") or purpose.startswith("waiver")) and (
            data_quality.get("add_drop_recommendations_ready") is not True
        ):
            return "Add/drop recommendation readiness is not explicitly trusted"
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


def _candidate_pool_quality(label: str, rows: list[dict[str, Any]], predicate) -> dict[str, Any]:
    """Require at least one individually trustworthy candidate, not perfect pool coverage."""
    total = len(rows)
    usable = sum(1 for row in rows if isinstance(row, dict) and predicate(row))
    if usable <= 0:
        return _section(
            "missing",
            f"{label} pool has 0/{total} players with trusted per-game value and age",
            usable_players=0,
            total_players=total,
        )
    return _section("ok", usable_players=usable, total_players=total)


def _future_games_quality(
    rows: list[dict[str, Any]],
    *,
    projection: bool = False,
) -> dict[str, Any]:
    predicate = _has_projection_future_games if projection else _has_future_games
    label = "Projection future-game" if projection else "Future-game"
    quality = _coverage_section(label, rows, predicate)
    game_count = sum(_future_game_count(row) for row in rows)
    quality["remaining_game_count"] = game_count
    status_counts: dict[str, int] = {}
    failed_examples: list[str] = []
    for row in rows:
        status = _future_game_status(row)
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
            if status in sandlot_future_games.FAILED_FUTURE_GAME_STATUSES and len(failed_examples) < 5:
                failed_examples.append(str(row.get("name") or row.get("id") or "unknown"))
    if status_counts:
        quality["status_counts"] = status_counts
    if projection and status_counts.get("pitcher_probables_unavailable"):
        quality["pitchers_without_probable_start"] = status_counts["pitcher_probables_unavailable"]
        quality["projection_scope"] = "known_opportunities_lower_bound"
        quality["assumption"] = (
            "Pitchers without a posted probable start contribute zero until MLB publishes a player-specific opportunity."
        )
    if failed_examples:
        quality["failed_examples"] = failed_examples
    if quality.get("state") == "ok" and game_count <= 0 and not _all_rows_schedule_backed(rows):
        quality["state"] = "missing"
        quality["reason"] = "No future games in snapshot"
    elif quality.get("state") == "ok" and game_count <= 0:
        quality["zero_remaining_games"] = True
    return quality


def _projection_reasons(*, complete: bool, sections: dict[str, dict[str, Any]]) -> list[str]:
    required = (
        ["matchup", "my_roster"]
        if complete
        else ["matchup", "my_roster", "opponent_roster", "fppg", "projection_future_games", "projection_slots"]
    )
    return _section_reasons(sections, required)


def _recommendation_reasons(sections: dict[str, dict[str, Any]]) -> list[str]:
    return _section_reasons(
        sections,
        ["matchup", "my_roster", "all_team_rosters", "opponent_roster", "fppg", "future_games", "eligibility"],
    )


def _action_recommendation_reasons(sections: dict[str, dict[str, Any]]) -> list[str]:
    return _section_reasons(
        sections,
        ["matchup", "my_roster", "all_team_rosters", "opponent_roster", "fppg", "future_games", "eligibility", "lineup_slots"],
    )


def _add_drop_recommendation_reasons(sections: dict[str, dict[str, Any]]) -> list[str]:
    return _section_reasons(
        sections,
        [
            "matchup",
            "my_roster",
            "all_team_rosters",
            "opponent_roster",
            "fppg",
            "future_games",
            "eligibility",
            "lineup_slots",
            "free_agent_pool",
        ],
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
        if (
            isinstance(row, dict)
            and str(row.get("slot") or "").strip().upper() not in INACTIVE_SLOTS
            and not _is_unavailable(row)
        )
    ]


def _is_unavailable(row: dict[str, Any]) -> bool:
    status = str(row.get("injury") or row.get("status") or "").strip().upper()
    if status in UNAVAILABLE_STATUSES:
        return True
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    return any(_truthy(player.get(key)) for key in ("out", "injured_reserve", "suspended"))


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _lineup_slots_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total <= 0:
        return _section("missing", "Lineup-slot source has no roster rows", trusted_players=0, total_players=0)

    trusted = []
    untrusted = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _has_trusted_slot_source(row):
            trusted.append(row)
        else:
            untrusted.append(row)

    if len(trusted) == total:
        return _section("ok", trusted_players=len(trusted), total_players=total)

    examples = [
        str(row.get("name") or row.get("id") or "unknown")
        for row in untrusted[:5]
    ]
    state = "missing" if not trusted else "partial"
    return _section(
        state,
        f"Lineup-slot source trusted for {len(trusted)}/{total} roster players",
        trusted_players=len(trusted),
        total_players=total,
        untrusted_examples=examples,
    )


def _projection_slots_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Report whether active rows can safely participate in projection math.

    Projection math deliberately excludes rows whose source explicitly says the
    slot was inferred or is unknown. Legacy rows with no source remain usable so
    older snapshots keep their historical behavior.
    """
    active_rows = _active_rows(rows)
    total = len(active_rows)
    if total <= 0:
        return _section(
            "missing",
            "Projection lineup-slot source has no active roster players",
            usable_players=0,
            total_players=0,
        )

    untrusted = [
        row
        for row in active_rows
        if str(row.get("slot_source") or "").strip().casefold() in EXPLICITLY_UNTRUSTED_SLOT_SOURCES
    ]
    usable = total - len(untrusted)
    if not untrusted:
        return _section("ok", usable_players=usable, total_players=total)

    examples = [str(row.get("name") or row.get("id") or "unknown") for row in untrusted[:5]]
    state = "missing" if usable <= 0 else "partial"
    return _section(
        state,
        f"Projection lineup-slot source usable for {usable}/{total} active players",
        usable_players=usable,
        total_players=total,
        untrusted_examples=examples,
    )


def _has_trusted_slot_source(row: dict[str, Any]) -> bool:
    source = str(row.get("slot_source") or "").strip().casefold()
    return bool(source) and source not in UNTRUSTED_SLOT_SOURCES


def _has_fppg(row: dict[str, Any]) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    value = _first_number(row.get("fppg"), raw.get("fantasy_points_per_game"))
    return value is not None and abs(value) <= MAX_ABS_FPPG


def _has_actionable_free_agent(row: dict[str, Any]) -> bool:
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    age, age_source = _free_agent_age_with_source(row, stats)
    if age is None or age_source is None or not 16 <= age <= 50:
        return False

    for key in TRUE_FPG_KEYS:
        value = _normalized_stat_value(stats, key)
        if _plausible_fpg(_number(value), allow_low=True):
            return True

    has_score_context = any(_normalized_stat_value(stats, key) is not None for key in SCORE_CONTEXT_KEYS)
    if has_score_context:
        for key in FANTRAX_AVG_KEYS:
            if _plausible_fpg(_number(_normalized_stat_value(stats, key))):
                return True
    return False


def _free_agent_age(row: dict[str, Any], stats: dict[str, Any]) -> float | None:
    return _free_agent_age_with_source(row, stats)[0]


def _free_agent_age_with_source(
    row: dict[str, Any],
    stats: dict[str, Any],
) -> tuple[float | None, str | None]:
    explicit = _first_number(row.get("age"))
    source = str(row.get("age_source") or "").strip()
    if explicit is not None and _trusted_age_source(source):
        return explicit, source

    for key in ("Age", "AGE", "age"):
        explicit = _first_number(stats.get(key))
        if explicit is not None:
            return explicit, f"stats.{key}"

    cells = stats.get("_cells")
    if not isinstance(cells, list) or len(cells) < 5:
        return None, None
    age = _number(cells[2])
    score = _number(cells[3])
    per_game = _number(cells[4])
    if age is None or score is None or not 16 <= age <= 50 or not _plausible_fpg(per_game):
        return None, None
    return age, "stats._cells[2]"


def _trusted_age_source(value: Any) -> bool:
    source = str(value or "").strip().casefold()
    return bool(source) and not any(token in source for token in ("unknown", "fallback", "inferred", "legacy"))


def _normalized_stat_value(stats: dict[str, Any], key: str) -> Any:
    target = re.sub(r"[^a-z0-9]", "", key.casefold())
    for raw_key, value in stats.items():
        if re.sub(r"[^a-z0-9]", "", str(raw_key).casefold()) == target:
            return value
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None


def _plausible_fpg(value: float | None, *, allow_low: bool = False) -> bool:
    if value is None:
        return False
    lower = 0.0 if allow_low else 0.5
    return lower < value <= 25.0


def _has_future_games(row: dict[str, Any]) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    status = _future_game_status(row)
    if status in sandlot_future_games.FAILED_FUTURE_GAME_STATUSES:
        return False
    if _future_game_source(row) == sandlot_future_games.SCHEDULE_SOURCE:
        return status in sandlot_future_games.OK_FUTURE_GAME_STATUSES
    if "future_games" in row:
        value = row.get("future_games")
        return isinstance(value, dict) or (isinstance(value, list) and len(value) > 0)
    if "future_games" in raw:
        value = raw.get("future_games")
        return isinstance(value, dict) or (isinstance(value, list) and len(value) > 0)
    return False


def _has_projection_future_games(row: dict[str, Any]) -> bool:
    # A successful schedule read with no player-specific probable is a known
    # lower bound, not missing provenance. The projection explicitly reports
    # these pitchers and counts them at zero until MLB publishes an opportunity.
    return _has_future_games(row)


def _future_game_count(row: dict[str, Any]) -> int:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    future_games = row.get("future_games") if "future_games" in row else raw.get("future_games")
    if isinstance(future_games, dict):
        return len(future_games)
    if isinstance(future_games, list):
        return len(future_games)
    return 0


def _future_game_status(row: dict[str, Any]) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    return str(row.get("future_games_status") or raw.get("future_games_status") or "").strip()


def _future_game_source(row: dict[str, Any]) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    return str(row.get("future_games_source") or raw.get("future_games_source") or "").strip()


def _all_rows_schedule_backed(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    return all(
        isinstance(row, dict)
        and _future_game_source(row) == sandlot_future_games.SCHEDULE_SOURCE
        and _future_game_status(row) in sandlot_future_games.OK_FUTURE_GAME_STATUSES
        for row in rows
    )


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
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
