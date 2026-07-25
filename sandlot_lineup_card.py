"""Weekly Monday lineup card: compute, payload, and markdown rendering.

Single source of truth for the optimal-lineup proposal. Three consumers:
the Railway cron (computes after each refresh and stores the payload in
`lineup_proposals`), `GET /api/lineup/card` (serves the stored payload to
the Today page), and `scripts/run_monday_lineup.py` (the GitHub Actions
weekly run, which prints/emails the same card).

Deterministic end to end: roster snapshot from Postgres, game history from
`game_scores` (MLB API fallback per uncovered player), next week's schedule
and posted probables, then an exact assignment to the league's full 20-slot
template. No AI in this path.
"""
from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import requests

import mlb_stats
import sandlot_lineup as lineup
import sandlot_scoring as scoring
from sandlot_autopsy import INJURED_SLOTS, eligibility_tokens

ET = ZoneInfo("America/New_York")
RECENT_WINDOW_DAYS = 30
RECENT_FORM_GAMES = 10
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
    """League-scored per-game rows straight from the MLB API (fallback path)."""
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
                    "pts": scoring.game_points(g, group),
                })
        except Exception as exc:  # noqa: BLE001
            print(f"  game log failed for mlb_id={mlb_id}: {exc}", flush=True)
    games.sort(key=lambda g: g["date"] or "")
    return games


def load_game_logs(dsn: str, rows: list[dict], mlb_ids: dict, season: int) -> dict:
    """fid -> [{date, gs, pts}], game_scores table first, MLB API fallback."""
    stored: dict[int, list[dict]] = defaultdict(list)
    try:
        with psycopg.connect(dsn, connect_timeout=20) as conn:
            conn.read_only = True
            for mlb_id, game_date, gs, pts in conn.execute(
                """
                SELECT mlb_id, game_date, gs, pts FROM game_scores
                WHERE season = %s AND mlb_id = ANY(%s)
                ORDER BY game_date ASC, game_pk ASC
                """,
                (season, sorted(set(mlb_ids.values()))),
            ):
                stored[mlb_id].append(
                    {"date": game_date.isoformat(), "gs": bool(gs), "pts": float(pts)}
                )
    except psycopg.errors.UndefinedTable:
        pass
    logs, missing = {}, []
    for r in rows:
        fid = r.get("id")
        if fid not in mlb_ids:
            continue
        if stored.get(mlb_ids[fid]):
            logs[fid] = stored[mlb_ids[fid]]
        else:
            missing.append((fid, r))
    if missing:
        print(f"game_scores covers {len(logs)}/{len(mlb_ids)} players; "
              f"fetching {len(missing)} from the MLB API", flush=True)
        with ThreadPoolExecutor(max_workers=GAME_LOG_THREADS) as pool:
            futures = {
                fid: pool.submit(scored_game_log, mlb_ids[fid], eligibility_tokens(r), season)
                for fid, r in missing
            }
            for fid, future in futures.items():
                logs[fid] = future.result()
    return logs


