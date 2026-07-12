"""Immutable recommendation receipts for Sandlot's trust and outcome loop."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sandlot_autopsy import PITCHER_TOKENS


MONDAY_LINEUP_BUILDER_VERSION = "monday_lineup_v1"
TEAM_RESULT_SCORING_VERSION = "team_result_v1"
COUNTERFACTUAL_LINEUP_SCORING_VERSION = "counterfactual_lineup_v1"
COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION = "fantrax_period_lineup_v2"
TEAM_RESULT_FINALIZATION_GRACE_DAYS = 8
ET = ZoneInfo("America/New_York")

SLOT_POSITION_IDS = {
    "C": {"001"},
    "1B": {"002"},
    "2B": {"003"},
    "3B": {"004"},
    "SS": {"005"},
    "OF": {"012"},
    "UT": {"014"},
    "SP": {"015"},
    "RP": {"016"},
    "P": {"015", "016"},
}


def build_monday_lineup_receipt(
    *,
    snapshot: dict[str, Any],
    week_start: date,
    week_end: date,
    result: dict[str, Any],
    entries: list[dict[str, Any]],
    current_active: list[dict[str, Any]],
    current_total: float,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build one versioned, deterministic receipt from decision-time inputs."""
    snapshot = copy.deepcopy(snapshot)
    entries = copy.deepcopy(entries)
    current_active = copy.deepcopy(current_active)
    result = copy.deepcopy(result)
    generated_at = _utc_datetime(generated_at or datetime.now(timezone.utc))

    snapshot_id = _required_int(snapshot.get("id"), "snapshot.id")
    league_id = _required_text(snapshot.get("league_id"), "snapshot.league_id")
    team_id = _required_text(snapshot.get("team_id"), "snapshot.team_id")
    snapshot_taken_at = _utc_datetime(snapshot.get("taken_at"))
    scope_key = f"{league_id}:{team_id}:monday_lineup:{week_start.isoformat()}"
    proposal_id = scope_key

    normalized_entries = sorted(
        (_normalized_entry(entry) for entry in entries),
        key=lambda entry: (entry["id"], entry["name"]),
    )
    entry_by_name = {}
    for entry in normalized_entries:
        previous = entry_by_name.get(entry["name"])
        if previous and previous["id"] != entry["id"]:
            raise ValueError(f"duplicate roster player name cannot be receipted: {entry['name']!r}")
        entry_by_name[entry["name"]] = entry
    proposal_assignment = _normalized_assignment(result.get("lineup"), entry_by_name)
    baseline_assignment = sorted(
        (
            {
                "slot": _required_text(entry.get("slot"), "baseline slot").upper(),
                "player_id": _required_text(entry.get("id"), "baseline player id"),
                "player_name": _required_text(entry.get("name"), "baseline player name"),
                "projected_points": _finite_number(
                    entry.get("assigned_projection", entry.get("proj")),
                    "baseline projected points",
                ),
            }
            for entry in current_active
        ),
        key=lambda item: (item["slot"], item["player_id"]),
    )
    projected_value = _finite_number(result.get("projected_total"), "projected total")
    baseline_value = _finite_number(current_total, "current total")
    projected_gain = _finite_number(projected_value - baseline_value, "projected gain")

    evidence = {
        "builder_version": MONDAY_LINEUP_BUILDER_VERSION,
        "snapshot": {
            "id": snapshot_id,
            "taken_at": snapshot_taken_at.isoformat(),
            "source": str(snapshot.get("source") or "").strip() or None,
            "status": str(snapshot.get("status") or "").strip() or None,
        },
        "league_id": league_id,
        "team_id": team_id,
        "season": week_start.year,
        "period": {"start": week_start.isoformat(), "end": week_end.isoformat()},
        "evaluation": {
            "horizon": "scoring_week",
            "metric_name": "league_fantasy_points",
            "metric_unit": "points",
            "baseline_value": baseline_value,
            "projected_value": projected_value,
            "projected_gain": projected_gain,
        },
        "baseline_assignment": baseline_assignment,
        "proposed_assignment": proposal_assignment,
        "unfilled_slots": sorted(str(slot) for slot in (result.get("unfilled") or [])),
        "projection_inputs": normalized_entries,
    }
    input_hash = _sha256(evidence)
    expires_at = datetime.combine(week_end + timedelta(days=1), time.min, tzinfo=ET).astimezone(timezone.utc)

    return {
        "receipt_id": f"monday-lineup:{input_hash}",
        "builder_version": MONDAY_LINEUP_BUILDER_VERSION,
        "scope_key": scope_key,
        "source": "monday_lineup",
        "action_type": "lineup_plan",
        "league_id": league_id,
        "team_id": team_id,
        "season": week_start.year,
        "period_start": week_start,
        "period_end": week_end,
        "proposal_id": proposal_id,
        "input_hash": input_hash,
        "snapshot_id": snapshot_id,
        "recommendation": evidence,
        "evaluation_horizon": "scoring_week",
        "metric_name": "league_fantasy_points",
        "metric_unit": "points",
        "baseline_value": baseline_value,
        "projected_value": projected_value,
        "projected_gain": projected_gain,
        "generated_at": generated_at,
        "expires_at": expires_at,
    }


