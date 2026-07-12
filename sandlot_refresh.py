"""Shared Fantrax refresh runner for Sandlot API and Railway cron."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

import auth
import fantrax_data
import fantrax_dom
import sandlot_data_quality
import sandlot_db
import sandlot_future_games
import sandlot_matchup
import sandlot_receipts

log = logging.getLogger(__name__)
REFRESH_LOCK_ID = 2026051501
DOM_SLOT_CAPTURE_ENV = "SANDLOT_CAPTURE_ROSTER_DOM_SLOTS"


@dataclass
class RefreshResult:
    status: str
    snapshot_id: int | None
    duration_ms: int
    errors: list[str]
    snapshot: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


def run_refresh(source: str = "manual") -> RefreshResult:
    """Scrape Fantrax once, persist a snapshot row, and log the refresh run."""
    load_dotenv()
    sandlot_db.init_schema()

    started = time.perf_counter()
    with _refresh_lock() as locked:
        if not locked:
            return _skipped_refresh(source, started, "Refresh already in progress")
        return _run_refresh_unlocked(source, started)


def _run_refresh_unlocked(source: str, started: float) -> RefreshResult:
    run_id = sandlot_db.create_refresh_run(source)
    snapshot_id: int | None = None

    try:
        league_id = os.environ["FANTRAX_LEAGUE_ID"]
        team_id = os.environ["FANTRAX_TEAM_ID"]
        session, cookies, cookie_source = _session_from_available_cookies()
        snapshot = fantrax_data.collect_all(session, league_id, team_id)
        snapshot = sandlot_future_games.enrich_snapshot_future_games(snapshot)
        snapshot = _maybe_apply_dom_slot_proof(snapshot, cookies, league_id, team_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        section_errors = [str(e) for e in (snapshot.get("errors") or [])]
        failure_errors = _refresh_failure_errors(snapshot, section_errors)
        errors = _unique_errors([*section_errors, *failure_errors])
        if errors != section_errors:
            snapshot = {**snapshot, "errors": errors}
        status = "failed" if failure_errors else "success"

        if status == "success" and cookies:
            sandlot_db.upsert_fantrax_cookies(cookies, source=cookie_source)

        snapshot_id = sandlot_db.insert_snapshot(
            source=source,
            status=status,
            data=snapshot,
            duration_ms=duration_ms,
            errors=errors,
        )
        if status == "success":
            _persist_projection_log(snapshot_id, snapshot)
            _persist_lineup_period_evidence(snapshot_id, snapshot)
            _persist_recommendation_outcomes(snapshot_id, snapshot)
        sandlot_db.finish_refresh_run(
            run_id,
            status=status,
            snapshot_id=snapshot_id,
            duration_ms=duration_ms,
            error="; ".join(errors)[:1000] if status == "failed" else None,
        )
        if status == "success":
            sandlot_db.prune_successful_snapshots(keep=int(os.environ.get("SANDLOT_KEEP_SNAPSHOTS", "30")))

        return RefreshResult(
            status=status,
            snapshot_id=snapshot_id,
            duration_ms=duration_ms,
            errors=errors,
            snapshot=snapshot,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        error = str(exc)
        failed_snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "league_id": os.environ.get("FANTRAX_LEAGUE_ID"),
            "team_id": os.environ.get("FANTRAX_TEAM_ID"),
            "team_name": None,
            "roster": None,
            "standings": None,
            "errors": [error],
        }
        try:
            snapshot_id = sandlot_db.insert_snapshot(
                source=source,
                status="failed",
                data=failed_snapshot,
                duration_ms=duration_ms,
                errors=[error],
            )
        finally:
            sandlot_db.finish_refresh_run(
                run_id,
                status="failed",
                snapshot_id=snapshot_id,
                duration_ms=duration_ms,
                error=error[:1000],
            )
        log.exception("Sandlot refresh failed")
        return RefreshResult(status="failed", snapshot_id=snapshot_id, duration_ms=duration_ms, errors=[error])


@contextmanager
def _refresh_lock():
    with sandlot_db.connect() as conn:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS locked",
            (REFRESH_LOCK_ID,),
        ).fetchone()
        locked = bool(row and row.get("locked"))
        if not locked:
            yield False
            return
        try:
            yield True
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (REFRESH_LOCK_ID,))


def _skipped_refresh(source: str, started: float, reason: str) -> RefreshResult:
    duration_ms = int((time.perf_counter() - started) * 1000)
    try:
        run_id = sandlot_db.create_refresh_run(source)
        sandlot_db.finish_refresh_run(
            run_id,
            status="skipped",
            snapshot_id=None,
            duration_ms=duration_ms,
            error=reason,
        )
    except Exception:
        log.exception("Failed to record skipped refresh")
    return RefreshResult(status="skipped", snapshot_id=None, duration_ms=duration_ms, errors=[reason])


def _persist_projection_log(snapshot_id: int, snapshot: dict[str, Any]) -> None:
    try:
        data_quality = sandlot_data_quality.snapshot_data_quality(snapshot)
        record = sandlot_matchup.projection_log_payload(snapshot_id, snapshot, data_quality)
        if record:
            sandlot_db.upsert_projection_log(**record, surface="api")
        actual = sandlot_matchup.actual_result_payload(snapshot)
        if actual:
            sandlot_db.update_projection_actuals(**actual)
    except Exception:
        log.exception("Projection log write failed for snapshot_id=%s", snapshot_id)


def _persist_lineup_period_evidence(snapshot_id: int, snapshot: dict[str, Any]) -> None:
    """Archive exact completed-period player evidence without blocking refresh."""
    evidence = snapshot.get("completed_lineup_evidence")
    if not isinstance(evidence, dict) or not os.environ.get("DATABASE_URL"):
        return
    try:
        sandlot_db.archive_lineup_period_evidence(evidence=evidence, snapshot_id=snapshot_id)
    except Exception:
        log.exception("Completed lineup evidence write failed for snapshot_id=%s", snapshot_id)


def _persist_recommendation_outcomes(snapshot_id: int, snapshot: dict[str, Any]) -> None:
    """Score exact completed receipt periods without blocking a healthy refresh."""
    if not os.environ.get("DATABASE_URL"):
        return
    try:
        receipts = sandlot_db.pending_recommendation_receipts(source="monday_lineup")
        taken_at = snapshot.get("timestamp")
        if not taken_at:
            raise ValueError("Successful snapshot is missing its capture timestamp")
        for receipt in receipts:
            try:
                outcome = sandlot_receipts.build_team_result_outcome(
                    receipt=receipt,
                    snapshot=snapshot,
                    snapshot_id=snapshot_id,
                    snapshot_taken_at=taken_at,
                )
                if outcome:
                    sandlot_db.score_recommendation_receipt_team_result(
                        receipt_id=receipt["receipt_id"], outcome=outcome
                    )
                    continue
                unavailable = sandlot_receipts.build_team_result_unavailable(
                    receipt=receipt,
                    snapshot=snapshot,
                    snapshot_id=snapshot_id,
                    snapshot_taken_at=taken_at,
                )
                if unavailable:
                    sandlot_db.mark_recommendation_receipt_outcome_unavailable(
                        receipt_id=receipt["receipt_id"], evidence=unavailable
                    )
            except Exception:
                log.exception("Recommendation outcome failed for receipt_id=%s", receipt.get("receipt_id"))
    except Exception:
        log.exception("Recommendation outcome write failed for snapshot_id=%s", snapshot_id)


def _maybe_apply_dom_slot_proof(
    snapshot: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
    league_id: str,
    team_id: str,
) -> dict[str, Any]:
    """Optionally enrich roster slot provenance from the read-only Fantrax DOM."""
    if os.environ.get(DOM_SLOT_CAPTURE_ENV) != "1":
        return snapshot

    updated = dict(snapshot)
    existing_metadata = snapshot.get("slot_provenance")
    metadata: dict[str, Any] = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    metadata.update({
        "dom_capture_enabled": True,
        "dom_slot_source": "dom.lineup-btn",
        "dom_slots_found": 0,
        "dom_slots_applied": 0,
    })

    if not cookies:
        metadata["dom_capture_error"] = "Fantrax cookies are required for read-only roster DOM slot proof"
        updated["slot_provenance"] = metadata
        return updated

    roster = snapshot.get("roster")
    if not isinstance(roster, dict) or not isinstance(roster.get("rows"), list):
        metadata["dom_capture_error"] = "snapshot roster rows are unavailable"
        updated["slot_provenance"] = metadata
        return updated

    before_summary = _slot_provenance_summary(roster)
    metadata["active_rows_before"] = before_summary["active_rows"]
    metadata["active_trusted_before"] = before_summary["active_trusted"]
    metadata["active_untrusted_examples_before"] = before_summary["active_untrusted_examples"]

    try:
        wait_seconds = float(os.environ.get("SANDLOT_ROSTER_DOM_WAIT_SECONDS", "20"))
        html = fantrax_dom.capture_roster_html(
            cookies,
            league_id=league_id,
            team_id=team_id,
            headful=os.environ.get("SANDLOT_ROSTER_DOM_HEADFUL") == "1",
            url=os.environ.get("SANDLOT_FANTRAX_ROSTER_URL"),
            wait_seconds=wait_seconds,
        )
        slot_overrides = fantrax_dom.lineup_slots_from_html(html)
        before = _slot_source_map(roster)
        enriched_roster = fantrax_data.apply_trusted_slot_overrides(roster, slot_overrides)
        after = _slot_source_map(enriched_roster)
        after_summary = _slot_provenance_summary(enriched_roster)
        updated["roster"] = enriched_roster
        metadata["dom_slots_found"] = len(slot_overrides)
        metadata["dom_slots_conflicted"] = sum(1 for value in slot_overrides.values() if value.get("conflicts"))
        metadata["dom_slots_matched_roster_rows"] = sum(1 for player_id in slot_overrides if str(player_id) in before)
        metadata["dom_slots_unknown_player_ids"] = [
            str(player_id)
            for player_id in slot_overrides
            if str(player_id) not in before
        ][:5]
        metadata["dom_slots_applied"] = sum(
            1
            for player_id, current in after.items()
            if current.get("slot_source") == "dom.lineup-btn" and before.get(player_id) != current
        )
        metadata["active_rows_after"] = after_summary["active_rows"]
        metadata["active_trusted_after"] = after_summary["active_trusted"]
        metadata["active_untrusted_examples_after"] = after_summary["active_untrusted_examples"]
        metadata["active_dom_slots_applied"] = sum(
            1
            for row in enriched_roster.get("rows") or []
            if isinstance(row, dict)
            and _is_active_slot(row.get("slot"))
            and row.get("slot_source") == "dom.lineup-btn"
            and before.get(str(row.get("id") or "")) != after.get(str(row.get("id") or ""))
        )
    except Exception as exc:
        metadata["dom_capture_error"] = str(exc)
        log.warning("Read-only Fantrax roster DOM slot proof failed: %s", exc)
        metadata["active_rows_after"] = before_summary["active_rows"]
        metadata["active_trusted_after"] = before_summary["active_trusted"]
        metadata["active_untrusted_examples_after"] = before_summary["active_untrusted_examples"]

    updated["slot_provenance"] = metadata
    return updated


def _slot_source_map(roster: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("id")): {
            "slot": row.get("slot"),
            "slot_source": row.get("slot_source"),
        }
        for row in roster.get("rows") or []
        if isinstance(row, dict) and row.get("id") is not None
    }


def _slot_provenance_summary(roster: dict[str, Any]) -> dict[str, Any]:
    active_rows = [
        row
        for row in roster.get("rows") or []
        if isinstance(row, dict) and _is_active_slot(row.get("slot"))
    ]
    trusted = [row for row in active_rows if _has_trusted_slot_source(row)]
    untrusted = [row for row in active_rows if not _has_trusted_slot_source(row)]
    return {
        "active_rows": len(active_rows),
        "active_trusted": len(trusted),
        "active_untrusted_examples": [
            str(row.get("name") or row.get("id") or "unknown")
            for row in untrusted[:5]
        ],
    }


def _is_active_slot(value: Any) -> bool:
    slot = str(value or "").strip().upper()
    return slot not in sandlot_data_quality.INACTIVE_SLOTS


def _has_trusted_slot_source(row: dict[str, Any]) -> bool:
    source = str(row.get("slot_source") or "").strip().casefold()
    return bool(source) and source not in sandlot_data_quality.UNTRUSTED_SLOT_SOURCES


def _session_from_available_cookies() -> tuple[requests.Session, list[dict[str, Any]] | None, str]:
    """Build a Fantrax session from DB/env/local cookies.

    Railway should use DB or env cookies. Local dev can fall back to the
    existing Selenium login flow when explicitly allowed or when no Railway env
    marker is present.
    """
    db_cookies = _safe_db_cookies()
    if db_cookies:
        return auth._build_session(db_cookies), db_cookies, "postgres"

    env_cookies = _cookies_from_env()
    if env_cookies:
        return auth._build_session(env_cookies), env_cookies, "env"

    local_cookies = _cookies_from_file(auth.COOKIE_PATH)
    if local_cookies:
        return auth._build_session(local_cookies), local_cookies, "local-file"

    allow_selenium = os.environ.get("SANDLOT_ALLOW_SELENIUM_LOGIN") == "1"
    running_on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
    if running_on_railway and not allow_selenium:
        raise RuntimeError(
            "No Fantrax cookies available. Seed fantrax_sessions first or set FANTRAX_COOKIES_JSON."
        )

    session = auth.get_session(force_login=False)
    post_login_cookies = _cookies_from_file(auth.COOKIE_PATH)
    return session, post_login_cookies, "selenium"


def _safe_db_cookies() -> list[dict[str, Any]] | None:
    try:
        return sandlot_db.get_fantrax_cookies()
    except Exception as exc:
        log.warning("Could not load Fantrax cookies from Postgres: %s", exc)
        return None


def _cookies_from_env() -> list[dict[str, Any]] | None:
    raw = os.environ.get("FANTRAX_COOKIES_JSON")
    if not raw:
        return None
    cookies = json.loads(raw)
    if not isinstance(cookies, list):
        raise RuntimeError("FANTRAX_COOKIES_JSON must be a JSON array of cookie objects")
    return cookies


def _cookies_from_file(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    cookies = json.loads(path.read_text())
    if not isinstance(cookies, list):
        return None
    return cookies


def _looks_like_failed_auth(snapshot: dict[str, Any]) -> bool:
    roster_missing = not snapshot.get("roster")
    standings_missing = not snapshot.get("standings")
    if not (roster_missing and standings_missing):
        return False
    joined_errors = " ".join(str(e) for e in snapshot.get("errors") or []).lower()
    if any(token in joined_errors for token in ("401", "403", "unauthor", "forbidden", "login", "session")):
        return True
    return snapshot.get("team_name") is None


def _refresh_failure_errors(snapshot: dict[str, Any], section_errors: list[str] | None = None) -> list[str]:
    errors = section_errors if section_errors is not None else [str(e) for e in (snapshot.get("errors") or [])]
    failures: list[str] = []
    if _looks_like_failed_auth(snapshot):
        failures.append("Fantrax auth/session appears invalid")
    failures.extend(error for error in errors if error.lower().startswith("roster:"))

    roster = snapshot.get("roster")
    rows = roster.get("rows") if isinstance(roster, dict) else None
    valid_rows = [
        row for row in (rows or [])
        if isinstance(row, dict) and row.get("id") and not row.get("error")
    ]
    if not valid_rows:
        failures.append("No my-roster rows in snapshot")
    return _unique_errors(failures)


def _unique_errors(errors: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for error in errors:
        text = str(error or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
