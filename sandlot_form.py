"""MLB enrichment that runs after the Fantrax scrape.

Bulk-warms recent game logs for the user's roster + opponent's roster, plus
fetches probable pitchers for the remaining matchup days. Both products are
attached to the snapshot blob before insert so Skipper and the win-prob calc
can read them in one trip.

Kept separate from `fantrax_data` (which is Fantrax-only) and `mlb_stats`
(which is the raw MLB API client). This module is the glue.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, datetime, timezone
from typing import Any, Iterable

import mlb_stats
import sandlot_db

log = logging.getLogger(__name__)

RECENT_GAMES_KEEP = 10
DEFAULT_PARALLELISM = 8
PITCHER_SLOTS = {"P", "SP", "RP"}


def enrich_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Mutate snapshot in place: add `probable_pitchers` and `mlb_recent_games`.

    Returns the same snapshot for convenience. Network failures degrade
    gracefully — we attach an empty dict + an error note rather than blocking
    the whole refresh.
    """
    errors: list[str] = []

    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    start, end = _matchup_window(matchup)
    if start and end:
        try:
            snapshot["probable_pitchers"] = mlb_stats.probable_pitchers_for_dates(start, end)
        except Exception as exc:
            log.warning("probable_pitchers enrichment failed: %s", exc)
            errors.append(f"probable_pitchers: {exc}")
            snapshot["probable_pitchers"] = {"by_date": {}, "by_pitcher_mlb_id": {}, "fetched_for": []}
    else:
        snapshot["probable_pitchers"] = {"by_date": {}, "by_pitcher_mlb_id": {}, "fetched_for": []}

    try:
        snapshot["mlb_recent_games"] = _bulk_recent_games(snapshot)
    except Exception as exc:
        log.exception("mlb_recent_games enrichment failed")
        errors.append(f"mlb_recent_games: {exc}")
        snapshot["mlb_recent_games"] = {}

    if errors:
        snapshot.setdefault("errors", []).extend(errors)
    return snapshot


def _matchup_window(matchup: dict[str, Any] | None) -> tuple[_date | None, _date | None]:
    if not matchup:
        return None, None
    start = _parse_date(matchup.get("start"))
    end = _parse_date(matchup.get("end"))
    if not start or not end:
        return None, None
    return start, end


