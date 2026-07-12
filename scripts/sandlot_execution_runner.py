#!/usr/bin/env python3
"""Visible, local, zero-click Fantrax preflight runner.

The runner may navigate a headful browser and call authenticated read APIs. It
contains no click, form-submit, or Fantrax mutation path.  A claimed request is
evaluated once and reported once; failures are terminal for that request.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import fantrax_data
import fantrax_dom
import sandlot_execution


BENCH_SLOTS = {"BN", "RES"}


class LiveReader(Protocol):
    def read(self, request_payload: dict[str, Any]) -> dict[str, Any]: ...


class FantraxVisibleReader:
    """Read live Fantrax truth through a visible page plus read-only APIs."""

    def read(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        contract = request_payload.get("contract") or {}
        league_id = str(contract.get("league_id") or "")
        team_id = str(contract.get("team_id") or "")
        if not league_id or not team_id:
            raise RuntimeError("Execution contract is missing league or team identity")
        cookies = auth._load_cookies()
        if not cookies:
            raise RuntimeError("Local Fantrax cookies are unavailable; log in locally before preflight")

        # This helper only calls driver.get and reads page_source. It never
        # clicks, types, submits, or invokes a Fantrax mutation endpoint.
        html = fantrax_dom.capture_roster_html(
            cookies,
            league_id=league_id,
            team_id=team_id,
            headful=True,
        )
        dom_slots = fantrax_dom.lineup_slots_from_html(html)
        live_snapshot = fantrax_data.collect_all(
            auth._build_session(cookies),
            league_id,
            team_id,
        )
        return {"snapshot": live_snapshot, "dom_slots": dom_slots}


class BrowserEvidenceReader:
    """Use a fresh, non-secret visible-DOM evidence artifact instead of cookies."""

    def __init__(self, path: str | Path, *, wait_seconds: float = 60.0):
        self.path = Path(path)
        self.wait_seconds = max(0.0, wait_seconds)

    def read(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        binding = _browser_evidence_binding(request_payload)
        sidecar = Path(str(self.path) + ".request.json")
        temporary = Path(str(sidecar) + ".tmp")
        temporary.write_text(json.dumps({**binding, "claimed_at": request_payload.get("claimed_at")}, sort_keys=True), encoding="utf-8")
        os.replace(temporary, sidecar)
        deadline = time.monotonic() + self.wait_seconds
        last_error: Exception | None = None
        while True:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError("Browser evidence must be a JSON object")
                return live_from_visible_browser(request_payload, payload)
            except (FileNotFoundError, json.JSONDecodeError, RuntimeError, fantrax_dom.VisibleRosterIdentityError) as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for request-bound browser evidence") from last_error
            time.sleep(0.25)


def evaluate_preflight(
    request_payload: dict[str, Any],
    live: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare a claimed immutable proposal to fresh live Fantrax evidence."""
    now = _aware(now or datetime.now(timezone.utc))
    contract = request_payload.get("contract") or {}
    expected_ids = {str(value) for value in request_payload.get("expected_roster_ids") or []}
    slot_moves = contract.get("slot_moves") or []
    snapshot = live.get("snapshot") if isinstance(live.get("snapshot"), dict) else {}
    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    rows = roster.get("rows") if isinstance(roster.get("rows"), list) else []
    by_id = {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }
    live_ids = set(by_id)
    dom_slots = live.get("dom_slots") if isinstance(live.get("dom_slots"), dict) else {}
    checks: list[dict[str, str]] = []
    participant_slots: dict[str, str] = {}
    eligible_destinations: dict[str, list[str]] = {}
    lineup_control_enabled: dict[str, bool] = {}

    _check(
        checks,
        "mode",
        request_payload.get("mode") == "dry_run" and contract.get("writes_enabled") is False,
        "Request and proposal remain dry-run/read-only.",
        "Request is not an immutable dry-run contract.",
    )
    _check(
        checks,
        "target_period",
        _same_period(roster.get("period_number"), (contract.get("target_period") or {}).get("period_number")),
        f"Fantrax is editing Period {roster.get('period_number')}.",
        "Live Fantrax editable period does not match the confirmed target period.",
    )
    _check(
        checks,
        "roster_set",
        bool(expected_ids) and live_ids == expected_ids,
        f"All {len(live_ids)} roster player IDs are unchanged.",
        _roster_difference_detail(expected_ids, live_ids),
    )
    _check(
        checks,
        "roster_departures",
        not (expected_ids - live_ids),
        "No player leaves the roster; protected-player departure count is zero.",
        "One or more expected roster players are absent; protected-player safety cannot be proven.",
    )

    deadline = _parse_datetime(((contract.get("movability") or {}).get("deadline") or {}).get("at"))
    _check(
        checks,
        "deadline",
        deadline is not None and now < deadline,
        f"Preflight is before the exact deadline {deadline.isoformat() if deadline else 'unknown'}.",
        "The exact action deadline is missing or has passed.",
    )

    participant_ids: list[str] = []
    for move in slot_moves if isinstance(slot_moves, list) else []:
        if not isinstance(move, dict):
            continue
        player_id = str(move.get("player_id") or "")
        participant_ids.append(player_id)
        row = by_id.get(player_id)
        player_name = str(move.get("player_name") or player_id)
        from_slot = _slot(move.get("from_slot"))
        to_slot = _slot(move.get("to_slot"))
        api_slot = _slot((row or {}).get("slot"))
        dom_slot = _slot((dom_slots.get(player_id) or {}).get("slot"))
        control_enabled = (dom_slots.get(player_id) or {}).get("lineup_control_enabled") is True
        participant_slots[player_id] = api_slot
        eligible_destinations[player_id] = sorted(_eligible_destinations(row or {}))
        lineup_control_enabled[player_id] = control_enabled
        _check(
            checks,
            f"player_present:{player_id}",
            row is not None,
            f"{player_name} is still on the roster.",
            f"{player_name} is missing from the live roster.",
        )
        _check(
            checks,
            f"from_slot:{player_id}",
            row is not None and api_slot == from_slot and dom_slot == from_slot,
            f"{player_name} is still in {from_slot}; API and visible DOM agree.",
            f"{player_name} live slot does not match {from_slot} in both API and visible DOM.",
        )
        _check(
            checks,
            f"destination:{player_id}",
            row is not None and _destination_allowed(row or {}, to_slot),
            f"Fantrax still exposes {to_slot} as an eligible destination for {player_name}.",
            f"Fantrax does not prove {to_slot} remains eligible for {player_name}.",
        )
        _check(
            checks,
            f"lineup_control:{player_id}",
            control_enabled,
            f"{player_name}'s visible lineup control is enabled.",
            f"{player_name}'s visible lineup control is disabled, locked, or missing.",
        )

    _check(
        checks,
        "atomic_swap",
        len(slot_moves) == 2 and len(set(participant_ids)) == 2,
        "The proposal remains one two-player lineup-only swap.",
        "The proposal is no longer an atomic two-player lineup swap.",
    )
    outcome = "failed" if any(check["state"] == "failed" for check in checks) else "passed"
    return {
        "outcome": outcome,
        "checks": checks,
        "evidence": {
            "source": str(live.get("evidence_source") or "visible_fantrax_dom+authenticated_read_api"),
            "target_period": roster.get("period_number"),
            "roster_player_count": len(live_ids),
            "roster_ids_sha256": sandlot_execution.roster_ids_digest(live_ids),
            "participant_ids": participant_ids,
            "participant_slots": participant_slots,
            "eligible_destinations": eligible_destinations,
            "lineup_control_enabled": lineup_control_enabled,
            "fantrax_click_count": 0,
            "fantrax_write_count": 0,
        },
        "observed_at": now.isoformat(),
        "writes_attempted": False,
    }


