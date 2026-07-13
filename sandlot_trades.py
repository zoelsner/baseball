"""Deterministic trade grader plus cached AI rationale.

V0.1 of the Trade tab. Mirrors the `sandlot_waivers.py` shape:

- Deterministic step computes a current per-game rate delta for each side,
  fairness, letter grade, and age delta from snapshot FP/G data.
- AI step explains the already-computed numbers via SkipperClient.complete().
- Result cached in `ai_briefs` keyed by (snapshot_id, "trade_grade", subject_key)
  with an input_hash so cache busts when relevant FP/G shifts.

V0.2 (counters + acceptance odds) and V0.3 (chat hookup) are deliberately
out of scope; see issue #3.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any

import sandlot_data_quality
import sandlot_db
import sandlot_skipper

log = logging.getLogger(__name__)

BRIEF_TYPE_GRADE = "trade_grade"
BRIEF_TYPE_COUNTER = "trade_counter"
TRADE_ELIGIBILITY_POLICY_VERSION = "trade_eligibility_v2"
MIN_COUNTER_ADD_FPPG = 0.75
MIN_VALID_DYNASTY_AGE = 16
MAX_VALID_DYNASTY_AGE = 50
DYNASTY_MANUAL_REVIEW_MAX_AGE = 24
PROTECTED_TRADE_SLOTS = {"MIN", "MINORS"}
PROTECTED_TRADE_FLAGS = {
    "protected",
    "is_protected",
    "keeper",
    "is_keeper",
    "keeper_protected",
    "minor",
    "minor_league",
    "minors",
    "is_minor_leaguer",
}

# Letter-grade thresholds applied to my_delta (sum of get FP/G - sum of give FP/G).
# Tuneable. The deterministic step always emits a grade; the AI just explains it.
_GRADE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (4.0, "A+"),
    (2.0, "A"),
    (1.0, "A−"),
    (0.5, "B+"),
    (0.0, "B"),
    (-0.5, "B−"),
    (-1.0, "C"),
    (-2.0, "D"),
)
_GRADE_FLOOR = "F"

GRADE_SYSTEM_PROMPT = """You explain a deterministic fantasy baseball trade grade.

Rules:
- Use only the supplied JSON. Do not invent injuries, news, lineups, or stats.
- Do not change the letter grade, deltas, or fairness — only explain them.
- Do not recommend Fantrax write actions (accept, decline, counter, send).
- Output ONE sentence under 32 words. No markdown, no bullets, no preamble.
- Cite at least one player name and at least one supplied number."""

COUNTER_SYSTEM_PROMPT = """You explain deterministic fantasy baseball counter offers.

Rules:
- Use only the supplied JSON. Do not invent players, reorder counters, change tiers, or estimate odds.
- Do not output percentages or calibrated acceptance probabilities.
- Output compact JSON only: [{"tier":"strong","rationale":"..."}, ...].
- Rationale must be one sentence under 26 words and mention why this counter fits the roster shape."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TradeGradeError(Exception):
    """Raised for caller-visible problems (missing players, empty side, etc)."""


def offer_validation_error(
    snapshot_row: dict[str, Any], give_ids: list[str], get_ids: list[str],
    *, expected_get_owner_id: str | None = None,
) -> str | None:
    """Return one safe fail-closed participant-policy reason without grading or AI work."""
    try:
        give_ids, get_ids = _validate_offer_ids(give_ids, get_ids)
        data = snapshot_row.get("data") or {}
        give_players, get_players = _resolve_trade_sides(
            data, give_ids, get_ids, expected_get_owner_id=expected_get_owner_id,
        )
        _validate_trade_participants(give_players, get_players)
    except TradeGradeError as exc:
        return str(exc)
    return None