def _parse_date(value: Any) -> _date | None:
    if not value:
        return None
    if isinstance(value, _date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return _date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _bulk_recent_games(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """For my roster + opponent roster, return last N games per fantrax_id.

    Output shape: {fantrax_id: [{date, fpts_estimated, group, line, ...}, ...]}.
    Uses player_id_map cache + player_game_logs cache to avoid redundant
    network calls. Stores any newly-fetched logs back into the cache.
    """
    rows = list(_rosters_to_warm(snapshot))
    if not rows:
        return {}

    parallelism = max(1, min(int(os.environ.get("SANDLOT_FORM_PARALLELISM", str(DEFAULT_PARALLELISM))), len(rows)))
    season = mlb_stats.current_season()
    out: dict[str, list[dict[str, Any]]] = {}

    if parallelism <= 1:
        for fid, name, team, group in rows:
            games = _fetch_for_player(fid, name, team, group, season)
            if games is not None:
                out[fid] = games
        return out

    with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix="form-warm") as ex:
        future_to_id = {
            ex.submit(_fetch_for_player, fid, name, team, group, season): fid
            for fid, name, team, group in rows
        }
        for fut in as_completed(future_to_id):
            fid = future_to_id[fut]
            try:
                games = fut.result()
            except Exception as exc:
                log.warning("form-warm raised for %s: %s", fid, exc)
                continue
            if games is not None:
                out[fid] = games
    return out


def _rosters_to_warm(snapshot: dict[str, Any]) -> Iterable[tuple[str, str, str | None, str]]:
    """Yield (fantrax_id, name, team, stat_group) for my + opponent rosters."""
    seen: set[str] = set()

    my_rows = ((snapshot.get("roster") or {}).get("rows")) or []
    for row in my_rows:
        for tup in _row_to_warm_tuple(row, seen):
            yield tup

    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    opp_id = (matchup or {}).get("opponent_team_id")
    all_rosters = snapshot.get("all_team_rosters") or {}
    opponent = all_rosters.get(opp_id) if opp_id else None
    if isinstance(opponent, dict):
        for row in opponent.get("rows") or []:
            for tup in _row_to_warm_tuple(row, seen):
                yield tup


def _row_to_warm_tuple(
    row: dict[str, Any],
    seen: set[str],
) -> Iterable[tuple[str, str, str | None, str]]:
    if not isinstance(row, dict):
        return
    fid = row.get("id")
    name = row.get("name")
    if not fid or not name or fid in seen:
        return
    seen.add(str(fid))
    group = _stat_group(row)
    yield str(fid), str(name), row.get("team"), group


def _stat_group(row: dict[str, Any]) -> str:
    """Pitcher vs hitter detection mirrors player_service._stat_group lightly."""
    tokens: set[str] = set()
    for field in (row.get("slot"), row.get("positions")):
        if isinstance(field, str):
            tokens.update(t.strip().upper() for t in field.split(",") if t.strip())
    for pos in row.get("all_positions") or []:
        if isinstance(pos, str):
            tokens.add(pos.strip().upper())
    if tokens & PITCHER_SLOTS:
        return "pitching"
    return "hitting"


def _fetch_for_player(
    fantrax_id: str,
    name: str,
    team: str | None,
    group: str,
    season: int,
) -> list[dict[str, Any]] | None:
    """Resolve mlb_id, pull cached/fresh game log, return last N slim games."""
    try:
        mlb_id = _resolve_mlb_id(fantrax_id, name, team)
    except Exception as exc:
        log.warning("mlb_id resolve raised for %s (%s): %s", fantrax_id, name, exc)
        return None
    if not mlb_id:
        return None

    cached = sandlot_db.get_player_game_log(mlb_id)
    games: list[dict[str, Any]] | None = None
    if cached and cached.get("group_type") == group and cached.get("season") == season:
        fetched_at = cached.get("fetched_at")
        if _fresh_enough(fetched_at):
            games = cached.get("games") or []

    if games is None:
        try:
            games = mlb_stats.fetch_game_log(mlb_id, season=season, group=group)
        except Exception as exc:
            log.warning("game log fetch failed for mlb_id=%s (%s): %s", mlb_id, name, exc)
            if cached and cached.get("group_type") == group:
                games = cached.get("games") or []
            else:
                games = []
        else:
            try:
                sandlot_db.set_player_game_log(mlb_id, group_type=group, season=season, games=games)
            except Exception as exc:
                log.warning("set_player_game_log failed for %s: %s", mlb_id, exc)

    return _slim_games(games, group)[-RECENT_GAMES_KEEP:]


def _resolve_mlb_id(fantrax_id: str, name: str, team: str | None) -> int | None:
    cached = sandlot_db.get_mlb_id(fantrax_id)
    if cached is not None:
        return cached.get("mlb_id")
    mlb_id = mlb_stats.lookup_player_by_name(name, team)
    try:
        sandlot_db.set_mlb_id(fantrax_id, mlb_id)
    except Exception as exc:
        log.warning("set_mlb_id failed for %s: %s", fantrax_id, exc)
    return mlb_id


def _fresh_enough(fetched_at: Any, ttl_hours: float = 6.0) -> bool:
    if not isinstance(fetched_at, datetime):
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0
    return age <= ttl_hours


def _slim_games(games: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    """Strip game logs to the fields winprob + Skipper actually need."""
    slim: list[dict[str, Any]] = []
    for g in games or []:
        if not isinstance(g, dict):
            continue
        slim.append({
            "date": g.get("date"),
            "opponent": g.get("opponent"),
            "home": g.get("home"),
            "fpts_estimated": g.get("fpts_estimated"),
            "group": group,
        })
    return slim