def immutable_receipt_fields(receipt: dict[str, Any]) -> dict[str, Any]:
    """Fields that must match when a deterministic receipt id is replayed."""
    keys = (
        "receipt_id",
        "builder_version",
        "scope_key",
        "source",
        "action_type",
        "league_id",
        "team_id",
        "season",
        "period_start",
        "period_end",
        "proposal_id",
        "input_hash",
        "recommendation",
        "evaluation_horizon",
        "metric_name",
        "metric_unit",
        "baseline_value",
        "projected_value",
        "projected_gain",
    )
    return {key: receipt.get(key) for key in keys}


def build_team_result_outcome(
    *,
    receipt: dict[str, Any],
    snapshot: dict[str, Any],
    snapshot_id: int,
    snapshot_taken_at: datetime | str,
) -> dict[str, Any] | None:
    """Build honest forecast telemetry from one exact completed team result.

    This scorer deliberately does not claim that the recommended lineup was
    used.  Per-player period scoring and lineup participation are required
    before Sandlot can calculate a realized counterfactual gain.
    """
    league_id = _required_text(receipt.get("league_id"), "receipt league id")
    team_id = _required_text(receipt.get("team_id"), "receipt team id")
    if str(snapshot.get("league_id") or "").strip() != league_id:
        return None
    if str(snapshot.get("team_id") or "").strip() != team_id:
        return None

    period_start = _iso_date(receipt.get("period_start"), "receipt period start")
    period_end = _iso_date(receipt.get("period_end"), "receipt period end")
    matches = []
    matchup = snapshot.get("matchup")
    if isinstance(matchup, dict):
        candidates = [matchup]
        latest_completed = matchup.get("latest_completed")
        if isinstance(latest_completed, dict):
            candidates.append(latest_completed)
        for candidate in candidates:
            if not candidate.get("complete"):
                continue
            try:
                candidate_start = _iso_date(candidate.get("start"), "matchup period start")
                candidate_end = _iso_date(candidate.get("end"), "matchup period end")
            except ValueError:
                continue
            if candidate_start == period_start and candidate_end == period_end:
                matches.append(candidate)
    if not matches:
        return None
    normalized_matches = []
    for item in matches:
        if str(item.get("source") or "") != "fantrax_schedule":
            raise ValueError("Completed matchup source is not authoritative")
        if str(item.get("score_state") or "") != "live_or_final":
            raise ValueError("Completed matchup score is not final")
        if str(item.get("my_team_id") or "") != team_id:
            raise ValueError("Completed matchup team does not match the receipt")
        normalized_matches.append({
            "matchup_key": _required_text(item.get("matchup_key"), "completed matchup key"),
            "period_number": _required_text(
                item.get("period_number") or item.get("period_id"), "completed matchup period number"
            ),
            "my_team_id": team_id,
            "my_score": _finite_number(item.get("my_score"), "completed team score"),
            "source": "fantrax_schedule",
            "score_state": "live_or_final",
        })
    if len({_sha256(item) for item in normalized_matches}) != 1:
        raise ValueError("Completed matchup evidence is ambiguous for the receipt period")

    normalized_result = normalized_matches[0]
    actual_value = normalized_result["my_score"]
    projected_value = _finite_number(receipt.get("projected_value"), "receipt projected value")
    residual = round(actual_value - projected_value, 4)
    evidence = {
        "receipt_id": _required_text(receipt.get("receipt_id"), "receipt id"),
        "input_hash": _required_text(receipt.get("input_hash"), "receipt input hash"),
        "league_id": league_id,
        "team_id": team_id,
        "measurement_scope": "observed_team_total",
        "adherence_state": "unverified",
        "counterfactual_state": "unavailable",
        "counterfactual_reason": "per_player_period_scoring_and_lineup_participation_not_ingested",
        "source": {
            "snapshot_id": int(snapshot_id),
            "snapshot_taken_at": _utc_datetime(snapshot_taken_at).isoformat(),
            "matchup_key": normalized_result["matchup_key"],
            "period_number": normalized_result["period_number"],
            "score_state": normalized_result["score_state"],
        },
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "observed_team_score": actual_value,
        "projected_team_total": projected_value,
        "team_total_residual": residual,
        "absolute_error": abs(residual),
        "signed_bias": residual,
    }
    return {
        "scoring_version": TEAM_RESULT_SCORING_VERSION,
        "actual_value": actual_value,
        "actual_baseline": None,
        "actual_gain": None,
        "outcome_evidence": {**evidence, "evidence_hash": _sha256(evidence)},
    }