def build_card(dsn: str | None = None) -> dict[str, Any]:
    """Compute the full card payload. Raises RuntimeError when no snapshot."""
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")

    with psycopg.connect(dsn, connect_timeout=20) as conn:
        conn.read_only = True
        row = conn.execute(
            """
            SELECT id, taken_at, data->'roster' AS roster, data->>'team_name'
            FROM snapshots WHERE status='success'
            ORDER BY taken_at DESC LIMIT 1
            """
        ).fetchone()
        if not row:
            raise RuntimeError("No successful snapshots found")
        snap_id, taken_at, roster, team_name = row
        id_map = dict(conn.execute(
            "SELECT fantrax_id, mlb_id FROM player_id_map WHERE mlb_id IS NOT NULL"
        ).fetchall())

    rows = (roster or {}).get("rows") or []
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

    logs = load_game_logs(dsn, rows, mlb_ids, season)

    entries, excluded, current_active = [], [], []
    for r in rows:
        fid = r.get("id")
        name = r.get("name") or fid or "?"
        tokens = eligibility_tokens(r)
        slot = (r.get("slot") or "").strip().upper()
        injury = (r.get("injury") or "").strip().upper()
        games = logs.get(fid, [])
        season_pts = [g["pts"] for g in games]
        recent = [g for g in games if (g["date"] or "") >= recent_start.isoformat()]
        recent_form = [g["pts"] for g in recent[-RECENT_FORM_GAMES:]]
        rate = lineup.blended_rate(
            sum(recent_form) / len(recent_form) if recent_form else 0.0, len(recent_form),
            sum(season_pts) / len(season_pts) if season_pts else 0.0, len(season_pts),
        )
        team = mlb_stats._normalize_team(r.get("team")) or ""
        n_probable = probable_counts.get(str(mlb_ids.get(fid)), 0)
        starts_recent = sum(1 for g in recent if g["gs"])
        exp = lineup.expected_games(
            tokens,
            team_games_next=games_next.get(team, 0),
            team_games_recent=games_recent_by_team.get(team, 0),
            games_recent=len(recent),
            starts_recent=starts_recent,
            probable_starts=n_probable,
        )
        proj = round(rate * exp, 1)
        is_pitcher_only = bool(tokens & lineup.PITCHER_TOKENS) and not (tokens - lineup.PITCHER_TOKENS)
        starter_usage = is_pitcher_only and starts_recent > 0 and starts_recent * 2 >= len(recent)
        basis = (f"{rate:.1f}/gm x {exp:.1f} "
                 + ("starts" if starter_usage else
                    "outings" if is_pitcher_only else "games")
                 + (" (probable)" if n_probable else "")
                 + (" [DTD]" if injury == "DTD" else "")
                 + ("" if fid in mlb_ids else " [no MLB data]"))
        entry = {"id": fid, "name": name, "tokens": tokens, "proj": proj,
                 "basis": basis, "slot": slot}
        if slot in INJURED_SLOTS or injury in lineup.BLOCKED_INJURIES:
            excluded.append(entry)
            continue
        if slot == "MIN":
            excluded.append({**entry, "basis": basis + " [minors]"})
            continue
        entries.append(entry)
        if slot not in ("BN", "RES") and slot not in INJURED_SLOTS:
            current_active.append(entry)

    result = lineup.propose(entries)
    by_name = {e["name"]: e for e in entries}
    current_total = round(sum(e["proj"] for e in current_active), 1)
    proposed_names = {name for _, name in result["lineup"]}
    ins = sorted(n for n in proposed_names if n not in {e["name"] for e in current_active})
    outs = sorted(e["name"] for e in current_active if e["name"] not in proposed_names)

    slot_order = {s: i for i, s in enumerate(lineup.FULL_ACTIVE_TEMPLATE)}
    lineup_rows = [
        {"slot": slot, "name": name,
         "proj": by_name.get(name, {}).get("proj", 0.0),
         "basis": by_name.get(name, {}).get("basis", "")}
        for slot, name in sorted(result["lineup"], key=lambda x: slot_order.get(x[0], 99))
    ]
    bench = sorted((e for e in entries if e["name"] not in proposed_names),
                   key=lambda e: -e["proj"])

    return {
        "week": [monday.isoformat(), sunday.isoformat()],
        "generated_at": datetime.now(ET).isoformat(),
        "snapshot_id": snap_id,
        "team_name": team_name,
        "projected_total": result["projected_total"],
        "current_total": current_total,
        "delta": round(result["projected_total"] - current_total, 1),
        "lineup": lineup_rows,
        "moves": {"start": ins, "bench": outs},
        "bench": [{"name": e["name"], "proj": e["proj"]} for e in bench],
        "unfilled": result["unfilled"],
        "excluded": [{"name": e["name"], "basis": e["basis"]} for e in excluded],
    }


def render_markdown(card: dict[str, Any]) -> str:
    """The card as the markdown block used in Actions summaries and email."""
    monday, sunday = card["week"]
    lines = [
        f"# Monday lineup — {card.get('team_name') or 'my team'}, week {monday} .. {sunday}",
        "",
        f"Projected: **{card['projected_total']:.1f}** vs {card['current_total']:.1f} "
        f"if you roll forward your current actives "
        f"(**{card['delta']:+.1f}**).",
        "",
        "| Slot | Player | Proj | Basis |",
        "|------|--------|-----:|-------|",
    ]
    for row in card["lineup"]:
        lines.append(f"| {row['slot']} | {row['name']} | {row['proj']:.1f} | {row['basis']} |")
    if card["unfilled"]:
        lines += ["", f"**No eligible player for: {', '.join(card['unfilled'])}** — "
                  "these slots score zero unless you add someone."]
    ins, outs = card["moves"]["start"], card["moves"]["bench"]
    if ins or outs:
        lines += ["", f"Moves: start {', '.join(ins) or '—'}; bench {', '.join(outs) or '—'}."]
    if card["bench"]:
        lines += ["", "Bench (by projection): "
                  + ", ".join(f"{e['name']} {e['proj']:.1f}" for e in card["bench"][:8])]
    if card["excluded"]:
        lines += ["", "Excluded (IL/out/minors): "
                  + ", ".join(e["name"] for e in card["excluded"])]
    lines += ["", "_Deterministic projection: blended per-game rate x expected "
              "games (schedule, rotation cadence, posted probables). No AI in "
              "this path._"]
    return "\n".join(lines)
