"""Frozen, projection-only starting-pitcher cadence evidence.

Posted MLB probables remain the only exact pitcher opportunities Sandlot can
use for lineup action contracts. This module adds a conservative expectation
for the informational matchup projection from MLB-ID-bound, completed player
game logs. The evidence is persisted in the snapshot; replay never refetches.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any, Callable

import mlb_stats
import player_service
import sandlot_db
import sandlot_lineup


MODEL_VERSION = "verified_gs_cadence_v1"
LOOKBACK_DAYS = 30
MAX_LAST_START_AGE_DAYS = 14
MIN_RECENT_STARTS = 2
DEFAULT_WORKERS = 6
INACTIVE_SLOTS = {"BN", "BE", "BENCH", "RES", "RESERVE", "IL", "IR", "MIN", "MINORS"}
UNAVAILABLE = {"OUT", "SUSP", "SUSPENDED", "IL", "IL10", "IL60", "IR"}


IdentityResolver = Callable[[dict[str, Any], int], dict[str, Any]]
GameLogLoader = Callable[[int, int], tuple[list[dict[str, Any]], dict[str, Any]]]
TeamCountFetcher = Callable[..., dict[str, int]]


def enrich_snapshot_pitcher_opportunities(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    identity_resolver: IdentityResolver | None = None,
    game_log_loader: GameLogLoader | None = None,
    team_count_fetcher: TeamCountFetcher | None = None,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    """Attach verified-GS cadence estimates to active matchup SP rows.

    Any upstream failure is isolated to cadence evidence. Existing posted
    probable rows and the rest of the Fantrax refresh remain intact.
    """
    if not isinstance(snapshot, dict):
        return snapshot
    now = _aware_now(now)
    season = now.year
    matchup = snapshot.get("editable_matchup") if isinstance(snapshot.get("editable_matchup"), dict) else None
    if not matchup:
        matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
    period_end = _parse_date(matchup.get("end"))
    period_start = _parse_date(matchup.get("start")) or now.date()
    projection_start = max(period_start, now.date())
    diagnostics = _diagnostics(now, period_end)
    updated = dict(snapshot)
    if period_end is None:
        diagnostics.update({"state": "missing", "reason": "matchup period end is unavailable"})
        updated["pitcher_opportunity_provenance"] = diagnostics
        return updated

    my_rows = ((snapshot.get("roster") or {}).get("rows") or []) if isinstance(snapshot.get("roster"), dict) else []
    opponent_id = str(matchup.get("opponent_team_id") or "")
    all_rosters = snapshot.get("all_team_rosters") if isinstance(snapshot.get("all_team_rosters"), dict) else {}
    opponent = all_rosters.get(opponent_id) if isinstance(all_rosters.get(opponent_id), dict) else {}
    opponent_rows = opponent.get("rows") if isinstance(opponent.get("rows"), list) else []
    candidates = [
        (side, row)
        for side, rows in (("mine", my_rows), ("opponent", opponent_rows))
        for row in rows
        if _active_pitcher(row)
    ]
    diagnostics["active_pitchers"] = len(candidates)
    diagnostics["active_starting_pitchers"] = sum(1 for _, row in candidates if _starting_slot(row))
    diagnostics["active_relievers"] = diagnostics["active_pitchers"] - diagnostics["active_starting_pitchers"]
    if not candidates:
        updated["pitcher_opportunity_provenance"] = diagnostics
        return updated

    history_start = now.date() - timedelta(days=LOOKBACK_DAYS)
    # Game-log rows carry only an official date, not a completion timestamp.
    # End both numerator and denominator at yesterday to keep the as-of window
    # identical after a same-day final or doubleheader.
    history_end = now.date() - timedelta(days=1)
    team_count_fetcher = team_count_fetcher or mlb_stats.fetch_completed_team_game_counts
    try:
        team_games_recent = team_count_fetcher(
            history_start,
            history_end,
            season=season,
            now=now,
        )
    except Exception as exc:  # cadence is optional; the snapshot still succeeds
        diagnostics.update({"state": "partial", "reason": "completed team-game exposure unavailable"})
        diagnostics["errors"].append(f"team_game_counts: {type(exc).__name__}")
        team_games_recent = {}

    identity_resolver = identity_resolver or _resolve_identity
    game_log_loader = game_log_loader or _load_game_log
    resolved: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
    for side, row in candidates:
        if not _starting_slot(row):
            continue
        key = (side, str(row.get("id") or ""))
        try:
            identity = identity_resolver(row, season)
        except Exception as exc:
            identity = {"status": "error", "mlb_id": None, "source": "mlb_identity"}
            diagnostics["errors"].append(f"identity:{row.get('name')}: {type(exc).__name__}")
        mlb_id = identity.get("mlb_id") if isinstance(identity, dict) else None
        if mlb_id is None:
            diagnostics["identity_failures"] += 1
            resolved[key] = (identity, 0)
        else:
            resolved[key] = (identity, int(mlb_id))

    logs: dict[tuple[str, str], tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    loadable = {key: value[1] for key, value in resolved.items() if value[1]}
    max_workers = max(1, min(int(workers or 1), len(loadable) or 1))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pitcher-cadence") as pool:
        future_to_key = {
            pool.submit(game_log_loader, mlb_id, season): key
            for key, mlb_id in loadable.items()
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                logs[key] = future.result()
            except Exception as exc:
                logs[key] = ([], {"state": "error", "error": type(exc).__name__})

    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for side, row in candidates:
        key = (side, str(row.get("id") or ""))
        if not _starting_slot(row):
            evidence[key] = _unmodeled("reliever appearances are not modeled", role="reliever")
            diagnostics["unmodeled_relievers"] += 1
            continue
        identity, mlb_id = resolved.get(key, ({"status": "missing"}, 0))
        if not mlb_id:
            evidence[key] = _unmodeled(
                f"MLB identity {identity.get('status') or 'unavailable'}",
                role="starter",
                identity=identity,
            )
            diagnostics["unmodeled_starters"] += 1
            continue
        games, cache = logs.get(key, ([], {"state": "missing"}))
        estimate = _estimate_row(
            row,
            mlb_id=mlb_id,
            identity=identity,
            games=games,
            cache=cache,
            team_games_recent=team_games_recent,
            history_start=history_start,
            as_of=now,
            projection_start=projection_start,
            period_end=period_end,
        )
        evidence[key] = estimate
        if cache.get("state") in {"error", "stale", "missing"}:
            diagnostics["game_log_failures"] += 1
            if len(diagnostics["errors"]) < 8:
                diagnostics["errors"].append(f"game_log:{row.get('name')}: {cache.get('state')}")
        if estimate.get("state") == "estimated":
            diagnostics["cadence_estimated_starters"] += 1
        else:
            diagnostics["unmodeled_starters"] += 1

    updated["roster"] = _replace_rows(snapshot.get("roster"), "mine", evidence)
    if opponent_id and opponent:
        updated_all = dict(all_rosters)
        updated_all[opponent_id] = _replace_rows(opponent, "opponent", evidence)
        updated["all_team_rosters"] = updated_all
    diagnostics["state"] = "ok" if not diagnostics["errors"] else "partial"
    diagnostics["reason"] = None if diagnostics["state"] == "ok" else "some cadence sources were unavailable"
    updated["pitcher_opportunity_provenance"] = diagnostics
    return updated


def _estimate_row(
    row: dict[str, Any],
    *,
    mlb_id: int,
    identity: dict[str, Any],
    games: list[dict[str, Any]],
    cache: dict[str, Any],
    team_games_recent: dict[str, int],
    history_start: date,
    as_of: datetime,
    projection_start: date,
    period_end: date,
) -> dict[str, Any]:
    as_of_date = as_of.date()
    current_team = mlb_stats._normalize_team(row.get("team")) or ""
    recent = [
        game for game in games
        if isinstance(game, dict)
        and (game_date := _parse_date(game.get("date"))) is not None
        and history_start <= game_date < as_of_date
        and mlb_stats._normalize_team(game.get("team")) == current_team
    ]
    starts = [game for game in recent if game.get("gs") is True]
    latest_start = max((_parse_date(game.get("date")) for game in starts), default=None)
    recent_team_games = int(team_games_recent.get(current_team) or 0)
    future_team_games = _future_team_game_count(row, projection_start, period_end)
    posted = _posted_probable_count(row, projection_start, period_end)
    common = {
        "version": MODEL_VERSION,
        "role": "starter",
        "mlb_id": mlb_id,
        "identity": identity,
        "source": "mlb_stats_game_log.gamesStarted+completed_team_schedule",
        "as_of": as_of.isoformat(),
        "history_window": {"start": history_start.isoformat(), "end_exclusive": as_of_date.isoformat()},
        "period_window": {"start": projection_start.isoformat(), "end": period_end.isoformat()},
        "game_log_cache": _cache_payload(cache),
        "appearances_recent": len(recent),
        "starts_recent": len(starts),
        "latest_start": latest_start.isoformat() if latest_start else None,
        "team_games_recent": recent_team_games,
        "future_team_games": future_team_games,
        "posted_probable_starts": posted,
    }
    if cache.get("state") not in {"fresh", "cached"}:
        return _unmodeled("verified pitching game log unavailable", **common)
    if len(starts) < MIN_RECENT_STARTS:
        return _unmodeled(f"fewer than {MIN_RECENT_STARTS} verified starts in lookback", **common)
    if len(starts) * 2 < len(recent):
        return _unmodeled("recent usage does not establish a starter role", **common)
    if latest_start is None or (as_of_date - latest_start).days > MAX_LAST_START_AGE_DAYS:
        return _unmodeled("latest verified start is stale", **common)
    if recent_team_games <= 0:
        return _unmodeled("completed team-game exposure unavailable", **common)
    if future_team_games <= 0:
        return _unmodeled("no future team games in matchup window", **common)

    raw_rate = len(starts) / recent_team_games
    uncapped = sandlot_lineup.expected_games(
        {"SP"},
        team_games_next=future_team_games,
        team_games_recent=recent_team_games,
        games_recent=len(recent),
        starts_recent=len(starts),
        probable_starts=0,
    )
    expected = min(float(future_team_games), max(float(posted), uncapped))
    return {
        **common,
        "state": "estimated",
        "reason": "Expected starts estimated from verified recent GS cadence; posted probables are the floor.",
        "raw_starts_per_team_game": round(raw_rate, 6),
        "uncapped_expected_starts": round(uncapped, 4),
        "expected_starts": round(expected, 4),
        "estimate_kind": "fractional_expectation",
        "action_eligible": False,
        "probability_release_eligible": False,
    }


def _resolve_identity(row: dict[str, Any], season: int) -> dict[str, Any]:
    identity = mlb_stats.resolve_player_identity(
        str(row.get("name") or ""),
        str(row.get("team") or "") or None,
        season=season,
    )
    fantrax_id = str(row.get("id") or "")
    if identity.get("mlb_id") is not None and fantrax_id:
        sandlot_db.set_mlb_id(fantrax_id, int(identity["mlb_id"]))
    return identity


def _load_game_log(mlb_id: int, season: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    games, cache = player_service.load_player_game_log(
        mlb_id,
        season=season,
        group="pitching",
        refresh_if_stale=True,
    )
    if games and any("gs" not in game or not game.get("team") for game in games if isinstance(game, dict)):
        return player_service.load_player_game_log(
            mlb_id,
            season=season,
            group="pitching",
            force_refresh=True,
        )
    return games, cache


def _replace_rows(container: Any, side: str, evidence: dict[tuple[str, str], dict[str, Any]]) -> Any:
    if not isinstance(container, dict) or not isinstance(container.get("rows"), list):
        return container
    return {
        **container,
        "rows": [
            {
                **row,
                "pitcher_opportunity_estimate": evidence[(side, str(row.get("id") or ""))],
            }
            if isinstance(row, dict) and (side, str(row.get("id") or "")) in evidence
            else row
            for row in container.get("rows") or []
        ],
    }


def _active_pitcher(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    slot = str(row.get("slot") or "").strip().upper()
    if not slot or slot in INACTIVE_SLOTS:
        return False
    status = str(row.get("injury") or row.get("status") or "").strip().upper()
    if status in UNAVAILABLE:
        return False
    return slot in {"SP", "RP", "P"}


def _starting_slot(row: dict[str, Any]) -> bool:
    return str(row.get("slot") or "").strip().upper() in {"SP", "P"}


def _future_team_game_count(row: dict[str, Any], start: date, end: date) -> int:
    return sum(
        1 for game in (row.get("team_future_games") or [])
        if isinstance(game, dict)
        and (game_date := _parse_date(game.get("date"))) is not None
        and start <= game_date <= end
    )


def _posted_probable_count(row: dict[str, Any], start: date, end: date) -> int:
    return sum(
        1 for game in (row.get("future_games") or [])
        if isinstance(game, dict)
        and game.get("probable_start") is True
        and (game_date := _parse_date(game.get("date"))) is not None
        and start <= game_date <= end
    )


def _unmodeled(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "version": MODEL_VERSION,
        "state": "unmodeled",
        "reason": reason,
        "action_eligible": False,
        "probability_release_eligible": False,
        **extra,
    }


def valid_projection_estimate(evidence: Any, period_end: date | None) -> float | None:
    """Return a safe fractional expectation shared by quality and projection."""
    if not isinstance(evidence, dict) or evidence.get("version") != MODEL_VERSION:
        return None
    if evidence.get("state") != "estimated":
        return None
    if evidence.get("action_eligible") is not False or evidence.get("probability_release_eligible") is not False:
        return None
    period = evidence.get("period_window") if isinstance(evidence.get("period_window"), dict) else {}
    if period_end is None or _parse_date(period.get("end")) != period_end:
        return None
    try:
        expected = float(evidence.get("expected_starts"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(expected) or expected < 0:
        return None
    return expected


def _cache_payload(cache: dict[str, Any]) -> dict[str, Any]:
    payload = dict(cache or {})
    fetched_at = payload.get("fetched_at")
    if isinstance(fetched_at, datetime):
        payload["fetched_at"] = _aware_now(fetched_at).isoformat()
    return payload


def _diagnostics(now: datetime, period_end: date | None) -> dict[str, Any]:
    return {
        "version": MODEL_VERSION,
        "source": "mlb_stats_game_log.gamesStarted+completed_team_schedule",
        "as_of": now.isoformat(),
        "period_end": period_end.isoformat() if period_end else None,
        "state": "ok",
        "reason": None,
        "active_pitchers": 0,
        "active_starting_pitchers": 0,
        "active_relievers": 0,
        "cadence_estimated_starters": 0,
        "unmodeled_starters": 0,
        "unmodeled_relievers": 0,
        "identity_failures": 0,
        "game_log_failures": 0,
        "errors": [],
    }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _aware_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
