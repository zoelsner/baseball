"""Daily Fantrax dynasty fantasy baseball audit.

Pulls a snapshot of the user's team and league, diffs against yesterday,
asks Claude (via the Code CLI) for analysis, and emails the result. Designed
to be run by launchd; persists snapshot/report/log to .data/ regardless of
email outcome so failed runs are still debuggable.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import auth
import claude_analyzer
import fantrax_data
import notify

DATA_DIR = Path(".data")
LOG_DIR = DATA_DIR / "logs"
AGE_DB = DATA_DIR / "age_cache.db"


def _setup_logging(date_str: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{date_str}.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Reset handlers so re-running in the same process doesn't duplicate.
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    return log_path


def _init_age_db() -> sqlite3.Connection:
    AGE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AGE_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT,
            age INTEGER,
            last_seen TEXT
        )
        """
    )
    conn.commit()
    return conn


def _hydrate_ages(roster: dict | None, db: sqlite3.Connection) -> None:
    if not roster:
        return
    rows = roster.get("rows") if isinstance(roster, dict) else None
    if not rows:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    cur = db.cursor()
    for p in rows:
        pid = p.get("id")
        if not pid:
            continue
        try:
            age_val = p.get("age")
            age_int = int(age_val) if age_val not in (None, "") else None
        except (TypeError, ValueError):
            age_int = None

        if age_int is not None:
            cur.execute(
                "INSERT OR REPLACE INTO players (id, name, age, last_seen) VALUES (?, ?, ?, ?)",
                (str(pid), p.get("name"), age_int, today),
            )
        else:
            cur.execute("SELECT age FROM players WHERE id = ?", (str(pid),))
            row = cur.fetchone()
            if row and row[0] is not None:
                p["age"] = row[0]
                p["age_source"] = "cache"
    db.commit()


def _find_prior_snapshot(today_str: str) -> Path | None:
    candidates = sorted(DATA_DIR.glob("snapshot-*.json"), reverse=True)
    for c in candidates:
        if today_str not in c.name:
            return c
    return None


def _diff_snapshots(today: dict, prior_path: Path) -> dict | None:
    try:
        prior = json.loads(prior_path.read_text())
    except Exception as e:
        logging.getLogger("audit").warning("Could not load prior snapshot %s: %s", prior_path, e)
        return None

    diffs: dict = {"added": [], "dropped": [], "slot_changes": [], "fppg_swings": [], "prior_date": prior_path.stem.replace("snapshot-", "")}

    def _rows(snap):
        r = snap.get("roster")
        if isinstance(r, dict):
            return r.get("rows") or []
        if isinstance(r, list):  # tolerate older snapshot format
            return r
        return []

    today_roster = {str(p.get("id")): p for p in _rows(today) if p.get("id")}
    prior_roster = {str(p.get("id")): p for p in _rows(prior) if p.get("id")}

    for pid, p in today_roster.items():
        if pid not in prior_roster:
            diffs["added"].append(p.get("name") or pid)

    for pid, p in prior_roster.items():
        if pid not in today_roster:
            diffs["dropped"].append(p.get("name") or pid)

    for pid, p in today_roster.items():
        prior_p = prior_roster.get(pid)
        if not prior_p:
            continue
        if p.get("slot") != prior_p.get("slot"):
            diffs["slot_changes"].append({
                "name": p.get("name"),
                "from": prior_p.get("slot"),
                "to": p.get("slot"),
            })
        try:
            t = float(p.get("fppg") or 0)
            o = float(prior_p.get("fppg") or 0)
            if abs(t - o) >= 1.0:
                diffs["fppg_swings"].append({"name": p.get("name"), "from": o, "to": t})
        except (TypeError, ValueError):
            pass

    return diffs


def _format_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    s = str(v)
    return s.replace("|", "\\|")


