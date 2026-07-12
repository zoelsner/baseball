"""Propose the optimal Monday lineup for the coming scoring week.

Deterministic end to end: latest roster snapshot from Postgres, MLB game
logs scored with the league's exact rules, next week's schedule + posted
probables, then an exact assignment to the league's full 20-slot template.

Usage: DATABASE_URL=postgres://... python scripts/run_monday_lineup.py
Output: markdown lineup card to stdout (and $GITHUB_STEP_SUMMARY if set),
        full JSON to monday_lineup.json, and one immutable recommendation
        receipt in Postgres. No Fantrax writes.
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
import requests  # noqa: E402

import mlb_stats  # noqa: E402
import sandlot_data_quality  # noqa: E402
import sandlot_db  # noqa: E402
import sandlot_lineup as lineup  # noqa: E402
import sandlot_receipts  # noqa: E402
import sandlot_scoring as scoring  # noqa: E402
from sandlot_autopsy import INJURED_SLOTS, PROTECTED_SLOTS, eligibility_tokens  # noqa: E402

ET = ZoneInfo("America/New_York")
RECENT_WINDOW_DAYS = 30
GAME_LOG_THREADS = 8


def coming_week(today: date) -> tuple[date, date]:
    """Next Monday..Sunday (or the current week if today is Monday)."""
    monday = today - timedelta(days=today.weekday())
    if today.weekday() != 0:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=6)


def probable_start_counts(start: date, end: date) -> dict[str, int]:
    """str(mlb_id) -> number of posted probable starts in [start, end]."""
    counts: dict[str, int] = defaultdict(int)
    resp = requests.get(
        f"{mlb_stats.BASE_URL}/schedule",
        params={
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "probablePitcher",
            "fields": "dates,games,teams,away,home,probablePitcher,id",
        },
        timeout=mlb_stats.DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    for d in (resp.json().get("dates") or []):
        for g in (d.get("games") or []):
            for side in ("away", "home"):
                pid = (((g.get("teams") or {}).get(side) or {}).get("probablePitcher") or {}).get("id")
                if pid is not None:
                    counts[str(pid)] += 1
    return dict(counts)


def team_game_counts(start: date, end: date) -> dict[str, int]:
    """Scheduled games per (normalized) team abbreviation in [start, end]."""
    counts: dict[str, int] = defaultdict(int)
    resp = requests.get(
        f"{mlb_stats.BASE_URL}/schedule",
        params={
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "fields": "dates,games,teams,away,home,team,id",
        },
        timeout=mlb_stats.DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    abbrev_map = mlb_stats._get_team_abbreviations(start.year)
    for d in (resp.json().get("dates") or []):
        for g in (d.get("games") or []):
            for side in ("away", "home"):
                team_id = (((g.get("teams") or {}).get(side) or {}).get("team") or {}).get("id")
                abbr = abbrev_map.get(int(team_id)) if team_id is not None else None
                if abbr:
                    counts[mlb_stats._normalize_team(abbr) or abbr] += 1
    return dict(counts)


def scored_game_log(mlb_id: int, tokens: set[str], season: int) -> list[dict]:
    """League-scored per-game rows, both stat groups for two-way players."""
    groups = []
    if tokens - lineup.PITCHER_TOKENS:
        groups.append("hitting")
    if tokens & lineup.PITCHER_TOKENS:
        groups.append("pitching")
    games = []
    for group in groups or ["hitting"]:
        try:
            for g in mlb_stats.fetch_game_log(mlb_id, season=season, group=group):
                games.append({
                    "date": g.get("date"),
                    "gs": bool(g.get("gs")),
                    "group": group,
                    "pts": scoring.game_points(g, group),
                })
        except Exception as exc:  # noqa: BLE001
            print(f"  game log failed for mlb_id={mlb_id}: {exc}", flush=True)
    games.sort(key=lambda g: g["date"] or "")
    return games


def require_trusted_roster_slots(rows: list[dict]) -> None:
    if not rows:
        raise RuntimeError("Monday lineup receipt paused: roster is empty")
    untrusted = []
    for row in rows:
        if not isinstance(row, dict):
            untrusted.append("unknown")
            continue
        source = str(row.get("slot_source") or "").strip().casefold()
        if source in sandlot_data_quality.UNTRUSTED_SLOT_SOURCES:
            untrusted.append(str(row.get("name") or row.get("id") or "unknown"))
    if untrusted:
        examples = ", ".join(untrusted[:5])
        raise RuntimeError(
            f"Monday lineup receipt paused: trusted Fantrax slots missing for "
            f"{len(untrusted)}/{len(rows)} roster players ({examples})"
        )


def run():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL is not set (is the repository secret configured?)")

    with psycopg.connect(dsn, connect_timeout=20) as conn:
        conn.read_only = True
        row = conn.execute(
            """
            SELECT id, taken_at, source, status, league_id, team_id, team_name,
                   data->'roster' AS roster
            FROM snapshots WHERE status='success'
            ORDER BY taken_at DESC LIMIT 1
            """
        ).fetchone()
        if not row:
            sys.exit("No successful snapshots found")
        snap_id, taken_at, snapshot_source, snapshot_status, league_id, team_id, team_name, roster = row
        id_map = dict(conn.execute(
            "SELECT fantrax_id, mlb_id FROM player_id_map WHERE mlb_id IS NOT NULL"
        ).fetchall())

    rows = roster.get("rows") or []
    require_trusted_roster_slots(rows)
    if not league_id or not team_id:
        raise RuntimeError("Monday lineup receipt requires snapshot league and team identity")
    today = datetime.now(ET).date()
    season = today.year
    monday, sunday = coming_week(today)
    recent_start = today - timedelta(days=RECENT_WINDOW_DAYS)

    print(f"roster snapshot {snap_id} ({taken_at:%Y-%m-%d}), {len(rows)} players")
    print(f"optimizing week {monday} .. {sunday}")

    games_next = team_game_counts(monday, sunday)
    games_recent_by_team = team_game_counts(recent_start, today)
    probable_counts = probable_start_counts(monday, sunday)

    def resolve(r):
        fid = r.get("id")
        mlb_id = id_map.get(fid) or mlb_stats.lookup_player_by_name(
            r.get("name") or "", r.get("team"), season=season
        )
        return fid, mlb_id

    mlb_ids = {}
    for fid, mlb_id in map(resolve, rows):
        if fid and mlb_id:
            mlb_ids[fid] = int(mlb_id)

    logs = {}
    with ThreadPoolExecutor(max_workers=GAME_LOG_THREADS) as pool:
        futures = {
            fid: pool.submit(scored_game_log, mlb_ids[fid], eligibility_tokens(r), season)
            for fid, r in ((r.get("id"), r) for r in rows) if fid in mlb_ids
        }
        logs = {fid: f.result() for fid, f in futures.items()}

    entries, excluded, current_active = [], [], []
    for r in rows:
        fid = r.get("id")
        name = r.get("name") or fid or "?"
        tokens = eligibility_tokens(r)
        slot = (r.get("slot") or "").strip().upper()
        injury = (r.get("injury") or "").strip().upper()
        games = logs.get(fid, [])
        recent = [g for g in games if (g["date"] or "") >= recent_start.isoformat()]
        team = mlb_stats._normalize_team(r.get("team")) or ""
        n_probable = probable_counts.get(str(mlb_ids.get(fid)), 0)
        hitting_games = [g for g in games if g.get("group") == "hitting"]
        pitching_games = [g for g in games if g.get("group") == "pitching"]
        hitting_recent = [g for g in recent if g.get("group") == "hitting"]
        pitching_recent = [g for g in recent if g.get("group") == "pitching"]
        starts_recent = sum(1 for g in pitching_recent if g["gs"])
        projection = lineup.project_week(
            tokens,
            hitting_season_points=[g["pts"] for g in hitting_games],
            hitting_recent_points=[g["pts"] for g in hitting_recent],
            pitching_season_points=[g["pts"] for g in pitching_games],
            pitching_recent_points=[g["pts"] for g in pitching_recent],
            team_games_next=games_next.get(team, 0),
            team_games_recent=games_recent_by_team.get(team, 0),
            starts_recent=starts_recent,
            probable_starts=n_probable,
        )
        component_points = {
            component["group"]: component["points"]
            for component in projection["components"]
        }
        hitter_proj = round(component_points.get("hitting", 0.0), 1)
        pitcher_proj = round(hitter_proj + component_points.get("pitching", 0.0), 1)
        can_hit = bool(tokens - lineup.PITCHER_TOKENS)
        can_pitch = bool(tokens & lineup.PITCHER_TOKENS)
        proj = max(
            hitter_proj if can_hit else float("-inf"),
            pitcher_proj if can_pitch else float("-inf"),
        )
        if proj == float("-inf"):
            proj = 0.0
        basis_parts = [
            f"{component['group']} {component['rate']:.1f}/gm x "
            f"{component['expected']:.1f} {component['unit']}"
            + (" (probable)" if component["group"] == "pitching" and n_probable else "")
            for component in projection["components"]
        ]
        basis = " + ".join(basis_parts or ["no scoring data"])
        basis += " [DTD]" if injury == "DTD" else ""
        basis += "" if fid in mlb_ids else " [no MLB data]"
        entry = {"id": fid, "name": name, "tokens": tokens, "proj": proj,
                 "hitter_proj": hitter_proj, "pitcher_proj": pitcher_proj,
                 "basis": basis, "slot": slot, "slot_source": r.get("slot_source"),
                 "injury": injury}
        if slot in INJURED_SLOTS or injury in lineup.BLOCKED_INJURIES:
            excluded.append(entry)
            continue
        if slot in PROTECTED_SLOTS:
            excluded.append({**entry, "basis": basis + " [minors]"})
            continue
        entries.append(entry)
        if slot not in ("BN", "RES") and slot not in INJURED_SLOTS:
            current_active.append(entry)

    result = lineup.propose(entries)
    by_name = {e["name"]: e for e in entries}
    current_total = round(
        sum(lineup.projected_for_slot(entry, entry["slot"]) for entry in current_active),
        1,
    )
    receipt_current_active = [
        {**entry, "assigned_projection": lineup.projected_for_slot(entry, entry["slot"])}
        for entry in current_active
    ]
    proposed_names = {name for _, name in result["lineup"]}
    ins = sorted(n for n in proposed_names if n not in {e["name"] for e in current_active})
    outs = sorted(e["name"] for e in current_active if e["name"] not in proposed_names)

    lines = [
        f"# Monday lineup — {team_name or 'my team'}, week {monday} .. {sunday}",
        "",
        f"Projected: **{result['projected_total']:.1f}** vs {current_total:.1f} "
        f"if you roll forward your current actives "
        f"(**{result['projected_total'] - current_total:+.1f}**).",
        "",
        "| Slot | Player | Proj | Basis |",
        "|------|--------|-----:|-------|",
    ]
    slot_order = {s: i for i, s in enumerate(lineup.FULL_ACTIVE_TEMPLATE)}
    for slot, name in sorted(result["lineup"], key=lambda x: slot_order.get(x[0], 99)):
        e = by_name.get(name, {})
        assigned_projection = lineup.projected_for_slot(e, slot)
        lines.append(f"| {slot} | {name} | {assigned_projection:.1f} | {e.get('basis','')} |")
    if result["unfilled"]:
        lines += ["", f"**No eligible player for: {', '.join(result['unfilled'])}** — "
                  "these slots score zero unless you add someone."]
    if ins or outs:
        lines += ["", f"Moves: start {', '.join(ins) or '—'}; bench {', '.join(outs) or '—'}."]
    bench = sorted((e for e in entries if e["name"] not in proposed_names),
                   key=lambda e: -e["proj"])
    if bench:
        lines += ["", "Bench (by projection): "
                  + ", ".join(f"{e['name']} {e['proj']:.1f}" for e in bench[:8])]
    if excluded:
        lines += ["", "Excluded (IL/out/minors): "
                  + ", ".join(e["name"] for e in excluded)]
    lines += ["", "_Deterministic projection: blended per-game rate x expected "
              "games (schedule, rotation cadence, posted probables). No AI in "
              "this path._"]
    summary = "\n".join(lines)

    receipt = sandlot_receipts.build_monday_lineup_receipt(
        snapshot={
            "id": snap_id,
            "taken_at": taken_at,
            "source": snapshot_source,
            "status": snapshot_status,
            "league_id": league_id,
            "team_id": team_id,
        },
        week_start=monday,
        week_end=sunday,
        result=result,
        entries=entries,
        current_active=receipt_current_active,
        current_total=current_total,
    )
    sandlot_db.ensure_recommendation_receipts_schema()
    persisted_receipt, created = sandlot_db.record_recommendation_receipt(receipt)
    print("\n" + summary)
    print(
        f"\nreceipt {persisted_receipt['receipt_id']} "
        f"({'created' if created else 'already recorded'})"
    )

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    with open("monday_lineup.json", "w", encoding="utf-8") as fh:
        json.dump({"week": [monday.isoformat(), sunday.isoformat()],
                   "proposal": result, "entries": entries, "excluded": excluded,
                   "current_total": current_total,
                   "receipt": {
                       "receipt_id": persisted_receipt["receipt_id"],
                       "input_hash": persisted_receipt["input_hash"],
                       "scope_key": persisted_receipt["scope_key"],
                       "created": created,
                   }}, fh, indent=1, default=str)
    print("\nfull detail written to monday_lineup.json")


if __name__ == "__main__":
    run()