def build_manual_review(
    snapshot_row: dict[str, Any],
    give_ids: list[str],
    get_ids: list[str],
    *,
    expected_get_owner_id: str | None = None,
    scheduled_execution_at_label: str | None = None,
) -> dict[str, Any]:
    """Explain an exact but ungradeable incoming offer without inventing value.

    This path is deterministic and read-only. It intentionally does not create
    a letter grade, acceptance recommendation, or trade receipt. Its job is to
    turn a fail-closed participant-policy result into a useful owner decision:
    hold, understand the roster consequences, and inspect the blocked evidence.
    """
    give_ids, get_ids = _validate_offer_ids(give_ids, get_ids)
    data = snapshot_row.get("data") or {}
    give_players, get_players = _resolve_trade_sides(
        data,
        give_ids,
        get_ids,
        expected_get_owner_id=expected_get_owner_id,
    )
    blockers = _manual_review_blockers(give_players, get_players)
    if not blockers:
        raise TradeGradeError("manual review requires a fail-closed participant-policy reason")

    give_names = ", ".join(str(row.get("name") or "Unknown") for row in give_players)
    get_names = ", ".join(str(row.get("name") or "Unknown") for row in get_players)
    blocker_names = list(dict.fromkeys(str(item.get("player_name") or "Unknown") for item in blockers))
    blocker_summary = ", ".join(blocker_names)
    unavailable_get_names = [
        str(item.get("player_name") or "Unknown")
        for item in blockers
        if item.get("kind") == "unavailable" and item.get("side") == "get"
    ]
    unavailable_give_names = [
        str(item.get("player_name") or "Unknown")
        for item in blockers
        if item.get("kind") == "unavailable" and item.get("side") == "give"
    ]
    unavailable_names = unavailable_give_names + unavailable_get_names
    dynasty_names = list(dict.fromkeys(
        str(item.get("player_name") or "Unknown")
        for item in blockers
        if item.get("kind") in {"protected_asset", "young_asset", "missing_age"}
    ))
    replacement_value = _manual_replacement_value(data, give_players, give_ids)
    give_rates = [_actionable_current_rate(row) for row in give_players]
    give_rate = (
        round(sum(rate for rate in give_rates if rate is not None), 2)
        if all(rate is not None for rate in give_rates)
        else None
    )
    give_positions = sorted(set().union(*(_position_tokens(row) for row in give_players)))
    get_positions = sorted(set().union(*(_position_tokens(row) for row in get_players)))

    current_period_detail = (
        f"Withheld because {', '.join(unavailable_names)} cannot be projected from current health and role evidence."
        if unavailable_names
        else "Withheld because one or more assets do not have gradeable current-period evidence."
    )
    ros_detail = (
        "Return timing and future role are not verified for " + ", ".join(unavailable_names) + "."
        if unavailable_names
        else "Current roles and playing-time assumptions are not complete enough for a rest-of-season comparison."
    )
    dynasty_detail = (
        "Manual dynasty valuation required for " + ", ".join(dynasty_names) + "."
        if dynasty_names
        else "Age alone cannot establish the long-term value of this package."
    )
    if unavailable_get_names and unavailable_give_names:
        counter_title = "Counter direction: resolve health uncertainty on both sides"
        counter_detail = "Do not compare or reshape the package until both unavailable sides have verified return and role evidence."
    elif unavailable_get_names:
        counter_title = "Counter direction: ask for healthy, gradeable value"
        counter_detail = "Ask the other manager to replace the unavailable incoming asset before Sandlot ranks an exact counter package."
    elif unavailable_give_names:
        counter_title = "Counter direction: value your unavailable player first"
        counter_detail = "Do not sell from a stale rate; verify your outgoing player's return, role, and dynasty value before naming a counter."
    elif dynasty_names:
        counter_title = "Counter direction: value the long-term assets first"
        counter_detail = "Do not name an exact counter until the young or protected assets receive a manual dynasty review."
    else:
        counter_title = "Counter direction: verify the missing evidence first"
        counter_detail = "Do not name an exact counter until every participant has a valid current-rate evidence record."

    do_nothing_rate = (
        f" at a verified current snapshot package rate of {give_rate:.2f} FP/G"
        if give_rate is not None
        else " with its current package rate withheld"
    )
    skipper_prompt = (
        f"Review this exact incoming Fantrax offer. I give {give_names}; I get {get_names}. "
        f"Sandlot is holding the offer because the blocked participants are {blocker_summary}. "
        f"The do-nothing alternative is to keep {give_names}{do_nothing_rate}. "
        "Explain current-matchup, rest-of-season, dynasty, roster-fit, and replacement-value implications. "
        "Then propose a health- and dynasty-aware counter direction. Clearly separate verified facts from assumptions, "
        "and do not claim the trade was accepted, rejected, or sent."
    )

    return {
        "state": "manual_review_required",
        "recommendation": {
            "action": "hold",
            "title": "Hold this offer for now",
            "detail": f"Sandlot cannot safely compare the full package while {blocker_summary} has unresolved value evidence.",
        },
        "uncertainty": {
            "level": "high",
            "label": "Value withheld",
            "detail": "A current-rate grade would treat uncertain health, roles, or long-term assets as if they were normal active players.",
        },
        "deadline": {
            "state": "unknown",
            "label": "Not provided",
            "fantrax_schedule_label": str(scheduled_execution_at_label or "Pending"),
            "detail": "The Fantrax schedule label is not a verified response deadline. Recheck the offer before answering.",
        },
        "do_nothing": {
            "title": f"Keep {give_names}",
            "detail": (
                "The roster stays unchanged and the incoming offer remains unanswered in Fantrax."
                if give_rate is not None
                else "The roster stays unchanged; current-rate value is withheld because the outgoing side is unavailable or missing valid FP/G evidence."
            ),
            "current_rate_preserved": give_rate,
            "unit": "FP/G",
        },
        "horizons": [
            {"key": "current_matchup", "label": "Current matchup", "status": "withheld", "detail": current_period_detail},
            {"key": "rest_of_season", "label": "Rest of season", "status": "withheld", "detail": ros_detail},
            {"key": "dynasty", "label": "Dynasty", "status": "manual_review", "detail": dynasty_detail},
        ],
        "roster_consequences": {
            "give_positions": give_positions,
            "get_positions": get_positions,
            "label": f"Moves out {', '.join(give_positions) or 'unverified positions'}; brings in {', '.join(get_positions) or 'unverified positions'}.",
            "detail": "This describes roster shape only; it is not proof that every incoming player can fill the vacated lineup role.",
        },
        "replacement_value": replacement_value,
        "counteroffer": {
            "state": "direction_only",
            "title": counter_title,
            "detail": counter_detail,
            "exact_package_available": False,
        },
        "blockers": blockers,
        "safest_next_action": {
            "type": "ask_skipper",
            "label": "Ask Skipper to pressure-test it",
            "detail": "Review return timing, role risk, and long-term asset value before taking any action in Fantrax.",
        },
        "skipper_prompt": skipper_prompt,
        "manual_only": True,
        "read_only": True,
        "fantrax_changed": False,
        "writes_enabled": False,
    }