def team_result_evidence_hash(evidence: dict[str, Any]) -> str:
    """Return the canonical hash for stored team-result evidence."""
    canonical = {key: value for key, value in evidence.items() if key != "evidence_hash"}
    return _sha256(canonical)


def build_counterfactual_lineup_evaluation(
    *, receipt: dict[str, Any], period_evidence: dict[str, Any]
) -> dict[str, Any]:
    """Score the receipt's static baseline and proposal against realized player FPts.

    This is a retrospective counterfactual, not causal lift and not proof that the
    recommendation was executed. It is intentionally limited to one verified
    weekly lineup window because Fantrax only exposes period-level player FPts.
    """
    from fantrax_data import lineup_period_evidence_hash

    if period_evidence.get("evidence_version") != COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION:
        raise ValueError("counterfactual requires exact v2 lineup period evidence")
    evidence_hash = _required_text(period_evidence.get("evidence_hash"), "period evidence hash")
    if evidence_hash != lineup_period_evidence_hash(period_evidence):
        raise ValueError("lineup period evidence hash is invalid")
    capability = period_evidence.get("counterfactual_capability")
    if not isinstance(capability, dict) or capability.get("eligible") is not True:
        reason = capability.get("reason") if isinstance(capability, dict) else "missing_capability"
        raise ValueError(f"lineup period evidence is not counterfactual eligible: {reason}")
    participation = period_evidence.get("participation")
    if not isinstance(participation, dict) or participation.get("window_count") != 1:
        raise ValueError("counterfactual requires exactly one stable lineup window")
    if participation.get("stable_within_windows") is not True:
        raise ValueError("counterfactual requires stable daily participation")

    period = period_evidence.get("period") if isinstance(period_evidence.get("period"), dict) else {}
    bindings = {
        "league_id": _required_text(receipt.get("league_id"), "receipt league id"),
        "team_id": _required_text(receipt.get("team_id"), "receipt team id"),
        "period_start": _iso_date(receipt.get("period_start"), "receipt period start").isoformat(),
        "period_end": _iso_date(receipt.get("period_end"), "receipt period end").isoformat(),
    }
    if (
        period_evidence.get("league_id") != bindings["league_id"]
        or period_evidence.get("team_id") != bindings["team_id"]
        or str(period.get("start")) != bindings["period_start"]
        or str(period.get("end")) != bindings["period_end"]
    ):
        raise ValueError("lineup period evidence does not match the receipt")

    recommendation = receipt.get("recommendation")
    if not isinstance(recommendation, dict):
        raise ValueError("receipt recommendation is unavailable")
    inputs = recommendation.get("projection_inputs")
    if not isinstance(inputs, list):
        raise ValueError("receipt projection inputs are unavailable")
    input_by_id: dict[str, dict[str, Any]] = {}
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("receipt projection input is invalid")
        player_id = _required_text(item.get("id"), "projection player id")
        if player_id in input_by_id:
            raise ValueError("receipt projection inputs contain duplicate player identity")
        input_by_id[player_id] = item

    archived_players = period_evidence.get("players")
    if not isinstance(archived_players, list) or not archived_players:
        raise ValueError("period player evidence is unavailable")
    player_by_role: dict[tuple[str, str], dict[str, Any]] = {}
    for item in archived_players:
        if not isinstance(item, dict):
            raise ValueError("period player evidence is invalid")
        key = (
            _required_text(item.get("player_id"), "period player id"),
            _required_text(item.get("scoring_role"), "period scoring role"),
        )
        if key in player_by_role:
            raise ValueError("period evidence contains duplicate player role")
        player_by_role[key] = item

    baseline = _score_counterfactual_assignment(
        recommendation.get("baseline_assignment"), input_by_id, player_by_role, label="baseline"
    )
    proposed = _score_counterfactual_assignment(
        recommendation.get("proposed_assignment"), input_by_id, player_by_role, label="proposed"
    )
    unfilled = [str(slot).strip().upper() for slot in (recommendation.get("unfilled_slots") or [])]
    if any(slot not in SLOT_POSITION_IDS for slot in unfilled):
        raise ValueError("receipt unfilled slots are invalid")

    days = participation.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("daily participation evidence is unavailable")
    last_day = days[-1]
    if not isinstance(last_day, dict) or not isinstance(last_day.get("players"), list):
        raise ValueError("final daily participation evidence is invalid")
    actual_active: set[tuple[str, str]] = set()
    actual_slots: dict[tuple[str, str], str | None] = {}
    for item in last_day["players"]:
        if not isinstance(item, dict):
            raise ValueError("daily player participation is invalid")
        key = (_required_text(item.get("player_id"), "daily player id"), _required_text(item.get("scoring_role"), "daily scoring role"))
        if key in actual_slots:
            raise ValueError("daily participation contains duplicate player role")
        actual_slots[key] = str(item.get("raw_pos_id") or "") or None
        if item.get("state") == "active":
            actual_active.add(key)
        elif item.get("state") != "bench":
            raise ValueError("daily participation contains an unknown state")
    if set(actual_slots) != set(player_by_role):
        raise ValueError("daily participation does not cover the exact period player roles")
    active_player_ids = [player_id for player_id, _role in actual_active]
    if len(active_player_ids) != len(set(active_player_ids)):
        raise ValueError("daily participation has ambiguous active two-way scoring")

    proposed_set = {(item["player_id"], item["scoring_role"]) for item in proposed["assignments"]}
    baseline_set = {(item["player_id"], item["scoring_role"]) for item in baseline["assignments"]}
    active_match = "proposed" if actual_active == proposed_set else "baseline" if actual_active == baseline_set else "other"
    slot_match = _actual_slot_match(actual_active, actual_slots, proposed, baseline)
    decision_state = str(receipt.get("decision_state") or "pending")
    decision_alignment = (
        "accepted_proposal_observed"
        if decision_state == "accepted" and active_match == "proposed"
        else "not_established"
    )
    observed_total = _decimal(period_evidence.get("observed_team_total"), "observed team total")
    gain = proposed["total"] - baseline["total"]
    metrics = {
        "counterfactual_baseline_total": _decimal_number(baseline["total"]),
        "counterfactual_proposed_total": _decimal_number(proposed["total"]),
        "counterfactual_gain": _decimal_number(gain),
        "observed_team_total": _decimal_number(observed_total),
    }
    evaluation_evidence = {
        "receipt_id": _required_text(receipt.get("receipt_id"), "receipt id"),
        "scoring_version": COUNTERFACTUAL_LINEUP_SCORING_VERSION,
        "input_hash": _required_text(receipt.get("input_hash"), "receipt input hash"),
        "league_id": bindings["league_id"],
        "team_id": bindings["team_id"],
        "period": {"start": bindings["period_start"], "end": bindings["period_end"], "number": str(period.get("number"))},
        "source_evidence": {
            "version": COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION,
            "hash": evidence_hash,
        },
        "measurement_scope": "retrospective_static_lineup_counterfactual",
        "causal_lift_claimed": False,
        "plan_execution_claimed": False,
        "autopilot_eligible": False,
        "decision_state": decision_state,
        "actual_assignment_match": active_match,
        "actual_slot_match": slot_match,
        "decision_alignment": decision_alignment,
        "baseline_assignment": baseline["assignments"],
        "proposed_assignment": proposed["assignments"],
        "unfilled_slots": unfilled,
        "metrics": metrics,
    }
    return {
        "scoring_version": COUNTERFACTUAL_LINEUP_SCORING_VERSION,
        "state": "scored",
        "source_evidence_version": COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION,
        "source_evidence_hash": evidence_hash,
        "metrics": metrics,
        "evidence": {**evaluation_evidence, "evidence_hash": _sha256(evaluation_evidence)},
    }


