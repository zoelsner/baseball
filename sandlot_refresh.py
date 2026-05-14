"""Shared Fantrax refresh runner for Sandlot API and Railway cron."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

import auth
import fantrax_data
import sandlot_data_quality
import sandlot_db
import sandlot_matchup

log = logging.getLogger(__name__)


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

    run_id = sandlot_db.create_refresh_run(source)
    started = time.perf_counter()
    snapshot_id: int | None = None

    try:
        league_id = os.environ["FANTRAX_LEAGUE_ID"]
        team_id = os.environ["FANTRAX_TEAM_ID"]
        session, cookies, cookie_source = _session_from_available_cookies()
        snapshot = fantrax_data.collect_all(session, league_id, team_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        errors = [str(e) for e in (snapshot.get("errors") or [])]
        status = "failed" if _looks_like_failed_auth(snapshot) else "success"

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


def _persist_projection_log(snapshot_id: int, snapshot: dict[str, Any]) -> None:
    try:
        data_quality = sandlot_data_quality.snapshot_data_quality(snapshot)
        record = sandlot_matchup.projection_log_payload(snapshot_id, snapshot, data_quality)
        if not record:
            return
        sandlot_db.upsert_projection_log(**record)
    except Exception:
        log.exception("Projection log write failed for snapshot_id=%s", snapshot_id)


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
