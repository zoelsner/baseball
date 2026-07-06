"""Normalized per-game league scoring history (the `game_scores` table).

The snapshot blob answers "who is on which roster right now"; this module
answers "what did each player actually score, game by game". The cron calls
`sync_latest()` after every successful refresh so the table accrues history
deterministically: MLB game logs in, league-exact points out, no AI anywhere.

Analytics consumers (`scripts/run_autopsy.py`, `scripts/run_monday_lineup.py`,
future in-app cards) read the table with `sandlot_db.game_scores_between` and
only fall back to live MLB API fetches for players the sync has not covered.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import mlb_stats
import sandlot_db
import sandlot_scoring as scoring
from sandlot_autopsy import PITCHER_TOKENS, eligibility_tokens

log = logging.getLogger(__name__)

FETCH_THREADS = 8
# Re-try players whose name lookup previously failed after this long; rookies
# and call-ups appear in the MLB people index mid-season.
NEGATIVE_RESOLVE_RETRY = timedelta(days=7)


def stat_groups(tokens: set[str]) -> list[str]:
    """Which MLB stat groups a player can produce points in."""
    groups = []
    if tokens - PITCHER_TOKENS:
        groups.append("hitting")
    if tokens & PITCHER_TOKENS:
        groups.append("pitching")
    return groups or ["hitting"]


def snapshot_players(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """fantrax_id -> {name, team, tokens} for every rostered player.

    Covers all 12 team rosters (not just mine) because the autopsy scores the
    whole league. Free agents are out of scope here — the waiver scanner can
    extend this when it lands.
    """
    players: dict[str, dict[str, Any]] = {}
    rosters = [((data.get("roster") or {}).get("rows")) or []]
    for team in (data.get("all_team_rosters") or {}).values():
        rosters.append((team or {}).get("rows") or [])
    for rows in rosters:
        for row in rows:
            fid = row.get("id")
            if not fid or fid in players:
                continue
            players[fid] = {
                "name": row.get("name") or "",
                "team": row.get("team") or "",
                "tokens": eligibility_tokens(row),
            }
    return players


def resolve_mlb_id(fid: str, name: str, team: str, season: int) -> int | None:
    """player_id_map first, then MLB name lookup; write the result back.

    A mapped-but-NULL row is a negative cache; honor it until it is stale so
    each sync does not re-hit the people index for the same misses.
    """
    cached = sandlot_db.get_mlb_id(fid)
    if cached:
        if cached.get("mlb_id"):
            return int(cached["mlb_id"])
        resolved_at = cached.get("resolved_at")
        if resolved_at and datetime.now(timezone.utc) - resolved_at < NEGATIVE_RESOLVE_RETRY:
            return None
    if not name:
        return None
    mlb_id = mlb_stats.lookup_player_by_name(name, team or None, season=season)
    sandlot_db.set_mlb_id(fid, int(mlb_id) if mlb_id else None)
    return int(mlb_id) if mlb_id else None


def score_rows(mlb_id: int, tokens: set[str], season: int) -> list[dict[str, Any]]:
    """`game_scores` rows for one player's full season, league-scored."""
    rows: list[dict[str, Any]] = []
    for group in stat_groups(tokens):
        for game in mlb_stats.fetch_game_log(mlb_id, season=season, group=group):
            if not game.get("date"):
                continue
            rows.append({
                "mlb_id": int(mlb_id),
                "season": season,
                "game_date": game["date"],
                "game_pk": int(game.get("game_pk") or 0),
                "stat_group": group,
                "gs": bool(game.get("gs")),
                "pts": round(scoring.game_points(game, group), 2),
                "stats": {k: v for k, v in game.items()
                          if k not in ("line", "fpts_estimated", "avg_game")},
            })
    return rows


def sync_latest(*, max_workers: int = FETCH_THREADS) -> dict[str, int]:
    """Refresh `game_scores` for everyone rostered in the latest snapshot."""
    snapshot = sandlot_db.latest_successful_snapshot()
    if not snapshot:
        return {"players": 0, "resolved": 0, "rows": 0, "failed": 0}
    data = snapshot.get("data") or {}
    season = (snapshot.get("taken_at") or datetime.now(timezone.utc)).year
    players = snapshot_players(data)

    resolved: dict[str, int] = {}
    for fid, info in players.items():
        try:
            mlb_id = resolve_mlb_id(fid, info["name"], info["team"], season)
        except Exception as exc:  # noqa: BLE001 — one bad lookup shouldn't stop the sync
            log.warning("mlb_id resolve failed for %s: %s", info["name"], exc)
            continue
        if mlb_id:
            resolved[fid] = mlb_id

    failed = 0
    total_rows = 0

    def fetch(fid: str) -> list[dict[str, Any]]:
        return score_rows(resolved[fid], players[fid]["tokens"], season)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {fid: pool.submit(fetch, fid) for fid in resolved}
        for fid, future in futures.items():
            try:
                rows = future.result()
            except Exception as exc:  # noqa: BLE001 — a missing log is data, not fatal
                log.warning("game log sync failed for %s: %s", players[fid]["name"], exc)
                failed += 1
                continue
            total_rows += sandlot_db.upsert_game_scores(rows)

    return {
        "players": len(players),
        "resolved": len(resolved),
        "rows": total_rows,
        "failed": failed,
    }