def _manual_review_blockers(
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for side, players in (("give", give_players), ("get", get_players)):
        for row in players:
            player_id = str(row.get("id") or "")
            player_name = str(row.get("name") or player_id or "Unknown")
            common = {"side": side, "player_id": player_id, "player_name": player_name}
            if _is_protected_trade_player(row):
                blockers.append({**common, "kind": "protected_asset", "reason": "Protected keeper or minors value requires manual review."})
            unavailable = _unavailable_reason(row)
            if unavailable:
                blockers.append({**common, "kind": "unavailable", "reason": f"Currently {unavailable}; return timing and role are not modeled."})
            fppg = _number(row.get("fppg"))
            if fppg is None or not math.isfinite(fppg):
                blockers.append({**common, "kind": "missing_rate", "reason": "Current FP/G evidence is unavailable."})
            age = _age(row)
            if age is None:
                blockers.append({**common, "kind": "missing_age", "reason": "Trusted age evidence is unavailable for dynasty review."})
            elif age <= DYNASTY_MANUAL_REVIEW_MAX_AGE:
                blockers.append({**common, "kind": "young_asset", "reason": f"Age {age:g} requires manual dynasty valuation."})
    return blockers


def _manual_replacement_value(
    data: dict[str, Any],
    give_players: list[dict[str, Any]],
    give_ids: list[str],
) -> dict[str, Any]:
    my_rows = [row for row in ((data.get("roster") or {}).get("rows") or []) if isinstance(row, dict)]
    excluded = set(give_ids)
    comparisons = []
    for outgoing in give_players:
        outgoing_tokens = _replacement_tokens(outgoing)
        candidates = []
        for row in my_rows:
            if str(row.get("id") or "") in excluded or not _is_inactive(row):
                continue
            if _is_unavailable(row) or _is_protected_trade_player(row):
                continue
            if not (outgoing_tokens & _replacement_tokens(row)):
                continue
            if _actionable_current_rate(row) is None:
                continue
            candidates.append(row)
        candidates.sort(key=lambda row: (_fppg(row), str(row.get("name") or "")), reverse=True)
        replacement = candidates[0] if candidates else None
        outgoing_rate = _actionable_current_rate(outgoing)
        replacement_rate = _actionable_current_rate(replacement) if replacement else None
        comparisons.append({
            "outgoing": _slim_player(outgoing),
            "replacement": _slim_player(replacement) if replacement else None,
            "gap_fppg": round(replacement_rate - outgoing_rate, 2) if replacement_rate is not None and outgoing_rate is not None else None,
            "status": "reserve_cover_found" if replacement else "no_verified_reserve_cover",
        })
    covered = [item for item in comparisons if item.get("replacement")]
    if covered:
        covered.sort(key=lambda item: (
            item.get("gap_fppg") is not None,
            float(item.get("gap_fppg") or 0.0),
            float((item.get("replacement") or {}).get("fppg") or 0.0),
        ), reverse=True)
        best = covered[0]
        replacement = best["replacement"]
        if best.get("gap_fppg") is not None:
            gap = float(best["gap_fppg"])
            label = f"Best reserve cover: {replacement.get('name')} ({gap:+.2f} FP/G vs outgoing)"
            detail = "Reserve-only, same-position comparison; exact post-trade lineup optimization is not simulated."
        else:
            label = f"Reserve cover found: {replacement.get('name')} (numeric gap withheld)"
            detail = "The outgoing player's current rate is not actionable, so Sandlot does not calculate a replacement gap."
        status = "directional"
    else:
        label = "No verified reserve cover found"
        detail = "Sandlot cannot show a safe internal replacement for the outgoing side from the current roster snapshot."
        status = "unavailable"
    return {"status": status, "label": label, "detail": detail, "comparisons": comparisons}


def _replacement_tokens(row: dict[str, Any]) -> set[str]:
    tokens = _position_tokens(row)
    baseball_positions = tokens - {"UT"}
    return baseball_positions or tokens


def _actionable_current_rate(row: dict[str, Any] | None) -> float | None:
    if not row or _is_unavailable(row):
        return None
    value = _number(row.get("fppg"))
    return value if value is not None and math.isfinite(value) else None


def grade_offer(
    snapshot_row: dict[str, Any],
    give_ids: list[str],
    get_ids: list[str],
) -> dict[str, Any]:
    """Grade a two-party offer against a stored Fantrax snapshot.

    Returns a payload suitable for the API. Raises TradeGradeError for
    caller-fixable issues (so the route can map to a 400).
    """
    if not give_ids or not get_ids:
        raise TradeGradeError("trade must have at least one player on each side")
    give_ids, get_ids = _validate_offer_ids(give_ids, get_ids)

    snapshot_id = int(snapshot_row.get("id") or 0)
    if not snapshot_id:
        raise TradeGradeError("snapshot row is missing an id")
    data = snapshot_row.get("data") or {}
    give_players, get_players = _resolve_trade_sides(data, give_ids, get_ids)
    _validate_trade_participants(give_players, get_players)
    data_quality = sandlot_data_quality.snapshot_data_quality(data)

    deltas = _compute_deltas(give_players, get_players)
    counter_result = _build_counter_result(
        snapshot_row=snapshot_row,
        data_quality=data_quality,
        give_ids=give_ids,
        get_ids=get_ids,
        give_players=give_players,
        get_players=get_players,
        deltas=deltas,
    )
    subject_key = _subject_key(give_ids, get_ids)
    context = _grade_prompt_context(snapshot_row, give_players, get_players, deltas)
    input_hash = _hash_context(context)

    rationale, model, cached = _load_or_generate_rationale(
        snapshot_id=snapshot_id,
        subject_key=subject_key,
        input_hash=input_hash,
        context=context,
    )

    analysis = _build_trade_analysis(
        give_players=give_players,
        get_players=get_players,
        deltas=deltas,
        counter_result=counter_result,
    )

    return {
        "snapshot_id": snapshot_id,
        "grade_scope": "current_rate_only",
        "value_basis": "current_snapshot_fppg",
        "time_horizon": "per_game_rate_only",
        "dynasty_complete": False,
        "grade": deltas["letter_grade"],
        "letter_grade": deltas["letter_grade"],
        "headline": _headline(deltas),
        "fairness": deltas["fairness"],
        "my_delta": deltas["my_delta"],
        "their_delta": deltas["their_delta"],
        "age_delta": deltas["age_delta"],
        "my_give_fppg": deltas["my_give_fppg"],
        "my_get_fppg": deltas["my_get_fppg"],
        "my_give": [_slim_player(p) for p in give_players],
        "my_get": [_slim_player(p) for p in get_players],
        "eligibility_evidence": _trade_eligibility_evidence(give_players, get_players),
        "rationale": rationale,
        "counters": counter_result["counters"],
        "my_weakest_position": counter_result["my_weakest_position"],
        "no_counter_reason": counter_result["no_counter_reason"],
        "analysis": analysis,
        "model": model,
        "cached": cached,
    }


def _trade_eligibility_evidence(
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
) -> dict[str, Any]:
    """Retain normalized, non-secret facts behind the fail-closed participant gate."""
    participants = []
    for side, players in (("give", give_players), ("get", get_players)):
        for row in players:
            participants.append({
                "side": side,
                "player_id": str(row.get("id") or ""),
                "slot": str(row.get("slot") or "").strip() or None,
                "age": _number(row.get("age")),
                "age_source": str(row.get("age_source") or "").strip() or None,
                "protected_trade_player": _is_protected_trade_player(row),
                "available_for_current_rate_grade": not _is_unavailable(row),
                "requires_manual_dynasty_review": (_age(row) or 0) <= DYNASTY_MANUAL_REVIEW_MAX_AGE,
                "fppg_valid": _number(row.get("fppg")) is not None,
            })
    return {
        "policy_version": TRADE_ELIGIBILITY_POLICY_VERSION,
        "maximum_auto_graded_age_floor": DYNASTY_MANUAL_REVIEW_MAX_AGE,
        "participants": sorted(participants, key=lambda item: (item["side"], item["player_id"])),
        "all_checks_passed": True,
    }


def _build_trade_analysis(
    *,
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    deltas: dict[str, Any],
    counter_result: dict[str, Any],
) -> dict[str, Any]:
    """Package the grader's evidence without inventing unsupported horizons."""
    my_delta = float(deltas["my_delta"])
    weakest = counter_result.get("my_weakest_position")
    acquired_positions = sorted(set().union(*(_position_tokens(p) for p in get_players)))
    fills_weakest = bool(weakest and weakest in acquired_positions)
    fit_label = (
        f"Adds {weakest} help"
        if fills_weakest
        else f"Does not directly fill {weakest}"
        if weakest
        else "Roster fit needs manual review"
    )
    fit_detail = (
        f"The get side includes {weakest}, your weakest current-rate position."
        if fills_weakest
        else f"Your weakest current-rate position is {weakest}; the get side covers {', '.join(acquired_positions) or 'no verified position'}."
        if weakest
        else "The snapshot could not identify a weakest position confidently."
    )

    counters = counter_result.get("counters") or []
    recommended_counter = next((c for c in counters if c.get("tier") == "balanced"), None)
    if recommended_counter is None and counters:
        recommended_counter = counters[0]

    if recommended_counter:
        recommendation = {
            "action": "counter",
            "title": "Counter before accepting",
            "detail": recommended_counter.get("rationale")
            or "A deterministic counter improves the current-rate package.",
        }
    elif my_delta >= 0.5:
        recommendation = {
            "action": "review",
            "title": "Current rate favors you; check the dynasty cost",
            "detail": counter_result.get("no_counter_reason")
            or "No counter is needed on current snapshot rate, but long-term value is not fully modeled.",
        }
    elif my_delta <= -0.5:
        recommendation = {
            "action": "review",
            "title": "Hold — the current rate is against you",
            "detail": counter_result.get("no_counter_reason")
            or "The current snapshot rate is negative and no safe counter is available.",
        }
    else:
        recommendation = {
            "action": "review",
            "title": "Near-even rate; decide on roster fit",
            "detail": counter_result.get("no_counter_reason")
            or "The rate gap is small enough that role, fit, and long-term value should decide it.",
        }

    age_delta = deltas.get("age_delta")
    age_value = None if age_delta is None else round(float(age_delta), 1)
    age_detail = (
        "Average age is only a directional signal; prospects, contracts, and market value are not modeled."
        if age_value is not None
        else "The snapshot does not have enough trusted age data for a directional signal."
    )
    horizons = [
        {
            "key": "current_rate",
            "label": "Current rate",
            "status": "modeled",
            "value": round(my_delta, 2),
            "unit": "FP/G",
            "detail": "Net change from current snapshot scoring rates.",
        },
        {
            "key": "this_week",
            "label": "This week",
            "status": "unavailable",
            "value": None,
            "unit": None,
            "detail": "Weekly games, probable starts, and lineup usage are not modeled in this grade yet.",
        },
        {
            "key": "rest_of_season",
            "label": "Rest of season",
            "status": "unavailable",
            "value": None,
            "unit": None,
            "detail": "Rest-of-season playing time and projection changes are not modeled yet.",
        },
        {
            "key": "dynasty",
            "label": "Dynasty",
            "status": "limited" if age_value is not None else "unavailable",
            "value": age_value,
            "unit": "yr avg age" if age_value is not None else None,
            "detail": age_detail,
        },
    ]

    give_names = ", ".join(str(p.get("name") or "Unknown") for p in give_players)
    get_names = ", ".join(str(p.get("name") or "Unknown") for p in get_players)
    counter_prompt = ""
    if recommended_counter:
        counter_give = ", ".join(
            str(p.get("name") or "Unknown") for p in (recommended_counter.get("give") or [])
        )
        counter_get = ", ".join(
            str(p.get("name") or "Unknown") for p in (recommended_counter.get("get") or [])
        )
        counter_prompt = (
            f" The recommended {recommended_counter.get('tier') or 'balanced'} counter is: "
            f"give {counter_give}; get {counter_get}; current-rate delta "
            f"{float(recommended_counter.get('my_delta') or 0):+.2f} FP/G."
        )
    skipper_prompt = (
        f"Sandlot trade-analysis evidence: I give {give_names}; I get {get_names}. "
        f"The deterministic snapshot-rate delta is {my_delta:+.2f} FP/G. "
        f"Sandlot's current recommendation is: {recommendation['title']}.{counter_prompt} "
        "Challenge the assumptions, explain this-week, rest-of-season, and dynasty implications, "
        "and compare the best counter. Do not claim unsupported certainty."
    )

    return {
        "recommendation": recommendation,
        "horizons": horizons,
        "roster_fit": {
            "weakest_position": weakest,
            "acquired_positions": acquired_positions,
            "fills_weakest_position": fills_weakest,
            "label": fit_label,
            "detail": fit_detail,
        },
        "recommended_counter": recommended_counter,
        "skipper_prompt": skipper_prompt,
        "manual_only": True,
    }


# ---------------------------------------------------------------------------
# Player selection and ownership validation
# ---------------------------------------------------------------------------


def _validate_offer_ids(
    give_ids: list[str],
    get_ids: list[str],
) -> tuple[list[str], list[str]]:
    def normalize(ids: list[str], side: str) -> list[str]:
        normalized = [str(pid or "").strip() for pid in ids]
        if any(not pid for pid in normalized):
            raise TradeGradeError(f"{side} side contains an empty player id")
        seen: set[str] = set()
        duplicates: list[str] = []
        for pid in normalized:
            if pid in seen and pid not in duplicates:
                duplicates.append(pid)
            seen.add(pid)
        if duplicates:
            raise TradeGradeError(
                f"duplicate player id(s) on {side} side: " + ", ".join(duplicates)
            )
        return normalized

    give = normalize(give_ids, "give")
    get = normalize(get_ids, "get")
    overlap = sorted(set(give) & set(get))
    if overlap:
        raise TradeGradeError(
            "player id(s) cannot appear on both sides: " + ", ".join(overlap)
        )
    return give, get


def _resolve_trade_sides(
    data: dict[str, Any],
    give_ids: list[str],
    get_ids: list[str],
    *,
    expected_get_owner_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    my_rows: dict[str, dict[str, Any]] = {}
    for row in (data.get("roster") or {}).get("rows") or []:
        if not isinstance(row, dict):
            continue
        pid = row.get("id")
        if pid not in (None, ""):
            my_rows.setdefault(str(pid), row)

    free_agent_ids = {
        str(row.get("id"))
        for row in (data.get("free_agents") or {}).get("players") or []
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }

    opponent_owners: dict[str, dict[str, dict[str, Any]]] = {}
    all_team_rosters = data.get("all_team_rosters") or {}
    my_team_id = str(data.get("team_id") or "").strip()
    if not isinstance(all_team_rosters, dict):
        all_team_rosters = {}
    for roster_key, team in all_team_rosters.items():
        if not isinstance(team, dict):
            continue
        team_id = str(team.get("team_id") or roster_key or "").strip()
        is_my_team = _truthy(team.get("is_me")) or bool(
            my_team_id
            and (team_id == my_team_id or str(roster_key).strip() == my_team_id)
        )
        if is_my_team:
            continue
        for row in (team or {}).get("rows") or []:
            if not isinstance(row, dict) or row.get("id") in (None, ""):
                continue
            pid = str(row.get("id"))
            opponent_owners.setdefault(pid, {}).setdefault(team_id, row)

    give_players: list[dict[str, Any]] = []
    for pid in give_ids:
        row = my_rows.get(pid)
        if row is None:
            raise TradeGradeError(f"give player {pid} is not on my canonical roster")
        if pid in free_agent_ids or pid in opponent_owners:
            raise TradeGradeError(
                f"give player {pid} has conflicting ownership in the snapshot"
            )
        give_players.append(row)

    get_players: list[dict[str, Any]] = []
    selected_owner_ids: set[str] = set()
    for pid in get_ids:
        if pid in my_rows:
            raise TradeGradeError(f"get player {pid} is already on my roster")
        owners = opponent_owners.get(pid) or {}
        if pid in free_agent_ids:
            if owners:
                raise TradeGradeError(
                    f"get player {pid} has conflicting free-agent and roster ownership"
                )
            raise TradeGradeError(f"get player {pid} is a free agent, not a trade asset")
        if not owners:
            raise TradeGradeError(f"get player {pid} is not on an opponent roster")
        if len(owners) != 1:
            raise TradeGradeError(f"get player {pid} appears on multiple opponent rosters")
        team_id, row = next(iter(owners.items()))
        selected_owner_ids.add(team_id)
        get_players.append(row)

    if len(selected_owner_ids) != 1:
        raise TradeGradeError("get players must all come from one opponent roster")
    if expected_get_owner_id is not None and selected_owner_ids != {str(expected_get_owner_id)}:
        raise TradeGradeError("get players no longer belong to the incoming offer counterparty")
    return give_players, get_players


def _validate_trade_participants(
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
) -> None:
    for side, players in (("give", give_players), ("get", get_players)):
        for row in players:
            label = str(row.get("name") or row.get("id") or "unknown player")
            if _is_protected_trade_player(row):
                raise TradeGradeError(
                    f"{side} player {label} is protected as a keeper/minors asset and cannot be trade graded"
                )
            unavailable_reason = _unavailable_reason(row)
            if unavailable_reason:
                raise TradeGradeError(
                    f"{side} player {label} is {unavailable_reason}; "
                    "current-rate-only grading cannot establish actionable trade value"
                )
            fppg = _number(row.get("fppg"))
            if fppg is None or not math.isfinite(fppg):
                raise TradeGradeError(f"{side} player {label} is missing a valid FP/G value")
            age = _age(row)
            if age is None:
                raise TradeGradeError(
                    f"{side} player {label} is missing a valid age for dynasty grading"
                )
            if age <= DYNASTY_MANUAL_REVIEW_MAX_AGE:
                raise TradeGradeError(
                    f"{side} player {label} is age {age:g} and requires manual dynasty review"
                )


def _is_protected_trade_player(row: dict[str, Any]) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    scorer = raw.get("scorer") if isinstance(raw.get("scorer"), dict) else {}
    for source in (row, raw, player, scorer):
        for key in (
            "slot",
            "slot_full",
            "rosterSlot",
            "rosterSlotName",
            "status",
            "statusName",
            "statusShortName",
        ):
            slot = str(source.get(key) or "").strip().upper()
            if slot in PROTECTED_TRADE_SLOTS:
                return True
        if any(_truthy(source.get(flag)) for flag in PROTECTED_TRADE_FLAGS):
            return True
    return False


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


# ---------------------------------------------------------------------------
# Deterministic math
# ---------------------------------------------------------------------------

def _compute_deltas(
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
) -> dict[str, Any]:
    my_give_fppg = round(sum(_fppg(p) for p in give_players), 2)
    my_get_fppg = round(sum(_fppg(p) for p in get_players), 2)
    my_delta = round(my_get_fppg - my_give_fppg, 2)
    their_delta = round(-my_delta, 2)

    denom = max(my_give_fppg, my_get_fppg, 1.0)
    fairness = max(0.0, min(1.0, 1.0 - abs(my_delta) / denom))
    fairness = round(fairness, 3)

    age_delta = _age_delta(give_players, get_players)
    letter_grade = _letter_grade(my_delta)

    return {
        "my_give_fppg": my_give_fppg,
        "my_get_fppg": my_get_fppg,
        "my_delta": my_delta,
        "their_delta": their_delta,
        "fairness": fairness,
        "age_delta": age_delta,
        "letter_grade": letter_grade,
    }


def _build_counter_result(
    *,
    snapshot_row: dict[str, Any],
    data_quality: dict[str, Any],
    give_ids: list[str],
    get_ids: list[str],
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    deltas: dict[str, Any],
) -> dict[str, Any]:
    data = snapshot_row.get("data") or {}
    my_rows = (data.get("roster") or {}).get("rows") or []
    my_weak_positions = _weak_positions(my_rows)
    my_weakest_position = my_weak_positions[0] if my_weak_positions else None

    if not data_quality.get("recommendations_ready"):
        return _counter_result(
            my_weakest_position,
            "Counter guidance paused: "
            + sandlot_data_quality.short_reason(data_quality, purpose="recommendations")
            + ".",
        )
    if deltas["my_delta"] >= 0.5:
        return _counter_result(
            my_weakest_position,
            "Current-rate comparison already favors you; no counter needed.",
        )

    counterparty = _counterparty_team(data, get_players)
    if not counterparty:
        return _counter_result(
            my_weakest_position,
            "Counter guidance needs the get side to come from one roster.",
        )

    team_id, team = counterparty
    opponent_rows = [row for row in (team.get("rows") or []) if isinstance(row, dict)]
    opponent_weak_positions = _weak_positions(opponent_rows)
    candidates = _counter_candidates(
        give_players=give_players,
        get_players=get_players,
        opponent_rows=opponent_rows,
        my_weak_positions=my_weak_positions,
        opponent_weak_positions=opponent_weak_positions,
        deltas=deltas,
    )
    picked = _pick_counter_tiers(candidates)
    if not picked:
        return _counter_result(
            my_weakest_position,
            "No deterministic counter clears a meaningful improvement threshold.",
        )

    counters = [
        _counter_card(
            tier=tier,
            candidate=candidate,
            give_players=give_players,
            get_players=get_players,
            my_weakest_position=my_weakest_position,
            opponent_need=candidate.get("opponent_need"),
        )
        for tier, candidate in picked
    ]
    _overlay_counter_rationales(
        snapshot_id=int(snapshot_row.get("id") or 0),
        give_ids=give_ids,
        get_ids=get_ids,
        counters=counters,
        team_id=str(team_id),
    )
    return {"counters": counters, "my_weakest_position": my_weakest_position, "no_counter_reason": None}


def _counter_result(my_weakest_position: str | None, reason: str) -> dict[str, Any]:
    return {"counters": [], "my_weakest_position": my_weakest_position, "no_counter_reason": reason}


def _counterparty_team(data: dict[str, Any], get_players: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    get_ids = {str(p.get("id")) for p in get_players if p.get("id")}
    if not get_ids:
        return None
    matches: list[tuple[str, dict[str, Any]]] = []
    for tid, team in (data.get("all_team_rosters") or {}).items():
        if not isinstance(team, dict):
            continue
        team_ids = {str(row.get("id")) for row in (team.get("rows") or []) if isinstance(row, dict) and row.get("id")}
        if get_ids <= team_ids:
            matches.append((str(team.get("team_id") or tid), team))
    if len(matches) != 1:
        return None
    return matches[0]


def _counter_candidates(
    *,
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    opponent_rows: list[dict[str, Any]],
    my_weak_positions: list[str],
    opponent_weak_positions: list[str],
    deltas: dict[str, Any],
) -> list[dict[str, Any]]:
    base_get_ids = {str(p.get("id")) for p in get_players if p.get("id")}
    give_positions = set().union(*(_position_tokens(p) for p in give_players)) if give_players else set()
    opponent_need = sorted(give_positions & set(opponent_weak_positions))
    candidates: list[dict[str, Any]] = []
    for row in opponent_rows:
        if str(row.get("id")) in base_get_ids:
            continue
        fppg = _fppg(row)
        age = _age(row)
        if (
            fppg < MIN_COUNTER_ADD_FPPG
            or age is None
            or age <= DYNASTY_MANUAL_REVIEW_MAX_AGE
            or _is_unavailable(row)
            or _is_protected_trade_player(row)
        ):
            continue
        tokens = _position_tokens(row)
        weak_fit = sorted(tokens & set(my_weak_positions))
        weakest_fit = 1.4 if my_weak_positions and my_weak_positions[0] in tokens else 0.0
        fit_score = weakest_fit + (0.6 if weak_fit else 0.0)
        other_need_score = 0.6 if opponent_need else 0.0
        score = round(fppg + fit_score + other_need_score, 3)
        counter_get_fppg = round(deltas["my_get_fppg"] + fppg, 2)
        counter_delta = round(counter_get_fppg - deltas["my_give_fppg"], 2)
        candidates.append({
            "row": row,
            "fppg": fppg,
            "score": score,
            "weak_fit": weak_fit,
            "opponent_need": opponent_need,
            "counter_delta": counter_delta,
        })
    return sorted(candidates, key=lambda c: (c["score"], c["fppg"], str((c["row"] or {}).get("name") or "")), reverse=True)


def _pick_counter_tiers(candidates: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    if not candidates:
        return []
    picked: list[tuple[str, dict[str, Any]]] = []
    used: set[str] = set()

    def add(tier: str, candidate: dict[str, Any] | None) -> None:
        if not candidate:
            return
        pid = str((candidate.get("row") or {}).get("id") or "")
        if not pid or pid in used:
            return
        used.add(pid)
        picked.append((tier, candidate))

    # Counter bands describe negotiation posture, not star power. Pick the
    # closest unique package to each target edge so an elite player is not
    # labeled the default "strong" ask merely because he has the most FP/G.
    targets = (("strong", 1.5), ("balanced", 0.5), ("light", 0.0))
    for tier, target in targets:
        remaining = [
            candidate
            for candidate in candidates
            if str((candidate.get("row") or {}).get("id") or "") not in used
        ]
        add(
            tier,
            min(
                remaining,
                key=lambda candidate: (
                    abs(candidate["counter_delta"] - target),
                    -candidate["score"],
                    str((candidate.get("row") or {}).get("name") or ""),
                ),
                default=None,
            ),
        )

    order = {"strong": 0, "balanced": 1, "light": 2}
    return sorted(picked, key=lambda item: order[item[0]])


def _counter_card(
    *,
    tier: str,
    candidate: dict[str, Any],
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    my_weakest_position: str | None,
    opponent_need: list[str],
) -> dict[str, Any]:
    row = candidate["row"]
    give = [_slim_player(p) for p in give_players]
    get = [_slim_player(p) for p in [*get_players, row]]
    counter_delta = candidate["counter_delta"]
    band = {"strong": "hard", "balanced": "balanced", "light": "easy"}[tier]
    return {
        "tier": tier,
        "give": give,
        "get": get,
        "counter_strength": tier,
        "acceptance_band": band,
        "my_delta": counter_delta,
        "their_delta": round(-counter_delta, 2),
        "added_player": _slim_player(row),
        "rationale": _counter_rationale(
            tier=tier,
            candidate=row,
            my_weakest_position=my_weakest_position,
            weak_fit=candidate.get("weak_fit") or [],
            opponent_need=opponent_need,
        ),
    }


def _counter_rationale(
    *,
    tier: str,
    candidate: dict[str, Any],
    my_weakest_position: str | None,
    weak_fit: list[str],
    opponent_need: list[str],
) -> str:
    name = candidate.get("name") or "the add-on"
    fppg = _fppg(candidate)
    fit = weak_fit[0] if weak_fit else my_weakest_position
    if fit:
        roster_text = f"adds {fit} help"
    else:
        roster_text = "adds current-rate value"
    if opponent_need:
        need_text = f" while your give side fits their {opponent_need[0]} need"
    else:
        need_text = ""
    return f"{tier.title()} counter {roster_text} with {name} at {fppg:.1f} FP/G{need_text}."


def _letter_grade(my_delta: float) -> str:
    for threshold, grade in _GRADE_THRESHOLDS:
        if my_delta >= threshold:
            return grade
    return _GRADE_FLOOR


def _headline(deltas: dict[str, Any]) -> str:
    grade = deltas["letter_grade"]
    my_delta = deltas["my_delta"]
    age_delta = deltas["age_delta"]
    if grade in ("A+", "A", "A−"):
        verdict = "Strong current-rate edge"
    elif grade in ("B+", "B", "B−"):
        verdict = "Near-even current rate"
    elif grade == "C":
        verdict = "Current-rate deficit"
    else:
        verdict = "Large current-rate deficit"
    if age_delta is not None and age_delta <= -1:
        flavor = "you get younger"
    elif age_delta is not None and age_delta >= 1:
        flavor = "you get older"
    elif my_delta >= 0.5:
        flavor = "higher FP/G"
    elif my_delta <= -0.5:
        flavor = "lower FP/G"
    else:
        flavor = "near even"
    return f"{verdict} · {flavor}"


def _age_delta(
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
) -> float | None:
    give_ages = [a for a in (_age(p) for p in give_players) if a is not None]
    get_ages = [a for a in (_age(p) for p in get_players) if a is not None]
    if not give_ages or not get_ages:
        return None
    return round(sum(get_ages) / len(get_ages) - sum(give_ages) / len(give_ages), 1)


def _fppg(row: dict[str, Any]) -> float:
    return _number(row.get("fppg")) or 0.0


def _age(row: dict[str, Any]) -> float | None:
    age = _number(row.get("age"))
    if age is None or not math.isfinite(age):
        return None
    if age < MIN_VALID_DYNASTY_AGE or age > MAX_VALID_DYNASTY_AGE:
        return None
    source = str(row.get("age_source") or "").strip()
    if isinstance(row.get("raw"), dict) and not _trusted_age_source(source):
        return None
    return age


def _trusted_age_source(value: Any) -> bool:
    source = str(value or "").strip().casefold()
    return bool(source) and not any(token in source for token in ("unknown", "fallback", "inferred", "legacy"))


def _weak_positions(rows: list[dict[str, Any]], limit: int = 3) -> list[str]:
    by_pos: dict[str, list[float]] = {}
    for row in rows or []:
        if not isinstance(row, dict) or _is_inactive(row):
            continue
        fppg = _fppg(row)
        if fppg <= 0:
            continue
        for pos in _position_tokens(row):
            by_pos.setdefault(pos, []).append(fppg)
    averages = [
        (pos, sum(values) / len(values))
        for pos, values in by_pos.items()
        if values
    ]
    averages.sort(key=lambda item: (item[1], item[0]))
    return [pos for pos, _avg in averages[:limit]]


def _position_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("all_positions", "positions", "multi_positions", "pos", "slot"):
        value = row.get(key)
        values = value if isinstance(value, list) else str(value or "").replace("/", ",").split(",")
        for raw in values:
            token = str(raw or "").strip().upper()
            if token and token not in {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "MIN", "MINORS", "HIT", "PIT", "ALL", "UTIL"}:
                tokens.add(token)
    return tokens


def _is_inactive(row: dict[str, Any]) -> bool:
    slot = str(row.get("slot") or "").strip().upper()
    return slot in {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "INJ", "INJ RES", "MIN", "MINORS"}


def _is_unavailable(row: dict[str, Any]) -> bool:
    return _unavailable_reason(row) is not None


def _unavailable_reason(row: dict[str, Any]) -> str | None:
    """Return a stable user-facing reason when current FP/G is not actionable."""
    unavailable_slots = {
        "IL", "IL10", "IL15", "IL60", "IR", "INJ", "INJ RES", "INJURED RESERVE",
    }
    slot = str(row.get("slot") or "").strip().upper()
    if slot in unavailable_slots:
        return f"on {slot}"

    for key in ("injury", "status"):
        status = str(row.get(key) or "").strip().upper()
        if status in {"SUSP", "SUSPENDED"}:
            return "suspended"
        if status in {"OUT", "IL", "IL10", "IL15", "IL60", "IR", "INJ"}:
            return f"marked {status}"

    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    if _truthy(player.get("suspended")):
        return "suspended"
    if _truthy(player.get("injured_reserve")):
        return "on injured reserve"
    if _truthy(player.get("out")):
        return "marked OUT"
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _slim_player(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "slot": row.get("slot"),
        "positions": row.get("positions"),
        "team": row.get("team"),
        "fppg": _number(row.get("fppg")),
        "age": _number(row.get("age")),
        "age_source": row.get("age_source"),
        "injury": row.get("injury"),
    }


# ---------------------------------------------------------------------------
# AI rationale — cached
# ---------------------------------------------------------------------------

def _subject_key(give_ids: list[str], get_ids: list[str]) -> str:
    g = ",".join(sorted(str(i) for i in give_ids))
    r = ",".join(sorted(str(i) for i in get_ids))
    return f"give:{g}|get:{r}"


def _grade_prompt_context(
    snapshot_row: dict[str, Any],
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    deltas: dict[str, Any],
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_row.get("id"),
        "taken_at": snapshot_row.get("taken_at"),
        "my_give": [_slim_player(p) for p in give_players],
        "my_get": [_slim_player(p) for p in get_players],
        "letter_grade": deltas["letter_grade"],
        "my_delta": deltas["my_delta"],
        "their_delta": deltas["their_delta"],
        "fairness": deltas["fairness"],
        "age_delta": deltas["age_delta"],
        "my_give_fppg": deltas["my_give_fppg"],
        "my_get_fppg": deltas["my_get_fppg"],
    }


def _grade_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": GRADE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Explain this deterministic trade grade:\n"
            + json.dumps(context, default=str, indent=2),
        },
    ]


def _overlay_counter_rationales(
    *,
    snapshot_id: int,
    give_ids: list[str],
    get_ids: list[str],
    counters: list[dict[str, Any]],
    team_id: str,
) -> None:
    if not snapshot_id or not counters:
        return
    subject_payload = {
        "give": sorted(str(pid) for pid in give_ids),
        "get": sorted(str(pid) for pid in get_ids),
        "counters": [
            {
                "tier": c.get("tier"),
                "give": [p.get("id") for p in c.get("give") or []],
                "get": [p.get("id") for p in c.get("get") or []],
            }
            for c in counters
        ],
    }
    subject_key = _hash_context(subject_payload)
    context = {
        "counterparty_team_id": team_id,
        "counters": [
            {
                "tier": c.get("tier"),
                "counter_strength": c.get("counter_strength"),
                "acceptance_band": c.get("acceptance_band"),
                "give": c.get("give"),
                "get": c.get("get"),
                "my_delta": c.get("my_delta"),
                "deterministic_rationale": c.get("rationale"),
            }
            for c in counters
        ],
    }
    input_hash = _hash_context(context)

    cached = None
    try:
        cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_COUNTER, subject_key)
    except Exception as exc:
        log.warning("Trade counter cache read failed for %s/%s: %s", snapshot_id, subject_key, exc)

    parsed: dict[str, str] = {}
    if cached and cached.get("input_hash") == input_hash and cached.get("text"):
        parsed = _parse_counter_rationales(str(cached.get("text") or ""))
    else:
        try:
            text, model = sandlot_skipper.SkipperClient().complete(
                _counter_messages(context),
                max_tokens=260,
                model_order=sandlot_skipper.default_model_order(),
            )
            parsed = _parse_counter_rationales(text)
            if parsed:
                sandlot_db.set_ai_brief(
                    snapshot_id,
                    BRIEF_TYPE_COUNTER,
                    subject_key,
                    json.dumps(parsed, ensure_ascii=True, sort_keys=True),
                    model,
                    input_hash,
                )
        except Exception as exc:
            log.warning("Trade counter AI failed for %s/%s: %s", snapshot_id, subject_key, exc)

    for counter in counters:
        rationale = parsed.get(str(counter.get("tier")))
        if rationale:
            counter["rationale"] = rationale


def _counter_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": COUNTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Explain these already-ranked deterministic trade counters:\n"
            + json.dumps(context, default=str, indent=2),
        },
    ]


