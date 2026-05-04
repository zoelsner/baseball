"""Weekly league intel: pulls all 12 teams, hydrates with pybaseball stats,
runs grounded research on the highest-impact decisions, and emails a TL;DR
report with cited sources.

Designed to run weekly on Sundays. The daily audit (audit.py) handles
day-to-day lineup hygiene; this script is for strategic moves backed by
verifiable research.

If launchd missed the scheduled time (laptop closed/asleep), this script will
still run when launched manually — it doesn't gate on a clock.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import auth
import decision_engine
import fantrax_data
import notify
import pybaseball_layer
import research_layer

DATA_DIR = Path(".data")
LOG_DIR = DATA_DIR / "logs"


def _setup_logging(date_str: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"intel-{date_str}.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
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


def _hydrate_with_mlb_stats(snapshot: dict, log: logging.Logger) -> None:
    """Add age + current MLB stats to every roster row across the league."""
    log.info("Fetching MLB season batting + pitching boards")
    batting = pybaseball_layer.season_batting_stats()
    pitching = pybaseball_layer.season_pitching_stats()
    log.info("Loaded %d batter rows, %d pitcher rows", len(batting), len(pitching))

    # Hydrate my roster
    roster = snapshot.get("roster") or {}
    if isinstance(roster, dict) and roster.get("rows"):
        log.info("Hydrating my roster (%d players)", len(roster["rows"]))
        pybaseball_layer.hydrate_roster(roster["rows"], batting, pitching)

    # Hydrate all-team rosters with age only (FA pool already cleared lookups)
    all_teams = snapshot.get("all_team_rosters") or {}
    for tid, tdata in all_teams.items():
        rows = tdata.get("rows") or []
        if rows:
            pybaseball_layer.hydrate_age_only(rows)


def main() -> int:
    load_dotenv()
    DATA_DIR.mkdir(exist_ok=True)

    date_str = datetime.now().date().isoformat()
    log_path = _setup_logging(date_str)
    log = logging.getLogger("intel")

    snapshot_path = DATA_DIR / f"intel-snapshot-{date_str}.json"
    report_path = DATA_DIR / f"intel-report-{date_str}.md"

    try:
        league_id = os.environ["FANTRAX_LEAGUE_ID"]
        team_id = os.environ["FANTRAX_TEAM_ID"]

        log.info("Acquiring Fantrax session")
        session = auth.get_session()

        log.info("Pulling base snapshot (my roster, standings, transactions, FA)")
        snapshot = fantrax_data.collect_all(session, league_id, team_id)

        log.info("Pulling all 12 teams' rosters")
        from fantraxapi import FantraxAPI
        api = FantraxAPI(league_id, session=session)
        try:
            snapshot["all_team_rosters"] = fantrax_data.extract_all_team_rosters(api, team_id)
        except Exception as e:
            log.exception("all_team_rosters failed")
            snapshot["errors"].append(f"all_team_rosters: {e}")
            snapshot["all_team_rosters"] = {}

        # Hydrate stats from pybaseball
        try:
            _hydrate_with_mlb_stats(snapshot, log)
        except Exception as e:
            log.exception("MLB stat hydration failed")
            snapshot["errors"].append(f"mlb_stats: {e}")

        # Build candidate decisions
        log.info("Narrowing to research-worthy decisions")
        decisions = decision_engine.build_decision_set(snapshot, max_research_players=10)
        log.info("Will research %d decisions", len(decisions))

        # Save the pre-research snapshot before any expensive calls
        snapshot["candidate_decisions"] = decisions
        snapshot_path.write_text(json.dumps(snapshot, default=str, indent=2))
        log.info("Pre-research snapshot saved to %s", snapshot_path)

        # Run grounded research on each decision
        log.info("Running grounded research (this is the slow part)")
        researched = research_layer.research_decision_set(decisions)

        # Save mid-stage so we don't lose research if synthesis fails
        snapshot["researched_decisions"] = researched
        snapshot_path.write_text(json.dumps(snapshot, default=str, indent=2))
        log.info("Researched snapshot saved")

        # Synthesize final report
        log.info("Synthesizing final report")
        report = decision_engine.synthesize_report(snapshot, researched)

        report_path.write_text(report)
        log.info("Report written to %s", report_path)

        # Send email
        log.info("Sending email")
        team_name = snapshot.get("team_name") or "League"
        notify.send_email(
            subject=f"{team_name} — weekly intel {date_str}",
            markdown_body=report,
        )

        log.info("Done")
        return 0
    except Exception:
        tb = traceback.format_exc()
        logging.getLogger("intel").error("Intel run failed:\n%s", tb)
        try:
            notify.send_failure(tb, log_path=str(log_path))
        except Exception as e2:
            logging.getLogger("intel").error("Failure email also failed: %s", e2)
        return 1


if __name__ == "__main__":
    sys.exit(main())