def _compose_report(snapshot: dict, diff: dict | None, analysis: str) -> str:
    date_str = snapshot["timestamp"][:10]
    parts: list[str] = []
    team_name = snapshot.get("team_name") or snapshot.get("team_id")
    parts.append(f"# Fantrax Daily Audit — {team_name} — {date_str}")
    parts.append("")

    if snapshot.get("errors"):
        parts.append("## Data Collection Issues")
        for err in snapshot["errors"]:
            parts.append(f"- {err}")
        parts.append("")

    if diff:
        parts.append(f"## Changes Since {diff.get('prior_date')}")
        wrote_anything = False
        if diff["added"]:
            parts.append(f"- **Added:** {', '.join(diff['added'])}")
            wrote_anything = True
        if diff["dropped"]:
            parts.append(f"- **Dropped:** {', '.join(diff['dropped'])}")
            wrote_anything = True
        if diff["slot_changes"]:
            parts.append("- **Slot changes:**")
            for c in diff["slot_changes"]:
                parts.append(f"  - {c['name']}: {c['from']} -> {c['to']}")
            wrote_anything = True
        if diff["fppg_swings"]:
            parts.append("- **FP/G swings (>=1.0):**")
            for s in diff["fppg_swings"]:
                parts.append(f"  - {s['name']}: {s['from']:.2f} -> {s['to']:.2f}")
            wrote_anything = True
        if not wrote_anything:
            parts.append("- No significant changes since last run.")
        parts.append("")

    parts.append("## Analysis")
    parts.append(analysis or "_No analysis produced._")
    parts.append("")

    parts.append("## Roster Snapshot")
    roster = snapshot.get("roster") or {}
    if isinstance(roster, dict):
        cap = []
        if roster.get("active") is not None:
            cap.append(f"Active {roster.get('active')}/{roster.get('active_max')}")
        if roster.get("reserve") is not None:
            cap.append(f"Reserve {roster.get('reserve')}/{roster.get('reserve_max')}")
        if roster.get("injured") is not None:
            cap.append(f"IR {roster.get('injured')}/{roster.get('injured_max')}")
        if cap:
            parts.append(" / ".join(cap))
            parts.append("")
        roster_rows = roster.get("rows") or []
    else:
        roster_rows = roster if isinstance(roster, list) else []
    if roster_rows:
        parts.append("| Player | Slot | Pos | Team | FP/G | FPts | Age | Status |")
        parts.append("|---|---|---|---|---|---|---|---|")
        for p in roster_rows:
            parts.append(
                "| "
                + " | ".join([
                    _format_cell(p.get("name")),
                    _format_cell(p.get("slot")),
                    _format_cell(p.get("positions")),
                    _format_cell(p.get("team")),
                    _format_cell(p.get("fppg")),
                    _format_cell(p.get("fpts")),
                    _format_cell(p.get("age")),
                    _format_cell(p.get("status") or p.get("injury")),
                ])
                + " |"
            )
    else:
        parts.append("_Roster data unavailable._")
    parts.append("")

    if snapshot.get("pending_trades"):
        parts.append("## Pending Trades")
        parts.append(f"{len(snapshot['pending_trades'])} pending trade(s) involving your team. See snapshot JSON for details.")
        parts.append("")

    fa = snapshot.get("free_agents")
    if fa:
        parts.append("## Free Agent Pool")
        parts.append(f"Method used: `{fa.get('method')}`. Player count fetched: {len(fa.get('players') or [])}.")
        parts.append("")
    else:
        parts.append("## Free Agent Pool")
        parts.append("_Free-agent pool unavailable for this run._")
        parts.append("")

    return "\n".join(parts)


def _is_unauthorized(snapshot_errors: list[str]) -> bool:
    blob = " ".join(snapshot_errors).lower()
    return any(tok in blob for tok in ("unauthorized", "401", "not logged in", "invalid session"))


def main() -> int:
    load_dotenv()
    DATA_DIR.mkdir(exist_ok=True)

    date_str = datetime.now().date().isoformat()
    log_path = _setup_logging(date_str)
    log = logging.getLogger("audit")

    snapshot_path = DATA_DIR / f"snapshot-{date_str}.json"
    report_path = DATA_DIR / f"report-{date_str}.md"

    try:
        league_id = os.environ["FANTRAX_LEAGUE_ID"]
        team_id = os.environ["FANTRAX_TEAM_ID"]

        log.info("Acquiring Fantrax session")
        session = auth.get_session()
        snapshot = fantrax_data.collect_all(session, league_id, team_id)

        if _is_unauthorized(snapshot.get("errors") or []):
            log.warning("Snapshot indicates auth failure; forcing re-login")
            session = auth.get_session(force_login=True)
            snapshot = fantrax_data.collect_all(session, league_id, team_id)

        log.info("Hydrating ages from cache")
        db = _init_age_db()
        try:
            _hydrate_ages(snapshot.get("roster"), db)
        finally:
            db.close()

        log.info("Computing diff vs prior snapshot")
        prior = _find_prior_snapshot(date_str)
        diff = _diff_snapshots(snapshot, prior) if prior else None

        log.info("Writing snapshot to %s", snapshot_path)
        snapshot_path.write_text(json.dumps(snapshot, default=str, indent=2))

        log.info("Calling Claude for analysis")
        analysis = claude_analyzer.analyze(snapshot)

        log.info("Composing report")
        report = _compose_report(snapshot, diff, analysis)
        report_path.write_text(report)
        log.info("Report written to %s", report_path)

        log.info("Sending email")
        team_name = snapshot.get("team_name") or "Fantrax"
        notify.send_email(
            subject=f"{team_name} — daily audit {date_str}",
            markdown_body=report,
        )

        log.info("Done")
        return 0
    except Exception:
        tb = traceback.format_exc()
        logging.getLogger("audit").error("Audit failed:\n%s", tb)
        try:
            notify.send_failure(tb, log_path=str(log_path))
        except Exception as e2:
            logging.getLogger("audit").error("Failure email also failed: %s", e2)
        return 1


if __name__ == "__main__":
    sys.exit(main())
