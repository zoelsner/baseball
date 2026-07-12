"""Fail-closed control-plane helpers for supervised Fantrax actions.

This module does not import Selenium and cannot write to Fantrax.  It turns a
server-derived lineup proposal into an immutable, short-lived *dry-run*
request and validates the evidence returned by a separately authenticated
local runner.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any


DRY_RUN_MODE = "dry_run"
REQUEST_TTL_SECONDS = 120
LEASE_TTL_SECONDS = 90
TERMINAL_STATES = {"preflight_passed", "preflight_failed", "expired", "cancelled"}
BENCH_SLOTS = {"BN", "RES"}
SENSITIVE_EVIDENCE_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "password",
    "session",
    "token",
}
ALLOWED_EVIDENCE_KEYS = {
    "source",
    "target_period",
    "roster_player_count",
    "roster_ids_sha256",
    "participant_ids",
    "participant_slots",
    "eligible_destinations",
    "fantrax_click_count",
    "fantrax_write_count",
}


class ExecutionContractError(ValueError):
    """The proposed execution request is unsafe, stale, or malformed."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_hashed_bearer(
    authorization: str | None,
    *,
    digest_env: str,
) -> None:
    """Validate a bearer secret against a configured SHA-256 digest.

    Only the digest is configured in the web service.  The plaintext owner or
    runner secret remains with the calling client.
    """
    expected = str(os.environ.get(digest_env) or "").strip().casefold()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ExecutionContractError(f"{digest_env} is not configured")
    prefix = "bearer "
    raw = str(authorization or "")
    if not raw.casefold().startswith(prefix):
        raise PermissionError("Missing bearer credential")
    provided = raw[len(prefix):].strip()
    if not provided or not hmac.compare_digest(token_digest(provided), expected):
        raise PermissionError("Invalid bearer credential")