def _parse_counter_rationales(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except Exception:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except Exception:
                parsed = None
    out: dict[str, str] = {}
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            tier = str(item.get("tier") or "").strip().lower()
            rationale = str(item.get("rationale") or "").strip()
            if tier in {"strong", "balanced", "light"} and rationale and "%" not in rationale:
                out[tier] = rationale[:500]
    elif isinstance(parsed, dict):
        for tier in ("strong", "balanced", "light"):
            rationale = str(parsed.get(tier) or "").strip()
            if rationale and "%" not in rationale:
                out[tier] = rationale[:500]
    return out


def _hash_context(context: dict[str, Any]) -> str:
    body = json.dumps(context, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_or_generate_rationale(
    *,
    snapshot_id: int,
    subject_key: str,
    input_hash: str,
    context: dict[str, Any],
) -> tuple[str, str, bool]:
    """Returns (text, model, cached). Falls back to deterministic on AI errors."""
    cached = None
    try:
        cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_GRADE, subject_key)
    except Exception as exc:
        log.warning("Trade grade cache read failed for %s/%s: %s",
                    snapshot_id, subject_key, exc)
    if cached and cached.get("input_hash") == input_hash and cached.get("text"):
        return str(cached["text"]).strip(), str(cached.get("model") or ""), True

    try:
        text, model = sandlot_skipper.SkipperClient().complete(
            _grade_messages(context),
            max_tokens=160,
            model_order=sandlot_skipper.default_model_order(),
        )
        text = (text or "").strip()
        if not text:
            raise RuntimeError("empty AI response")
    except Exception as exc:
        log.warning("Trade grade AI failed for %s/%s: %s",
                    snapshot_id, subject_key, exc)
        return _fallback_rationale(context), "", False

    try:
        sandlot_db.set_ai_brief(
            snapshot_id, BRIEF_TYPE_GRADE, subject_key, text, model, input_hash
        )
    except Exception as exc:
        log.warning("Trade grade cache write failed for %s/%s: %s",
                    snapshot_id, subject_key, exc)
    return text, model, False


def _fallback_rationale(context: dict[str, Any]) -> str:
    """Deterministic prose for when AI is unavailable. Single sentence."""
    give = ", ".join(p.get("name") or "" for p in context.get("my_give") or []) or "the give side"
    get = ", ".join(p.get("name") or "" for p in context.get("my_get") or []) or "the get side"
    my_delta = context.get("my_delta", 0.0)
    sign = "+" if my_delta >= 0 else ""
    return f"You give {give} and get {get} for a net {sign}{my_delta} FP/G from the current snapshot."
