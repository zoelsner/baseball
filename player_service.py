"""Composes the player-profile payload served by /api/player/{fantrax_id}.

Combines (a) the latest snapshot row for the player with (b) MLB Stats API
game-log data, resolving the fantrax<->mlb id mapping lazily and caching it
plus the per-game log in Postgres. Designed to be called on profile open.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import mlb_stats
import sandlot_db

log = logging.getLogger(__name__)

GAME_LOG_TTL_HOURS = 12
SPARKLINE_GAMES = 14
TREND_GAMES = 7

PITCHING_SLOT_TOKENS = {"SP", "RP", "P"}


class PlayerNotFound(Exception):
    """Raised when fantrax_id isn't present in the latest snapshot."""


def get_player_profile(fantrax_id: str, *, force_refresh: bool = False) -> dict[str, Any]:
    snapshot_row = sandlot_db.latest_successful_snapshot()
    if not snapshot_row:
        raise PlayerNotFound("no snapshot available")
    snapshot = snapshot_row.get("data") or {}
    player_row, source = _find_player(snapshot, fantrax_id)
    if player_row is None:
        raise PlayerNotFound(f"player {fantrax_id} not in snapshot")

    group = _stat_group(player_row)
    season = mlb_stats.current_season()
    payload: dict[str, Any] = {
        "fantrax_id": fantrax_id,
        "player": _player_block(player_row, source),
        "snapshot_taken_at": snapshot_row.get("taken_at"),
        "snapshot_id": snapshot_row.get("id"),
        "snapshot_freshness": _snapshot_freshness(snapshot_row.get("taken_at")),
        "group": group,
        "season": season,
        "mlb": {"available": False, "reason": None},
        "trend": None,
        "sparkline": [],
        "games": [],
    }

    mlb_id = _resolve_mlb_id(fantrax_id, player_row, force_refresh=force_refresh)
    if mlb_id is None:
        payload["mlb"]["reason"] = "MLB stats not available for this player"
        return payload

    games = _load_games(mlb_id, season=season, group=group, force_refresh=force_refresh)
    if not games:
        payload["mlb"].update({"available": True, "reason": "No games logged this season"})
        return payload

    payload["mlb"].update({"available": True, "mlb_id": mlb_id})
    payload["games"] = games
    payload["sparkline"] = _sparkline(games)
    payload["trend"] = _trend(games, group)
    return payload


def force_refresh(fantrax_id: str) -> dict[str, Any]:
    return get_player_profile(fantrax_id, force_refresh=True)


# ---------------------------------------------------------------------------
# Snapshot lookup
# ---------------------------------------------------------------------------

def _find_player(snapshot: dict[str, Any], fantrax_id: str) -> tuple[dict[str, Any] | None, str]:
    roster = (snapshot.get("roster") or {}).get("rows") or []
    for row in roster:
        if (row or {}).get("id") == fantrax_id:
            return row, "my_roster"
    all_rosters = snapshot.get("all_team_rosters") or {}
    for tid, team in all_rosters.items():
        for row in (team or {}).get("rows") or []:
            if (row or {}).get("id") == fantrax_id:
                merged = dict(row)
                merged.setdefault("owner_team_id", tid)
                merged.setdefault("owner_team_name", (team or {}).get("team_name"))
                return merged, "league_roster"
    fa_block = snapshot.get("free_agents") or {}
    for row in fa_block.get("players") or []:
        if (row or {}).get("id") == fantrax_id:
            return row, "free_agent"
    return None, "missing"


def _stat_group(player_row: dict[str, Any]) -> str:
    """Pick hitting vs pitching using exact-token match on slot/positions.

    Substring containment would misfire on slots like 'TWP' (two-way) since
    'P' would match. Split on '/' so 'SP/RP' decomposes into {'SP','RP'}.
    """
    raw_tokens: list[str] = []
    raw_tokens.extend(str(player_row.get("slot") or "").split("/"))
    raw_tokens.extend(str(player_row.get("positions") or "").split("/"))
    for p in (player_row.get("all_positions") or []):
        raw_tokens.extend(str(p or "").split("/"))
    tokens = {t.strip().upper() for t in raw_tokens if t}
    if tokens & PITCHING_SLOT_TOKENS:
        return "pitching"
    return "hitting"


