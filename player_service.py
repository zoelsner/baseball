"""Composes the player-profile payload served by /api/player/{fantrax_id}.

Normal profile opens are intentionally cache-first: the API should return the
Fantrax snapshot row and any cached Postgres profile data without waiting on
MLB Stats API or OpenRouter. Explicit refreshes and cron warmups populate those
caches out-of-band.
"""

from __future__ import annotations

import logging
import json
import os
from datetime import datetime, timezone
from typing import Any

import mlb_stats
import sandlot_db
import sandlot_skipper

log = logging.getLogger(__name__)

GAME_LOG_TTL_HOURS = 12
MEDIA_TTL_HOURS = 12
SPARKLINE_GAMES = 14
TREND_GAMES = 7
DEFAULT_WARM_LIMIT = 30

PITCHING_SLOT_TOKENS = {"SP", "RP", "P"}

TAKE_SYSTEM_PROMPT = """You are Skipper, a fantasy baseball assistant for a 12-team Fantrax keeper league.

Write a roster-aware player take for the user's player sheet.

Rules:
- Use only the supplied JSON. Do not invent injuries, news, lineups, matchups, or transactions.
- Be concise and direct: 2-3 sentences, no markdown, no bullets.
- Cite useful numbers when available: FP/G, total points, recent trend, age, slot, or roster context.
- If MLB game-log data is unavailable, still give the best snapshot-based read and mention the limitation naturally.
- Do not recommend Fantrax write actions such as drops, claims, trades, or lineup moves."""


class PlayerNotFound(Exception):
    """Raised when fantrax_id isn't present in the latest snapshot."""


def get_player_profile(
    fantrax_id: str,
    *,
    force_refresh: bool = False,
    generate_take: bool | None = None,
) -> dict[str, Any]:
    """Return a player profile payload.

    `force_refresh=False` is the fast path and only reads Postgres caches.
    `force_refresh=True` may call MLB Stats API and, by default, OpenRouter.
    """
    if generate_take is None:
        generate_take = force_refresh

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
        "media": {"items": [], "cache": {"state": "missing", "fetched_at": None, "age_hours": None}},
        "profile_cache": {
            "mode": "refresh" if force_refresh else "cache",
            "needs_refresh": False,
            "game_log": {"state": "missing", "fetched_at": None, "age_hours": None},
            "media": {"state": "missing", "fetched_at": None, "age_hours": None},
            "take": {"state": "missing"},
        },
        "take": {"text": None, "model": None, "generated_at": None, "error": None},
    }

    mlb_id = _resolve_mlb_id(fantrax_id, player_row, force_refresh=force_refresh)
    if mlb_id is None:
        cached = sandlot_db.get_mlb_id(fantrax_id) if not force_refresh else None
        if cached is None and not force_refresh:
            payload["mlb"]["reason"] = "MLB ID not cached yet"
            payload["profile_cache"]["needs_refresh"] = True
        else:
            payload["mlb"]["reason"] = "MLB stats not available for this player"
    else:
        games, game_cache = _load_games(mlb_id, season=season, group=group, force_refresh=force_refresh)
        payload["profile_cache"]["game_log"] = game_cache
        if not games:
            reason = "No games logged this season" if force_refresh else "MLB game log not cached yet"
            payload["mlb"].update({"available": True, "mlb_id": mlb_id, "reason": reason})
            if not force_refresh:
                payload["profile_cache"]["needs_refresh"] = True
        else:
            payload["mlb"].update({"available": True, "mlb_id": mlb_id})
            if game_cache.get("state") == "stale":
                payload["mlb"]["reason"] = "Showing stale cached MLB game log"
                payload["profile_cache"]["needs_refresh"] = True
            payload["games"] = games
            payload["sparkline"] = _sparkline(games)
            payload["trend"] = _trend(games, group)
        media_items, media_cache = _load_media(
            mlb_id,
            player_name=str(player_row.get("name") or ""),
            games=games,
            force_refresh=force_refresh,
        )
        payload["media"] = {"items": media_items, "cache": media_cache}
        payload["profile_cache"]["media"] = media_cache
        if media_cache.get("state") in {"missing", "stale"} and not force_refresh:
            payload["profile_cache"]["needs_refresh"] = True
    if generate_take:
        payload["take"] = _load_or_generate_take(
            fantrax_id=fantrax_id,
            snapshot_row=snapshot_row,
            snapshot=snapshot,
            player_row=player_row,
            payload=payload,
        )
    else:
        payload["take"] = _load_cached_take(fantrax_id, snapshot_row)
    payload["profile_cache"]["take"] = _take_cache_state(payload["take"])
    return payload