def counterfactual_evidence_hash(evidence: dict[str, Any]) -> str:
    canonical = {key: value for key, value in evidence.items() if key != "evidence_hash"}
    return _sha256(canonical)


def build_counterfactual_lineup_unavailable(
    *, receipt: dict[str, Any], period_evidence: dict[str, Any], detail: str
) -> dict[str, Any]:
    """Build one terminal, immutable record for incompatible archived inputs."""
    source_hash = _required_text(period_evidence.get("evidence_hash"), "period evidence hash")
    period = period_evidence.get("period") if isinstance(period_evidence.get("period"), dict) else {}
    evidence = {
        "receipt_id": _required_text(receipt.get("receipt_id"), "receipt id"),
        "scoring_version": COUNTERFACTUAL_LINEUP_SCORING_VERSION,
        "input_hash": _required_text(receipt.get("input_hash"), "receipt input hash"),
        "league_id": _required_text(receipt.get("league_id"), "receipt league id"),
        "team_id": _required_text(receipt.get("team_id"), "receipt team id"),
        "period": {
            "start": _iso_date(receipt.get("period_start"), "receipt period start").isoformat(),
            "end": _iso_date(receipt.get("period_end"), "receipt period end").isoformat(),
            "number": str(period.get("number")),
        },
        "source_evidence": {
            "version": COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION,
            "hash": source_hash,
        },
        "measurement_scope": "retrospective_static_lineup_counterfactual",
        "reason": "immutable_receipt_or_archive_incompatible",
        "detail": _required_text(detail, "counterfactual unavailable detail")[:500],
        "retryable": False,
        "autopilot_eligible": False,
        "metrics": {},
    }
    return {
        "scoring_version": COUNTERFACTUAL_LINEUP_SCORING_VERSION,
        "state": "unavailable",
        "source_evidence_version": COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION,
        "source_evidence_hash": source_hash,
        "metrics": {},
        "evidence": {**evidence, "evidence_hash": _sha256(evidence)},
    }