def process_once(
    *,
    base_url: str,
    runner_token: str,
    runner_id: str,
    reader: LiveReader | None = None,
    http: Any = requests,
) -> dict[str, Any] | None:
    base_url = validate_base_url(base_url)
    headers = {"authorization": f"Bearer {runner_token}"}
    response = _post_without_redirects(
        http,
        f"{base_url.rstrip('/')}/api/execution-requests/claim",
        json={"runner_id": runner_id},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    claimed = response.json().get("request")
    if not claimed:
        return None

    lease_token = claimed.pop("lease_token")
    try:
        live = (reader or FantraxVisibleReader()).read(claimed)
        report = evaluate_preflight(claimed, live)
    except Exception as exc:
        report = {
            "outcome": "failed",
            "checks": [{
                "key": "live_read",
                "state": "failed",
                "detail": f"Live Fantrax read failed ({type(exc).__name__}); inspect the local runner.",
            }],
            "evidence": {
                "source": "visible_fantrax_dom+authenticated_read_api",
                "fantrax_click_count": 0,
                "fantrax_write_count": 0,
            },
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "writes_attempted": False,
        }

    result = _post_without_redirects(
        http,
        f"{base_url.rstrip('/')}/api/execution-requests/{claimed['request_id']}/preflight",
        json={**report, "lease_token": lease_token},
        headers=headers,
        timeout=20,
    )
    result.raise_for_status()
    return result.json()


def live_from_visible_browser(
    request_payload: dict[str, Any],
    browser_evidence: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Join fresh visible rows to the server-bound roster without cookies."""
    now = _aware(now or datetime.now(timezone.utc))
    captured_at = _parse_datetime(browser_evidence.get("captured_at"))
    claimed_at = _parse_datetime(request_payload.get("claimed_at"))
    if not captured_at or not claimed_at:
        raise RuntimeError("Browser evidence and claim timestamps are required")
    expected_binding = _browser_evidence_binding(request_payload)
    if any(str(browser_evidence.get(key) or "") != str(value) for key, value in expected_binding.items()):
        raise RuntimeError("Browser evidence does not match the claimed request identity")
    if captured_at < claimed_at - timedelta(seconds=2):
        raise RuntimeError("Browser evidence predates the live claim window")
    if captured_at > now + timedelta(seconds=15) or now - captured_at > timedelta(seconds=30):
        raise RuntimeError("Browser evidence is not fresh")

    safety = request_payload.get("safety") if isinstance(request_payload.get("safety"), dict) else {}
    expected_roster = safety.get("expected_roster") if isinstance(safety.get("expected_roster"), list) else []
    participant_destinations = (
        safety.get("participant_destinations")
        if isinstance(safety.get("participant_destinations"), dict)
        else {}
    )
    visible_rows = browser_evidence.get("rows") if isinstance(browser_evidence.get("rows"), list) else []
    reconciled = fantrax_dom.reconcile_visible_roster_rows(visible_rows, expected_roster)
    period_number = browser_evidence.get("period_number")
    try:
        int(period_number)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Visible Fantrax period number is missing") from exc

    roster_rows: list[dict[str, Any]] = []
    dom_slots: dict[str, dict[str, Any]] = {}
    for row in reconciled:
        player_id = str(row["player_id"])
        destinations = {
            _slot(value)
            for value in participant_destinations.get(player_id) or []
            if value
        }
        roster_rows.append({
            "id": player_id,
            "name": row.get("name"),
            "team": row.get("team"),
            "slot": _slot(row.get("slot")),
            "lineup_eligibility": {
                "eligible_statuses": ["RES"] if "RES" in destinations else [],
                "eligible_positions": sorted(destinations - {"RES"}),
                "source": "fresh_server_snapshot_bound_to_visible_dom",
            },
        })
        dom_slots[player_id] = {
            "slot": _slot(row.get("slot")),
            "slot_source": "visible_browser_dom",
            "identity_source": row.get("identity_source"),
            "lineup_control_enabled": row.get("lineup_control_enabled") is True,
        }
    return {
        "snapshot": {"roster": {"period_number": int(period_number), "rows": roster_rows}},
        "dom_slots": dom_slots,
        "evidence_source": "visible_fantrax_dom+fresh_server_snapshot",
    }


def _browser_evidence_binding(request_payload: dict[str, Any]) -> dict[str, Any]:
    contract = request_payload.get("contract") if isinstance(request_payload.get("contract"), dict) else {}
    binding = {
        "request_id": request_payload.get("request_id"),
        "snapshot_id": request_payload.get("snapshot_id"),
        "proposal_id": request_payload.get("proposal_id"),
        "input_hash": request_payload.get("input_hash"),
        "league_id": contract.get("league_id"),
        "team_id": contract.get("team_id"),
    }
    if any(value in {None, ""} for value in binding.values()):
        raise RuntimeError("Claim is missing browser-evidence binding fields")
    return binding


def validate_base_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").casefold()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Runner base URL must be an uncredentialed origin")
    if parsed.path not in {"", "/"}:
        raise ValueError("Runner base URL must not contain a path")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("Runner base URL must use HTTPS except for loopback development")
    return raw.rstrip("/")


def _post_without_redirects(http: Any, url: str, **kwargs: Any) -> Any:
    response = http.post(url, allow_redirects=False, **kwargs)
    status_code = int(getattr(response, "status_code", 0) or 0)
    if 300 <= status_code < 400:
        raise RuntimeError("Runner API refused a credential-bearing redirect")
    return response


def _destination_allowed(row: dict[str, Any], to_slot: str) -> bool:
    return to_slot in _eligible_destinations(row)


def _eligible_destinations(row: dict[str, Any]) -> set[str]:
    eligibility = row.get("lineup_eligibility")
    if not isinstance(eligibility, dict):
        return set()
    destinations = {_slot(value) for value in eligibility.get("eligible_positions") or []}
    statuses = {_slot(value) for value in eligibility.get("eligible_statuses") or []}
    if statuses & BENCH_SLOTS:
        destinations.add("RES")
    return {value for value in destinations if value}


def _check(
    checks: list[dict[str, str]],
    key: str,
    passed: bool,
    passed_detail: str,
    failed_detail: str,
) -> None:
    checks.append({
        "key": key,
        "state": "passed" if passed else "failed",
        "detail": passed_detail if passed else failed_detail,
    })


def _roster_difference_detail(expected: set[str], live: set[str]) -> str:
    missing = sorted(expected - live)
    added = sorted(live - expected)
    return f"Roster set changed (missing={missing[:5]}, added={added[:5]})."


def _same_period(left: Any, right: Any) -> bool:
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def _slot(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return "RES" if normalized == "BN" else normalized


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("SANDLOT_URL", "http://127.0.0.1:8123"))
    parser.add_argument("--runner-id", default=os.environ.get("SANDLOT_RUNNER_ID", "zach-mac-visible"))
    parser.add_argument(
        "--browser-evidence-json",
        help="Fresh non-secret visible-DOM roster evidence; avoids reading local Fantrax cookies",
    )
    parser.add_argument("--loop", action="store_true", help="Poll for new requests after each terminal dry-run")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--evidence-wait-seconds", type=float, default=60.0)
    args = parser.parse_args()
    token = str(os.environ.get("SANDLOT_RUNNER_TOKEN") or "")
    if not token:
        raise SystemExit("SANDLOT_RUNNER_TOKEN is required")
    while True:
        reader = BrowserEvidenceReader(
            args.browser_evidence_json,
            wait_seconds=args.evidence_wait_seconds,
        ) if args.browser_evidence_json else None
        result = process_once(
            base_url=args.base_url,
            runner_token=token,
            runner_id=args.runner_id,
            reader=reader,
        )
        if result:
            print(f"{result.get('request_id')}: {result.get('state')}")
        elif not args.loop:
            print("No pending dry-run execution request.")
        if not args.loop:
            return 0
        time.sleep(max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