def force_refresh(fantrax_id: str) -> dict[str, Any]:
    return get_player_profile(fantrax_id, force_refresh=True)


def refresh_cached_profile(fantrax_id: str, *, generate_take: bool = False) -> dict[str, Any] | None:
    """Best-effort cache warmer for background tasks.

    Errors are logged and swallowed so a background warm cannot break the page
    request that scheduled it.
    """
    try:
        return get_player_profile(fantrax_id, force_refresh=True, generate_take=generate_take)
    except Exception as exc:
        log.warning("Player profile warm failed for %s: %s", fantrax_id, exc)
        return None


def warm_roster_profiles(
    *,
    snapshot_id: int | None = None,
    limit: int | None = None,
    generate_takes: bool = False,
) -> dict[str, Any]:
    """Pre-warm roster MLB profile caches after a snapshot refresh."""
    snapshot_row = sandlot_db.latest_successful_snapshot()
    if not snapshot_row:
        return {"attempted": 0, "warmed": 0, "failed": 0, "errors": ["no snapshot available"]}
    if snapshot_id is not None and int(snapshot_row.get("id") or 0) != int(snapshot_id):
        log.info(
            "Requested profile warm for snapshot_id=%s; latest is %s",
            snapshot_id,
            snapshot_row.get("id"),
        )

    snapshot = snapshot_row.get("data") or {}
    rows = (snapshot.get("roster") or {}).get("rows") or []
    limit = limit if limit is not None else int(os.environ.get("SANDLOT_PROFILE_WARM_LIMIT", str(DEFAULT_WARM_LIMIT)))
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fantrax_id = row.get("id")
        if fantrax_id and fantrax_id not in ids:
            ids.append(str(fantrax_id))
        if len(ids) >= limit:
            break

    warmed = 0
    errors: list[str] = []
    for fantrax_id in ids:
        if refresh_cached_profile(fantrax_id, generate_take=generate_takes) is None:
            errors.append(fantrax_id)
        else:
            warmed += 1
    return {"attempted": len(ids), "warmed": warmed, "failed": len(errors), "errors": errors[:10]}


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