def _score_counterfactual_assignment(value, input_by_id, player_by_role, *, label: str):
    if not isinstance(value, list):
        raise ValueError(f"receipt {label} assignment is unavailable")
    assignments = []
    seen_players: set[str] = set()
    total = Decimal("0")
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError(f"receipt {label} assignment is invalid")
        slot = _required_text(raw.get("slot"), f"{label} slot").upper()
        player_id = _required_text(raw.get("player_id"), f"{label} player id")
        if slot not in SLOT_POSITION_IDS:
            raise ValueError(f"unsupported {label} lineup slot: {slot}")
        if player_id in seen_players:
            raise ValueError(f"receipt {label} assignment repeats a player")
        seen_players.add(player_id)
        projection_input = input_by_id.get(player_id)
        if not projection_input:
            raise ValueError(f"{label} player is absent from decision-time inputs")
        tokens = {_canonical_token(token) for token in (projection_input.get("tokens") or [])}
        if not _decision_time_slot_eligible(slot, tokens):
            raise ValueError(f"{label} player was not decision-time eligible for {slot}")
        role = "pitcher" if slot in PITCHER_TOKENS else "hitter"
        archived = player_by_role.get((player_id, role))
        if not archived:
            raise ValueError(f"{label} player role is absent from archived period evidence")
        eligible_ids = {str(value) for value in (archived.get("eligibility_pos_ids") or [])}
        if not eligible_ids or not (eligible_ids & SLOT_POSITION_IDS[slot]):
            raise ValueError(f"{label} player was not archive-eligible for {slot}")
        points = _decimal(archived.get("period_fpts"), f"{label} player period FPts")
        total += points
        assignments.append({
            "slot": slot,
            "player_id": player_id,
            "scoring_role": role,
            "period_fpts": _decimal_number(points),
        })
    return {"assignments": sorted(assignments, key=lambda item: (item["slot"], item["player_id"])), "total": total}


def _actual_slot_match(actual_active, actual_slots, proposed, baseline):
    def exact(candidate):
        assignments = candidate["assignments"]
        identities = {(item["player_id"], item["scoring_role"]) for item in assignments}
        if identities != actual_active:
            return False
        return all(actual_slots.get((item["player_id"], item["scoring_role"])) in SLOT_POSITION_IDS[item["slot"]] for item in assignments)
    if exact(proposed):
        return "proposed"
    if exact(baseline):
        return "baseline"
    return "other"


def _canonical_token(value: Any) -> str:
    token = str(value or "").strip().upper()
    return {"UTIL": "UT", "LF": "OF", "CF": "OF", "RF": "OF"}.get(token, token)


