"""Hard-stat layer: fetches current MLB stats from Fangraphs / Statcast via
pybaseball and caches them locally. This is the antidote to LLM training-data
hallucination — these numbers are real, fetched at runtime, dated.

Cached in .data/pybaseball.db (SQLite). Cache TTL is short (1 day for in-season
stats, 7 days for projections) so reports are always working with fresh data.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CACHE_PATH = Path(".data/pybaseball.db")
CACHE_TTL_STATS_DAYS = 1
CACHE_TTL_PROJECTIONS_DAYS = 7
CACHE_TTL_PLAYER_LOOKUP_DAYS = 30


def _conn() -> sqlite3.Connection:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            payload TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (kind, key)
        )
    """)
    return conn


def _cache_get(kind: str, key: str, ttl_days: int) -> Any | None:
    with _conn() as c:
        row = c.execute(
            "SELECT payload, fetched_at FROM cache WHERE kind=? AND key=?",
            (kind, key),
        ).fetchone()
    if not row:
        return None
    payload, fetched_at = row
    age_days = (datetime.utcnow() - datetime.fromisoformat(fetched_at)).days
    if age_days > ttl_days:
        return None
    return json.loads(payload)


def _cache_put(kind: str, key: str, value: Any) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO cache (kind, key, payload, fetched_at) VALUES (?, ?, ?, ?)",
            (kind, key, json.dumps(value, default=str), datetime.utcnow().isoformat()),
        )


def _import_pyb():
    """Lazy import — pybaseball pulls in pandas + matplotlib, which we don't
    want to load when only the daily audit is running."""
    import pybaseball  # noqa: F401
    return pybaseball


def lookup_player(name: str) -> dict | None:
    """Resolve 'First Last' to MLBAM/Fangraphs IDs + birthdate (so we can
    compute age — Fantrax doesn't expose it). Cached aggressively."""
    cached = _cache_get("player_lookup", name.lower(), CACHE_TTL_PLAYER_LOOKUP_DAYS)
    if cached is not None:
        return cached if cached else None  # cached miss

    parts = name.strip().split()
    if len(parts) < 2:
        _cache_put("player_lookup", name.lower(), {})
        return None
    first, last = parts[0], " ".join(parts[1:])

    try:
        pyb = _import_pyb()
        df = pyb.playerid_lookup(last, first, fuzzy=False)
        if df is None or len(df) == 0:
            _cache_put("player_lookup", name.lower(), {})
            return None

        # Prefer most-recent active player when multiple match
        df = df.sort_values("mlb_played_last", ascending=False, na_position="last")
        row = df.iloc[0]
        info = {
            "name": f"{row.get('name_first','')} {row.get('name_last','')}".strip(),
            "mlbam_id": int(row["key_mlbam"]) if row.get("key_mlbam") else None,
            "fangraphs_id": str(row.get("key_fangraphs")) if row.get("key_fangraphs") else None,
            "bbref_id": str(row.get("key_bbref")) if row.get("key_bbref") else None,
            "birth_year": int(row["birth_year"]) if row.get("birth_year") else None,
            "birth_month": int(row["birth_month"]) if row.get("birth_month") else None,
            "birth_day": int(row["birth_day"]) if row.get("birth_day") else None,
            "mlb_played_first": int(row["mlb_played_first"]) if row.get("mlb_played_first") else None,
            "mlb_played_last": int(row["mlb_played_last"]) if row.get("mlb_played_last") else None,
        }
        _cache_put("player_lookup", name.lower(), info)
        return info
    except Exception as e:
        log.warning("playerid_lookup failed for %r: %s", name, e)
        return None


def compute_age(player_info: dict, ref_date: date | None = None) -> int | None:
    if not player_info:
        return None
    y, m, d = player_info.get("birth_year"), player_info.get("birth_month"), player_info.get("birth_day")
    if not y:
        return None
    ref = ref_date or date.today()
    age = ref.year - y
    if m and d and (ref.month, ref.day) < (m, d):
        age -= 1
    return age


def season_batting_stats(season: int | None = None) -> list[dict]:
    """Full Fangraphs batting board, current season. Cached 1 day."""
    season = season or date.today().year
    cached = _cache_get("batting_stats", str(season), CACHE_TTL_STATS_DAYS)
    if cached is not None:
        return cached
    try:
        pyb = _import_pyb()
        df = pyb.batting_stats(season, qual=0)
        records = _df_to_records(df)
        _cache_put("batting_stats", str(season), records)
        return records
    except Exception as e:
        log.warning("batting_stats(%s) failed: %s", season, e)
        return []