def _player_block(player_row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "id": player_row.get("id"),
        "name": player_row.get("name"),
        "team": player_row.get("team"),
        "positions": player_row.get("positions"),
        "all_positions": player_row.get("all_positions"),
        "slot": player_row.get("slot"),
        "slot_full": player_row.get("slot_full"),
        "fpts": player_row.get("fpts"),
        "fppg": player_row.get("fppg"),
        "age": player_row.get("age"),
        "injury": player_row.get("injury"),
        "owner_team_id": player_row.get("owner_team_id"),
        "owner_team_name": player_row.get("owner_team_name"),
        "source": source,
    }


# ---------------------------------------------------------------------------
# MLB id + game log resolution (cache-aware)
# ---------------------------------------------------------------------------

def _resolve_mlb_id(fantrax_id: str, player_row: dict[str, Any], *, force_refresh: bool) -> int | None:
    if not force_refresh:
        cached = sandlot_db.get_mlb_id(fantrax_id)
        if cached is not None:
            return cached.get("mlb_id")
    name = player_row.get("name") or ""
    team = player_row.get("team")
    mlb_id = mlb_stats.lookup_player_by_name(name, team)
    sandlot_db.set_mlb_id(fantrax_id, mlb_id)
    return mlb_id


def _load_games(mlb_id: int, *, season: int, group: str, force_refresh: bool) -> list[dict[str, Any]]:
    if not force_refresh:
        cached = sandlot_db.get_player_game_log(mlb_id)
        if cached and cached.get("group_type") == group and _fresh_enough(cached):
            return cached.get("games") or []
    try:
        games = mlb_stats.fetch_game_log(mlb_id, season=season, group=group)
    except Exception as exc:
        log.warning("MLB game log fetch failed for %s: %s", mlb_id, exc)
        cached = sandlot_db.get_player_game_log(mlb_id)
        if cached and cached.get("group_type") == group:
            return cached.get("games") or []
        return []
    sandlot_db.set_player_game_log(mlb_id, group_type=group, season=season, games=games)
    return games


def _snapshot_freshness(taken_at: Any) -> dict[str, Any]:
    if not isinstance(taken_at, datetime):
        return {"state": "missing", "age_minutes": None}
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((datetime.now(timezone.utc) - taken_at).total_seconds() / 60))
    if age_minutes <= 30:
        state = "fresh"
    elif age_minutes <= 24 * 60:
        state = "stale"
    else:
        state = "old"
    return {"state": state, "age_minutes": age_minutes}


def _fresh_enough(cached: dict[str, Any]) -> bool:
    fetched_at = cached.get("fetched_at")
    if not isinstance(fetched_at, datetime):
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0
    return age_hours < GAME_LOG_TTL_HOURS


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

def _sparkline(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last = games[-SPARKLINE_GAMES:]
    return [
        {
            "date": g.get("date"),
            "opponent": g.get("opponent"),
            "fpts": g.get("fpts_estimated", 0.0),
        }
        for g in last
    ]


def _trend(games: list[dict[str, Any]], group: str) -> dict[str, Any]:
    fpts_all = [g.get("fpts_estimated", 0.0) for g in games]
    season_avg = (sum(fpts_all) / len(fpts_all)) if fpts_all else 0.0
    last = games[-TREND_GAMES:]
    last_fpts = [g.get("fpts_estimated", 0.0) for g in last]
    last_avg = (sum(last_fpts) / len(last_fpts)) if last_fpts else 0.0
    pct_change = None
    if season_avg:
        pct_change = round(((last_avg - season_avg) / abs(season_avg)) * 100, 1)
    last_batting = None
    if group == "hitting":
        ab = sum((g.get("ab") or 0) for g in last)
        h = sum((g.get("h") or 0) for g in last)
        if ab:
            last_batting = round(h / ab, 3)
    return {
        "window": len(last),
        "last_avg_fpts": round(last_avg, 2),
        "season_avg_fpts": round(season_avg, 2),
        "pct_change": pct_change,
        "last_batting_avg": last_batting,
        "direction": "up" if (pct_change or 0) > 0 else "down" if (pct_change or 0) < 0 else "flat",
    }
