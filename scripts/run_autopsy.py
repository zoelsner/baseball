"""Lineup-efficiency autopsy over historical snapshots — weekly edition.

This league sets lineups WEEKLY (executed Mondays), so the honest ceiling is
the best lineup you could have locked in on Monday with perfect foresight of
the week's scores — not a daily-churn optimal nobody could execute. For every
team and every Monday-to-Sunday scoring week we have snapshots for, compare:

  actual  — the week's points from the slots the manager actually ran
            (reference roster: the earliest snapshot in that week)
  optimal — the best assignment of that same roster to the same slot
            template, scored over the same full week

Points use the league's exact scoring rules (sandlot_scoring), computed from
MLB per-game logs joined via player_id_map plus name lookup. Weekly player
totals come from the full Mon-Sun game-log window, even for days without a
snapshot — the lineup was locked, so the games count regardless.

Runs anywhere DATABASE_URL can reach the Sandlot Postgres (GitHub Actions,
Railway, local). Read-only: no writes to the database.

Usage: DATABASE_URL=postgres://... python scripts/run_autopsy.py
Output: markdown summary to stdout (and $GITHUB_STEP_SUMMARY if set),
        full JSON report to autopsy_report.json.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

import mlb_stats  # noqa: E402
import sandlot_autopsy as core  # noqa: E402
import sandlot_scoring as scoring  # noqa: E402

ET = ZoneInfo("America/New_York")
GAME_LOG_THREADS = 8
MIN_TRUSTED_COVERAGE = 0.90


def snapshot_day(taken_at, roster_meta) -> str:
    """The MLB calendar date a snapshot's lineups refer to.

    Prefer Fantrax's own period_date when parseable; otherwise use the scrape
    time converted to US/Eastern (MLB's calendar), so a late-night UTC cron
    doesn't land on the wrong day.
    """
    period_date = (roster_meta or {}).get("period_date")
    if isinstance(period_date, str):
        head = period_date.strip()[:10]
        try:
            return datetime.strptime(head, "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    return taken_at.astimezone(ET).date().isoformat()


def week_monday(day_iso: str) -> str:
    d = date.fromisoformat(day_iso)
    return (d - timedelta(days=d.weekday())).isoformat()


def load_snapshots(conn):
    rows = conn.execute(
        """
        SELECT id, taken_at,
               data->'all_team_rosters' AS rosters,
               data->>'team_id'          AS my_team_id
        FROM snapshots
        WHERE status = 'success'
        ORDER BY taken_at
        """
    ).fetchall()
    by_day = {}
    my_team_id = None
    for snap_id, taken_at, rosters, snap_my_team in rows:
        if not rosters:
            continue
        my_team_id = snap_my_team or my_team_id
        any_roster = next(iter(rosters.values()), {})
        day = snapshot_day(taken_at, any_roster)
        by_day[day] = (snap_id, rosters)  # later snapshot for the same day wins
    return by_day, my_team_id


def collect_players(by_day):
    """fid -> {name, team, tokens} across all snapshots (newest wins)."""
    players = {}
    for _, (_, rosters) in sorted(by_day.items()):
        for team in rosters.values():
            for row in team.get("rows") or []:
                fid = row.get("id")
                if not fid:
                    continue
                players[fid] = {
                    "name": row.get("name") or "",
                    "team": row.get("team") or "",
                    "tokens": core.eligibility_tokens(row),
                }
    return players


def resolve_mlb_ids(conn, players, season):
    """fid -> mlb_id via player_id_map, falling back to name lookup."""
    id_map = dict(
        conn.execute(
            "SELECT fantrax_id, mlb_id FROM player_id_map WHERE mlb_id IS NOT NULL"
        ).fetchall()
    )
    resolved, unresolved = {}, []
    for fid, info in players.items():
        mlb_id = id_map.get(fid)
        if mlb_id is None and info["name"]:
            mlb_id = mlb_stats.lookup_player_by_name(info["name"], info["team"], season=season)
        if mlb_id:
            resolved[fid] = int(mlb_id)
        else:
            unresolved.append(fid)
    return resolved, unresolved


def fetch_daily_points(players, resolved, season):
    """Return points plus explicit per-player fetch provenance."""

    def groups_for(tokens):
        groups = []
        if tokens - core.PITCHER_TOKENS:
            groups.append("hitting")
        if tokens & core.PITCHER_TOKENS:
            groups.append("pitching")
        return groups or ["hitting"]

    def fetch(fid):
        daily: dict[str, float] = defaultdict(float)
        failures = []
        for group in groups_for(players[fid]["tokens"]):
            try:
                for game in mlb_stats.fetch_game_log(resolved[fid], season=season, group=group):
                    day = game.get("date")
                    if day:
                        daily[day] += scoring.game_points(game, group)
            except Exception as exc:  # noqa: BLE001 — a missing log is data, not fatal
                print(f"  game log failed for {players[fid]['name']}: {exc}", flush=True)
                failures.append(f"{group}: {type(exc).__name__}")
        return fid, dict(daily), failures

    points, successful_ids, failures = {}, set(), {}
    with ThreadPoolExecutor(max_workers=GAME_LOG_THREADS) as pool:
        for fid, daily, player_failures in pool.map(fetch, list(resolved)):
            points[fid] = daily
            if player_failures:
                failures[fid] = player_failures
            else:
                successful_ids.add(fid)
    return points, successful_ids, failures


def coverage_is_trusted(coverage):
    points_coverage = coverage.get("points_coverage")
    id_coverage = coverage.get("id_coverage")
    return (
        isinstance(points_coverage, (int, float))
        and isinstance(id_coverage, (int, float))
        and points_coverage >= MIN_TRUSTED_COVERAGE
        and id_coverage >= MIN_TRUSTED_COVERAGE
    )


def week_windows(by_day):
    """monday_iso -> (reference_day, [date_iso, ...] for the full Mon-Sun)."""
    weeks = defaultdict(list)
    for day in sorted(by_day):
        weeks[week_monday(day)].append(day)
    out = {}
    for monday, snap_days in weeks.items():
        start = date.fromisoformat(monday)
        window = [(start + timedelta(days=i)).isoformat() for i in range(7)]
        out[monday] = (snap_days[0], window)
    return out


def run():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL is not set (is the repository secret configured?)")

    with psycopg.connect(dsn, connect_timeout=20) as conn:
        conn.read_only = True
        by_day, my_team_id = load_snapshots(conn)
        if not by_day:
            sys.exit("No successful snapshots found")
        season = max(int(d[:4]) for d in by_day)
        players = collect_players(by_day)
        resolved, unresolved = resolve_mlb_ids(conn, players, season)

    print(f"snapshot days: {len(by_day)} ({min(by_day)} .. {max(by_day)})")
    print(f"players seen: {len(players)}, mlb-resolved: {len(resolved)}, unresolved: {len(unresolved)}")
    points, successful_point_ids, game_log_failures = fetch_daily_points(players, resolved, season)
    last_game_day = max((d for daily in points.values() for d in daily), default=None)

    team_weeks = defaultdict(list)
    team_names = {}
    weeks = week_windows(by_day)
    for monday, (ref_day, window) in sorted(weeks.items()):
        _, rosters = by_day[ref_day]
        partial = bool(last_game_day) and window[-1] > last_game_day
        label = f"wk {monday}" + (" (partial)" if partial else "")
        for tid, team in rosters.items():
            rows = team.get("rows") or []
            if not rows:
                continue
            team_names[tid] = team.get("team_name") or tid
            week_points = {
                fid: sum(points[fid].get(d, 0.0) for d in window)
                for fid in (r.get("id") for r in rows)
                if fid in successful_point_ids
            }
            result = core.team_day(rows, week_points)
            result["date"] = label
            result["coverage"] = core.coverage(rows, week_points, set(resolved))
            result["trusted"] = coverage_is_trusted(result["coverage"])
            team_weeks[tid].append(result)

    report = {"teams": {}, "diagnostics": {
        "weeks": sorted(weeks),
        "snapshot_days": sorted(by_day),
        "last_game_day": last_game_day,
        "players_seen": len(players),
        "players_resolved": len(resolved),
        "game_log_failure_count": len(game_log_failures),
        "game_log_failures": {
            players[fid]["name"] or fid: failures
            for fid, failures in game_log_failures.items()
        },
        "unresolved_players": sorted(
            players[f]["name"] for f in unresolved if players[f]["name"]
        ),
        "scoring": "league-exact (sandlot_scoring)",
        "my_team_id": my_team_id,
    }}
    for tid, wks in team_weeks.items():
        trusted_wks = [week for week in wks if week["trusted"]]
        agg = core.autopsy(trusted_wks)
        cov = [w["coverage"]["points_coverage"] for w in wks
               if w["coverage"]["points_coverage"] is not None]
        agg["avg_points_coverage"] = round(sum(cov) / len(cov), 3) if cov else None
        agg["trusted_weeks"] = len(trusted_wks)
        agg["excluded_weeks"] = len(wks) - len(trusted_wks)
        agg["team_name"] = team_names[tid]
        agg["is_me"] = tid == my_team_id
        agg["weeks_detail"] = [
            {"week": w["date"], "actual": w["actual"], "optimal": w["optimal"],
             "points_left": w["points_left"],
             "trusted": w["trusted"],
             "coverage": w["coverage"],
             "best_lineup": w["assignment"] if w["trusted"] else []} for w in wks
        ]
        report["teams"][tid] = agg

    ranked = sorted(report["teams"].values(),
                    key=lambda t: t["efficiency"] or 0, reverse=True)
    lines = [
        f"# Weekly lineup efficiency — {len(weeks)} scoring weeks "
        f"({min(weeks)} .. {max(weeks)}), league-exact scoring",
        "",
        "| # | Team | Eff % | Actual | Optimal | Left on bench | Left/week | Coverage | Trusted |",
        "|---|------|------:|-------:|--------:|--------------:|----------:|---------:|--------:|",
    ]
    for i, t in enumerate(ranked, 1):
        eff = f"{t['efficiency'] * 100:.1f}%" if t["efficiency"] is not None else "—"
        cov = f"{t['avg_points_coverage'] * 100:.0f}%" if t["avg_points_coverage"] is not None else "—"
        me = " **(me)**" if t["is_me"] else ""
        left_per_week = t["points_left_total"] / t["days"] if t["days"] else 0.0
        lines.append(
            f"| {i} | {t['team_name']}{me} | {eff} | {t['actual_total']:.1f} "
            f"| {t['optimal_total']:.1f} | {t['points_left_total']:.1f} "
            f"| {left_per_week:.1f} | {cov} | {t['trusted_weeks']}/{t['trusted_weeks'] + t['excluded_weeks']} |"
        )
    lines += ["", "_Weekly-hindsight optimal: the best Monday lineup given "
              "perfect foresight of the week's scores, holding the team's own "
              "slot template fixed. Partial weeks are scored through the last "
              "completed game day. Weeks below 90% player-id or game-log "
              "coverage are excluded from efficiency totals._"]
    summary = "\n".join(lines)
    print("\n" + summary)

    # Per-week detail for my team, so the report can show where points leaked.
    mine = next((t for t in report["teams"].values() if t["is_me"]), None)
    if mine:
        print(f"\nMy weeks ({mine['team_name']}):")
        for w in mine["weeks_detail"]:
            trust = "trusted" if w["trusted"] else "excluded: low coverage"
            print(f"  {w['week']}: actual {w['actual']:.1f} / optimal {w['optimal']:.1f} "
                  f"(left {w['points_left']:.1f}; {trust})")
            if w["trusted"]:
                print(f"    best lineup: {w['best_lineup']}")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    with open("autopsy_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    print("\nfull report written to autopsy_report.json")


if __name__ == "__main__":
    run()
