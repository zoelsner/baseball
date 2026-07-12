"""Immutable recommendation receipts for Sandlot's trust and outcome loop."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sandlot_autopsy import PITCHER_TOKENS


MONDAY_LINEUP_BUILDER_VERSION = "monday_lineup_v1"
ET = ZoneInfo("America/New_York")


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


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime or ISO datetime string")
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)