def _decision_time_slot_eligible(slot: str, tokens: set[str]) -> bool:
    if slot == "P":
        return bool(tokens & PITCHER_TOKENS)
    return slot in tokens


def _decimal(value: Any, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{label} must be finite")
    return number


def _decimal_number(value: Decimal) -> float:
    return round(float(value), 4)


def build_team_result_unavailable(
    *, receipt: dict[str, Any], snapshot: dict[str, Any], snapshot_id: int, snapshot_taken_at: datetime | str
) -> dict[str, Any] | None:
    """Terminalize evidence that is provably older than Fantrax's retained result window."""
    team_id = _required_text(receipt.get("team_id"), "receipt team id")
    if str(snapshot.get("league_id") or "") != str(receipt.get("league_id") or ""):
        return None
    if str(snapshot.get("team_id") or "") != team_id:
        return None
    target_end = _iso_date(receipt.get("period_end"), "receipt period end")
    target_start = _iso_date(receipt.get("period_start"), "receipt period start")
    captured_at = _utc_datetime(snapshot_taken_at)
    if captured_at.date() <= target_end + timedelta(days=TEAM_RESULT_FINALIZATION_GRACE_DAYS):
        return None
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
    latest = matchup.get("latest_completed") if isinstance(matchup.get("latest_completed"), dict) else None
    if not latest:
        return None
    if str(latest.get("source") or "") != "fantrax_schedule":
        return None
    if str(latest.get("score_state") or "") != "live_or_final":
        return None
    if str(latest.get("my_team_id") or "") != team_id:
        return None
    latest_end = _iso_date(latest.get("end"), "latest completed period end")
    if latest_end <= target_end:
        return None
    evidence = {
        "receipt_id": _required_text(receipt.get("receipt_id"), "receipt id"),
        "input_hash": _required_text(receipt.get("input_hash"), "receipt input hash"),
        "league_id": _required_text(receipt.get("league_id"), "receipt league id"),
        "team_id": team_id,
        "reason": "completed_period_evidence_missed_after_grace_window",
        "retryable": False,
        "grace_days": TEAM_RESULT_FINALIZATION_GRACE_DAYS,
        "period": {"start": target_start.isoformat(), "end": target_end.isoformat()},
        "source": {
            "snapshot_id": int(snapshot_id),
            "snapshot_taken_at": captured_at.isoformat(),
            "latest_completed_period_end": latest_end.isoformat(),
            "latest_completed_matchup_key": _required_text(
                latest.get("matchup_key"), "latest completed matchup key"
            ),
        },
    }
    return {**evidence, "evidence_hash": _sha256(evidence)}


def _normalized_assignment(value: Any, entry_by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    assignment = []
    for raw in value or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError("proposed assignment must contain [slot, player name] pairs")
        slot, name = raw
        name = _required_text(name, "proposed player name")
        entry = entry_by_name.get(name)
        if not entry:
            raise ValueError(f"proposed player {name!r} was absent from projection inputs")
        assignment.append({
            "slot": _required_text(slot, "proposed slot").upper(),
            "player_id": entry["id"],
            "player_name": name,
            "projected_points": _projection_for_slot(entry, str(slot)),
        })
    return sorted(assignment, key=lambda item: (item["slot"], item["player_id"]))


def _projection_for_slot(entry: dict[str, Any], slot: str) -> float:
    if str(slot).strip().upper() in PITCHER_TOKENS:
        return entry["pitcher_projected_points"]
    return entry["hitter_projected_points"]


def _normalized_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("projection input must be an object")
    return {
        "id": _required_text(entry.get("id"), "projection player id"),
        "name": _required_text(entry.get("name"), "projection player name"),
        "tokens": sorted(str(token).strip().upper() for token in (entry.get("tokens") or []) if str(token).strip()),
        "slot": str(entry.get("slot") or "").strip().upper() or None,
        "slot_source": str(entry.get("slot_source") or "").strip() or None,
        "injury": str(entry.get("injury") or "").strip().upper() or None,
        "projected_points": _finite_number(entry.get("proj"), "player projected points"),
        "hitter_projected_points": _finite_number(entry.get("hitter_proj", 0.0), "hitter projected points"),
        "pitcher_projected_points": _finite_number(entry.get("pitcher_proj", 0.0), "pitcher projected points"),
        "basis": str(entry.get("basis") or "").strip(),
    }


def _sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return round(number, 4)


def _required_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is required") from exc


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _iso_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime or ISO datetime string")
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)