def dry_run_enabled() -> bool:
    return str(os.environ.get("SANDLOT_EXECUTION_DRY_RUN_ENABLED") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def distinct_role_credentials_configured() -> bool:
    owner = str(os.environ.get("SANDLOT_OWNER_ACTION_TOKEN_SHA256") or "").strip().casefold()
    runner = str(os.environ.get("SANDLOT_RUNNER_TOKEN_SHA256") or "").strip().casefold()

    def valid(value: str) -> bool:
        return len(value) == 64 and all(char in "0123456789abcdef" for char in value)

    return valid(owner) and valid(runner) and not hmac.compare_digest(owner, runner)


def prepare_dry_run_request(
    *,
    snapshot_row: dict[str, Any],
    action: dict[str, Any],
    submitted: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the immutable DB payload for one exact dry-run request."""
    now = _aware(now or utc_now())
    review = action.get("review") if isinstance(action.get("review"), dict) else {}
    contract = review.get("contract") if isinstance(review.get("contract"), dict) else {}
    confirmation = contract.get("confirmation") if isinstance(contract.get("confirmation"), dict) else {}
    expected = confirmation.get("expected") if isinstance(confirmation.get("expected"), dict) else {}

    if submitted.get("mode") != DRY_RUN_MODE:
        raise ExecutionContractError("Only dry_run execution requests are supported")
    if review.get("state") != "reviewable":
        raise ExecutionContractError("Proposal is not reviewable")
    if contract.get("type") != "lineup_swap" or contract.get("action") != "change_slot":
        raise ExecutionContractError("Only lineup_swap proposals are supported")
    if contract.get("executable") is not False or contract.get("writes_enabled") is not False:
        raise ExecutionContractError("Proposal contract is not read-only")
    if contract.get("requires_multi_step") is not False:
        raise ExecutionContractError("Only a simple two-player lineup swap can be dry-run")

    identity = {
        "proposal_id": submitted.get("proposal_id"),
        "snapshot_id": submitted.get("snapshot_id"),
        "input_hash": submitted.get("input_hash"),
    }
    canonical_identity = {
        "proposal_id": expected.get("proposal_id"),
        "snapshot_id": expected.get("snapshot_id"),
        "input_hash": expected.get("input_hash"),
    }
    if identity != canonical_identity:
        raise ExecutionContractError("Proposal identity is stale or does not match the server contract")
    if submitted.get("confirmation") != expected:
        raise ExecutionContractError("Exact confirmation does not match the server contract")

    slot_moves = expected.get("slot_moves")
    if not _is_simple_swap(slot_moves):
        raise ExecutionContractError("Proposal is not an atomic two-player reserve/active swap")
    if contract.get("slot_moves") != slot_moves or review.get("slot_moves") != slot_moves:
        raise ExecutionContractError("Proposal slot mappings are internally inconsistent")
    if contract.get("target_period") != expected.get("target_period"):
        raise ExecutionContractError("Proposal target period is internally inconsistent")

    snapshot_id = int(snapshot_row.get("id") or 0)
    if snapshot_id != int(expected.get("snapshot_id") or 0):
        raise ExecutionContractError("Latest snapshot no longer matches the confirmed proposal")
    roster_ids = _roster_ids(snapshot_row)
    participant_ids = {str(move.get("player_id") or "") for move in slot_moves}
    if not roster_ids or not participant_ids.issubset(set(roster_ids)):
        raise ExecutionContractError("Proposal participants are not all present on the latest roster")

    deadline = _parse_datetime(((contract.get("movability") or {}).get("deadline") or {}).get("at"))
    if deadline is None:
        raise ExecutionContractError("Proposal has no exact action deadline")
    if deadline <= now:
        raise ExecutionContractError("Proposal deadline has passed")

    request_expires_at = min(now + timedelta(seconds=REQUEST_TTL_SECONDS), deadline)
    if request_expires_at <= now:
        raise ExecutionContractError("Proposal cannot be claimed before its deadline")

    immutable_contract = json.loads(json.dumps(contract, sort_keys=True, default=_json_default))
    expected_roster_digest = roster_ids_digest(roster_ids)
    return {
        "request_id": "xreq_" + secrets.token_urlsafe(18),
        "mode": DRY_RUN_MODE,
        "snapshot_id": snapshot_id,
        "proposal_id": str(expected["proposal_id"]),
        "input_hash": str(expected["input_hash"]),
        "contract": immutable_contract,
        "expected_roster_ids": roster_ids,
        "state": "pending",
        "expires_at": request_expires_at,
        "writes_enabled": False,
        "safety": {
            "action_scope": "lineup_only",
            "roster_departures": [],
            "protected_players_may_leave_roster": False,
            "visible_runner_required": True,
            "fantrax_clicks_allowed": False,
            "expected_roster_ids_sha256": expected_roster_digest,
        },
    }


def new_lease() -> tuple[str, str]:
    plaintext = secrets.token_urlsafe(24)
    return plaintext, token_digest(plaintext)


def lease_expiry(*, request_expires_at: datetime, now: datetime | None = None) -> datetime:
    now = _aware(now or utc_now())
    return min(_aware(request_expires_at), now + timedelta(seconds=LEASE_TTL_SECONDS))


def validate_preflight_report(
    report: dict[str, Any],
    *,
    request_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outcome = report.get("outcome")
    if outcome not in {"passed", "failed"}:
        raise ExecutionContractError("Preflight outcome must be passed or failed")
    if report.get("writes_attempted") is not False:
        raise ExecutionContractError("Dry-run preflight must report writes_attempted=false")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > 32:
        raise ExecutionContractError("Preflight must include 1 to 32 checks")
    normalized_checks: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            raise ExecutionContractError("Every preflight check must be an object")
        key = str(check.get("key") or "").strip()
        state = str(check.get("state") or "").strip()
        if not key or state not in {"passed", "failed"}:
            raise ExecutionContractError("Every preflight check needs a key and passed/failed state")
        normalized_checks.append({
            "key": key[:80],
            "state": state,
            "detail": "Live check passed." if state == "passed" else "Live check failed.",
        })
    keys = [check["key"] for check in normalized_checks]
    if len(keys) != len(set(keys)):
        raise ExecutionContractError("Preflight check keys must be unique")
    any_failed = any(check["state"] == "failed" for check in normalized_checks)
    evidence = report.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise ExecutionContractError("Preflight evidence must be an object")
    _reject_sensitive_evidence(evidence)
    unknown_evidence = sorted(set(evidence) - ALLOWED_EVIDENCE_KEYS)
    if unknown_evidence:
        raise ExecutionContractError("Unsupported preflight evidence fields: " + ", ".join(unknown_evidence))
    if evidence.get("fantrax_click_count") != 0 or evidence.get("fantrax_write_count") != 0:
        raise ExecutionContractError("Dry-run evidence must prove zero Fantrax clicks and writes")
    if evidence.get("source") != "visible_fantrax_dom+authenticated_read_api":
        raise ExecutionContractError("Preflight evidence source is not the visible read-only runner")
    read_failure = keys == ["live_read"] and normalized_checks[0]["state"] == "failed"
    if read_failure:
        if outcome != "failed":
            raise ExecutionContractError("A live-read failure cannot report a passing outcome")
        allowed_failure_evidence = {"source", "fantrax_click_count", "fantrax_write_count"}
        if set(evidence) - allowed_failure_evidence:
            raise ExecutionContractError("Live-read failure evidence contains unobserved live invariants")
    else:
        if request_row is None:
            raise ExecutionContractError("A claimed request is required for contract-specific preflight")
        required_keys = required_preflight_check_keys(request_row)
        if set(keys) != required_keys or len(keys) != len(required_keys):
            raise ExecutionContractError("Preflight report does not contain the exact required live checks")
        if (outcome == "passed" and any_failed) or (outcome == "failed" and not any_failed):
            raise ExecutionContractError("Preflight outcome does not agree with its checks")
        participant_ids = evidence.get("participant_ids", [])
        contract = request_row.get("contract") if isinstance(request_row.get("contract"), dict) else {}
        expected_participants = [
            str(move.get("player_id") or "")
            for move in contract.get("slot_moves") or []
            if isinstance(move, dict)
        ]
        if participant_ids != expected_participants:
            raise ExecutionContractError("Preflight participants do not match the claimed contract")
        roster_digest = str(evidence.get("roster_ids_sha256") or "").casefold()
        if len(roster_digest) != 64 or any(char not in "0123456789abcdef" for char in roster_digest):
            raise ExecutionContractError("Preflight roster digest is missing or malformed")
        if outcome == "passed":
            target_period = (contract.get("target_period") or {}).get("period_number")
            try:
                period_matches = int(evidence.get("target_period")) == int(target_period)
            except (TypeError, ValueError):
                period_matches = False
            if not period_matches:
                raise ExecutionContractError("Passing preflight period does not match the claimed contract")
            expected_roster_ids = request_row.get("expected_roster_ids") or []
            if evidence.get("roster_player_count") != len(expected_roster_ids):
                raise ExecutionContractError("Passing preflight roster count does not match the claimed contract")
            if not hmac.compare_digest(roster_digest, roster_ids_digest(expected_roster_ids)):
                raise ExecutionContractError("Passing preflight roster membership does not match the claimed contract")
            observed_slots = evidence.get("participant_slots")
            eligible_destinations = evidence.get("eligible_destinations")
            if not isinstance(observed_slots, dict) or set(observed_slots) != set(expected_participants):
                raise ExecutionContractError("Passing preflight participant slots are incomplete")
            if not isinstance(eligible_destinations, dict) or set(eligible_destinations) != set(expected_participants):
                raise ExecutionContractError("Passing preflight destination evidence is incomplete")
            for move in contract.get("slot_moves") or []:
                player_id = str(move.get("player_id") or "")
                if _normalized_slot(observed_slots.get(player_id)) != _normalized_slot(move.get("from_slot")):
                    raise ExecutionContractError("Passing preflight participant slot does not match the contract")
                destinations = {
                    _normalized_slot(value)
                    for value in eligible_destinations.get(player_id) or []
                }
                if _normalized_slot(move.get("to_slot")) not in destinations:
                    raise ExecutionContractError("Passing preflight destination eligibility does not match the contract")
            if any(check["state"] != "passed" for check in normalized_checks):
                raise ExecutionContractError("Passing preflight must pass every required live check")
    if outcome == "failed" and not any_failed:
        raise ExecutionContractError("Failed preflight must contain a failed check")
    if request_row is not None:
        _validate_observed_at(report.get("observed_at"), request_row)
    evidence = {key: value for key, value in evidence.items() if key in ALLOWED_EVIDENCE_KEYS}
    if len(json.dumps(evidence, sort_keys=True, default=str)) > 32_000:
        raise ExecutionContractError("Preflight evidence is too large")
    return {
        "outcome": outcome,
        "checks": normalized_checks,
        "evidence": evidence,
        "writes_attempted": False,
        "observed_at": (
            _aware(report["observed_at"]).isoformat()
            if isinstance(report.get("observed_at"), datetime)
            else str(report.get("observed_at") or "")
        ),
    }


def required_preflight_check_keys(request_row: dict[str, Any]) -> set[str]:
    contract = request_row.get("contract") if isinstance(request_row.get("contract"), dict) else {}
    participant_ids = [
        str(move.get("player_id") or "")
        for move in contract.get("slot_moves") or []
        if isinstance(move, dict) and move.get("player_id")
    ]
    keys = {"mode", "target_period", "roster_set", "roster_departures", "deadline", "atomic_swap"}
    for player_id in participant_ids:
        keys.update({
            f"player_present:{player_id}",
            f"from_slot:{player_id}",
            f"destination:{player_id}",
        })
    return keys


def roster_ids_digest(roster_ids: Any) -> str:
    normalized = sorted({str(value) for value in roster_ids or [] if value})
    canonical = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_slot(value: Any) -> str:
    slot = str(value or "").strip().upper()
    return "RES" if slot == "BN" else slot


def public_request(row: dict[str, Any], *, include_contract: bool) -> dict[str, Any]:
    payload = {
        "request_id": row.get("request_id"),
        "mode": row.get("mode"),
        "snapshot_id": row.get("snapshot_id"),
        "proposal_id": row.get("proposal_id"),
        "input_hash": row.get("input_hash"),
        "state": row.get("state"),
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
        "claimed_at": row.get("claimed_at"),
        "completed_at": row.get("completed_at"),
        "failure_reason": row.get("failure_reason"),
        "evidence": row.get("evidence") or {},
        "safety": row.get("safety") or {},
        "writes_enabled": False,
    }
    if include_contract:
        payload["contract"] = row.get("contract") or {}
        payload["expected_roster_ids"] = row.get("expected_roster_ids") or []
    return payload


def _is_simple_swap(slot_moves: Any) -> bool:
    if not isinstance(slot_moves, list) or len(slot_moves) != 2:
        return False
    if not all(isinstance(move, dict) for move in slot_moves):
        return False
    if [move.get("order") for move in slot_moves] != [1, 2]:
        return False
    player_ids = [str(move.get("player_id") or "") for move in slot_moves]
    if not all(player_ids) or len(set(player_ids)) != 2:
        return False
    first_from = str(slot_moves[0].get("from_slot") or "").upper()
    first_to = str(slot_moves[0].get("to_slot") or "").upper()
    second_from = str(slot_moves[1].get("from_slot") or "").upper()
    second_to = str(slot_moves[1].get("to_slot") or "").upper()
    return (
        first_from in BENCH_SLOTS
        and second_to == first_from
        and first_to == second_from
        and first_to not in BENCH_SLOTS
    )


def _roster_ids(snapshot_row: dict[str, Any]) -> list[str]:
    data = snapshot_row.get("data") if isinstance(snapshot_row.get("data"), dict) else {}
    roster = data.get("roster") if isinstance(data.get("roster"), dict) else {}
    rows = roster.get("rows") if isinstance(roster.get("rows"), list) else []
    ids = {str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")}
    return sorted(ids)


def _reject_sensitive_evidence(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if any(sensitive in normalized for sensitive in SENSITIVE_EVIDENCE_KEYS):
                raise ExecutionContractError(
                    "Sensitive data is not allowed in preflight evidence: " + ".".join((*path, str(key)))
                )
            _reject_sensitive_evidence(child, path=(*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_evidence(child, path=(*path, str(index)))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _validate_observed_at(value: Any, request_row: dict[str, Any]) -> None:
    observed = _parse_datetime(value)
    claimed_at = _parse_datetime(request_row.get("claimed_at"))
    lease_expires_at = _parse_datetime(request_row.get("lease_expires_at"))
    request_expires_at = _parse_datetime(request_row.get("expires_at"))
    if not observed or not claimed_at or not lease_expires_at or not request_expires_at:
        raise ExecutionContractError("Preflight observation or claim timing is incomplete")
    skew = timedelta(seconds=15)
    terminal = min(lease_expires_at, request_expires_at)
    if observed < claimed_at - skew or observed > terminal + skew:
        raise ExecutionContractError("Preflight observation is outside the live claim window")


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