def _load_games(
    mlb_id: int,
    *,
    season: int,
    group: str,
    force_refresh: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cached = sandlot_db.get_player_game_log(mlb_id)
    if not force_refresh:
        if cached and cached.get("group_type") == group and cached.get("season") == season:
            return cached.get("games") or [], _game_log_cache_state(cached)
        return [], {"state": "missing", "fetched_at": None, "age_hours": None}
    try:
        games = mlb_stats.fetch_game_log(mlb_id, season=season, group=group)
    except Exception as exc:
        log.warning("MLB game log fetch failed for %s: %s", mlb_id, exc)
        if cached and cached.get("group_type") == group and cached.get("season") == season:
            return cached.get("games") or [], _game_log_cache_state(cached, fallback_error=str(exc))
        return [], {"state": "error", "fetched_at": None, "age_hours": None, "error": str(exc)}
    sandlot_db.set_player_game_log(mlb_id, group_type=group, season=season, games=games)
    fresh_state = {"state": "fresh", "fetched_at": datetime.now(timezone.utc), "age_hours": 0.0}
    return games, fresh_state


def _load_cached_take(fantrax_id: str, snapshot_row: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = snapshot_row.get("id")
    if not snapshot_id:
        return {"text": None, "model": None, "generated_at": None, "error": "No snapshot id available", "cached": False}
    try:
        cached = sandlot_db.get_player_take(fantrax_id, int(snapshot_id))
    except Exception as exc:
        log.warning("Player take cache read failed for %s/%s: %s", fantrax_id, snapshot_id, exc)
        return {"text": None, "model": None, "generated_at": None, "error": str(exc), "cached": False}
    if not cached:
        return {"text": None, "model": None, "generated_at": None, "error": None, "cached": False}
    return {
        "text": cached.get("text"),
        "model": cached.get("model"),
        "generated_at": cached.get("generated_at"),
        "error": None,
        "cached": True,
    }


def _load_media(
    mlb_id: int,
    *,
    player_name: str,
    games: list[dict[str, Any]],
    force_refresh: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cached = sandlot_db.get_player_media(mlb_id)
    if not force_refresh:
        if cached:
            return cached.get("items") or [], _generic_cache_state(cached, ttl_hours=MEDIA_TTL_HOURS)
        return [], {"state": "missing", "fetched_at": None, "age_hours": None}
    try:
        items = mlb_stats.fetch_player_media(mlb_id, player_name, games)
    except Exception as exc:
        log.warning("MLB media fetch failed for %s: %s", mlb_id, exc)
        if cached:
            return cached.get("items") or [], _generic_cache_state(cached, ttl_hours=MEDIA_TTL_HOURS, fallback_error=str(exc))
        return [], {"state": "error", "fetched_at": None, "age_hours": None, "error": str(exc)}
    sandlot_db.set_player_media(mlb_id, items)
    return items, {"state": "fresh", "fetched_at": datetime.now(timezone.utc), "age_hours": 0.0}


def _load_or_generate_take(
    *,
    fantrax_id: str,
    snapshot_row: dict[str, Any],
    snapshot: dict[str, Any],
    player_row: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    snapshot_id = snapshot_row.get("id")
    if not snapshot_id:
        return {"text": None, "model": None, "generated_at": None, "error": "No snapshot id available"}

    try:
        cached = sandlot_db.get_player_take(fantrax_id, int(snapshot_id))
    except Exception as exc:
        log.warning("Player take cache read failed for %s/%s: %s", fantrax_id, snapshot_id, exc)
        cached = None

    if cached:
        return {
            "text": cached.get("text"),
            "model": cached.get("model"),
            "generated_at": cached.get("generated_at"),
            "error": None,
            "cached": True,
        }

    try:
        messages = _build_take_messages(snapshot, player_row, payload)
        text, model = sandlot_skipper.SkipperClient().complete(messages, max_tokens=220)
        try:
            sandlot_db.set_player_take(fantrax_id, int(snapshot_id), text, model)
        except Exception as exc:
            log.warning("Player take cache write failed for %s/%s: %s", fantrax_id, snapshot_id, exc)
        return {
            "text": text,
            "model": model,
            "generated_at": datetime.now(timezone.utc),
            "error": None,
            "cached": False,
        }
    except Exception as exc:
        log.warning("Player take generation failed for %s/%s: %s", fantrax_id, snapshot_id, exc)
        return {"text": None, "model": None, "generated_at": None, "error": str(exc), "cached": False}


def _build_take_messages(
    snapshot: dict[str, Any],
    player_row: dict[str, Any],
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    context = {
        "snapshot_taken_at": snapshot.get("timestamp"),
        "team_name": snapshot.get("team_name"),
        "target_player": payload.get("player"),
        "mlb": payload.get("mlb"),
        "trend": payload.get("trend"),
        "recent_games": (payload.get("games") or [])[-7:],
        "roster_context": _take_roster_context(snapshot, player_row),
    }
    return [
        {"role": "system", "content": TAKE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Write the player-sheet Skipper take from this JSON:\n"
            + json.dumps(context, default=str, indent=2),
        },
    ]


def _take_roster_context(snapshot: dict[str, Any], player_row: dict[str, Any]) -> dict[str, Any]:
    roster_rows = (snapshot.get("roster") or {}).get("rows") or []
    target_positions = _position_tokens(player_row)
    same_position: list[dict[str, Any]] = []
    slot_counts: dict[str, int] = {}
    for row in roster_rows:
        if not isinstance(row, dict):
            continue
        slot = str(row.get("slot") or "BN").upper()
        slot_counts[slot] = slot_counts.get(slot, 0) + 1
        if row.get("id") == player_row.get("id"):
            continue
        if target_positions and not (_position_tokens(row) & target_positions):
            continue
        same_position.append(_slim_take_player(row))
    same_position.sort(key=lambda r: (r.get("fppg") is None, -(r.get("fppg") or 0)))
    return {
        "active": (snapshot.get("roster") or {}).get("active"),
        "active_max": (snapshot.get("roster") or {}).get("active_max"),
        "reserve": (snapshot.get("roster") or {}).get("reserve"),
        "reserve_max": (snapshot.get("roster") or {}).get("reserve_max"),
        "target_positions": sorted(target_positions),
        "slot_counts": slot_counts,
        "same_position_players": same_position[:8],
    }


def _position_tokens(player_row: dict[str, Any]) -> set[str]:
    tokens: list[str] = []
    tokens.extend(str(player_row.get("slot") or "").split("/"))
    tokens.extend(str(player_row.get("positions") or "").split("/"))
    for p in (player_row.get("all_positions") or []):
        tokens.extend(str(p or "").split("/"))
    return {t.strip().upper() for t in tokens if t and t.strip().upper() not in {"BN", "IL", "IR"}}


def _slim_take_player(player_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": player_row.get("id"),
        "name": player_row.get("name"),
        "slot": player_row.get("slot"),
        "positions": player_row.get("positions"),
        "team": player_row.get("team"),
        "fppg": _take_number(player_row.get("fppg")),
        "fpts": _take_number(player_row.get("fpts")),
        "age": player_row.get("age"),
        "injury": player_row.get("injury"),
    }


def _take_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


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


def _fresh_enough(cached: dict[str, Any], *, ttl_hours: int = GAME_LOG_TTL_HOURS) -> bool:
    fetched_at = cached.get("fetched_at")
    if not isinstance(fetched_at, datetime):
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0
    return age_hours < ttl_hours


def _game_log_cache_state(cached: dict[str, Any], *, fallback_error: str | None = None) -> dict[str, Any]:
    return _generic_cache_state(cached, ttl_hours=GAME_LOG_TTL_HOURS, fallback_error=fallback_error)


def _generic_cache_state(
    cached: dict[str, Any],
    *,
    ttl_hours: int,
    fallback_error: str | None = None,
) -> dict[str, Any]:
    fetched_at = cached.get("fetched_at")
    age_hours = None
    if isinstance(fetched_at, datetime):
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_hours = round(max(0.0, (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0), 2)
    state = "fresh" if _fresh_enough(cached, ttl_hours=ttl_hours) else "stale"
    result = {"state": state, "fetched_at": fetched_at, "age_hours": age_hours}
    if fallback_error:
        result["refresh_error"] = fallback_error
    return result


def _take_cache_state(take: dict[str, Any]) -> dict[str, Any]:
    if take.get("text"):
        return {
            "state": "fresh" if take.get("cached") is False else "cached",
            "generated_at": take.get("generated_at"),
            "model": take.get("model"),
        }
    if take.get("error"):
        return {"state": "error", "error": take.get("error")}
    return {"state": "missing"}


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