def season_pitching_stats(season: int | None = None) -> list[dict]:
    season = season or date.today().year
    cached = _cache_get("pitching_stats", str(season), CACHE_TTL_STATS_DAYS)
    if cached is not None:
        return cached
    try:
        pyb = _import_pyb()
        df = pyb.pitching_stats(season, qual=0)
        records = _df_to_records(df)
        _cache_put("pitching_stats", str(season), records)
        return records
    except Exception as e:
        log.warning("pitching_stats(%s) failed: %s", season, e)
        return []


def find_in_stats(stats: list[dict], name: str) -> dict | None:
    name_lower = name.lower().strip()
    for row in stats:
        for key in ("Name", "name"):
            if key in row and isinstance(row[key], str) and row[key].lower().strip() == name_lower:
                return row
    return None


KEY_BATTER_STATS = ["G", "PA", "AB", "H", "HR", "R", "RBI", "SB", "BB", "SO", "AVG", "OBP", "SLG", "OPS", "wOBA", "wRC+", "BABIP", "Barrel%", "HardHit%", "EV", "ISO", "BB%", "K%"]
KEY_PITCHER_STATS = ["G", "GS", "IP", "W", "L", "SV", "HLD", "K", "BB", "ERA", "WHIP", "FIP", "xFIP", "SIERA", "K/9", "BB/9", "K-BB%", "Stuff+", "Pitching+", "GB%", "BABIP"]


def slim_stat_row(row: dict | None, kind: str) -> dict | None:
    """Keep only the columns Claude actually needs in the prompt."""
    if not row:
        return None
    keys = KEY_BATTER_STATS if kind == "batter" else KEY_PITCHER_STATS
    out = {"Name": row.get("Name") or row.get("name"), "Team": row.get("Team") or row.get("team")}
    for k in keys:
        if k in row and row[k] is not None:
            out[k] = row[k]
    return out


def _df_to_records(df) -> list[dict]:
    """Coerce a DataFrame to JSON-safe records."""
    if df is None or len(df) == 0:
        return []
    # Replace NaN with None
    import pandas as pd
    df = df.where(pd.notna(df), None)
    records = []
    for r in df.to_dict(orient="records"):
        clean = {}
        for k, v in r.items():
            if hasattr(v, "item"):
                try:
                    v = v.item()
                except Exception:
                    pass
            if isinstance(v, float) and (v != v):  # NaN
                v = None
            clean[str(k)] = v
        records.append(clean)
    return records


def hydrate_roster(roster_rows: list[dict], batting: list[dict], pitching: list[dict]) -> None:
    """Mutate roster rows in place to add age + slim stat row from
    batting/pitching boards. Uses pybaseball player lookup for age."""
    for p in roster_rows:
        name = p.get("name")
        if not name:
            continue

        # Age (cached lookup)
        try:
            info = lookup_player(name)
            if info:
                age = compute_age(info)
                if age is not None:
                    p["age"] = age
                    p["age_source"] = "pybaseball"
                p["mlbam_id"] = info.get("mlbam_id")
        except Exception as e:
            log.debug("age hydrate failed for %s: %s", name, e)

        # Stats — try as batter first, then pitcher
        kind = None
        stat_row = find_in_stats(batting, name)
        if stat_row:
            kind = "batter"
        else:
            stat_row = find_in_stats(pitching, name)
            if stat_row:
                kind = "pitcher"

        if stat_row:
            p["mlb_stats_kind"] = kind
            p["mlb_stats"] = slim_stat_row(stat_row, kind)


def hydrate_age_only(roster_rows: list[dict]) -> None:
    """Used by the cheap daily audit — no full board fetch, just ages."""
    for p in roster_rows:
        name = p.get("name")
        if not name or p.get("age"):
            continue
        try:
            info = lookup_player(name)
            if info:
                age = compute_age(info)
                if age is not None:
                    p["age"] = age
                    p["age_source"] = "pybaseball"
        except Exception as e:
            log.debug("age-only hydrate failed for %s: %s", name, e)
