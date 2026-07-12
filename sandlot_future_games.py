"""MLB schedule enrichment for Sandlot roster rows.

This module keeps schedule provenance separate from projection semantics:
hitters get remaining team games as countable `future_games`; pitchers get
only explicit probable-start rows there, with team schedule context stored
separately.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

import mlb_stats


SCHEDULE_SOURCE = "mlb_schedule"
OK_FUTURE_GAME_STATUSES = {"ok", "pitcher_probables_unavailable"}
FAILED_FUTURE_GAME_STATUSES = {"fetch_error", "unresolved_team", "window_missing"}

HITTER_POSITIONS = {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF", "UTIL", "UT"}
PITCHER_POSITIONS = {"P", "SP", "RP"}
POSITION_ALIASES = {"LF": "OF", "CF": "OF", "RF": "OF", "UTIL": "UT"}


ScheduleFetcher = Callable[..., list[dict[str, Any]]]
TeamResolver = Callable[[Optional[str], Optional[int]], Optional[int]]


def enrich_snapshot_future_games(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    season: int | None = None,
    schedule_fetcher: ScheduleFetcher | None = None,
    team_resolver: TeamResolver | None = None,
) -> dict[str, Any]:
    """Return a snapshot copy with roster rows enriched from MLB schedule data."""
    if not isinstance(snapshot, dict):
        return snapshot
    now = _aware_now(now)
    current_matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
    editable_matchup = snapshot.get("editable_matchup") if isinstance(snapshot.get("editable_matchup"), dict) else {}
    matchup = editable_matchup or current_matchup
    window = _schedule_window(matchup, now)
    diagnostics = _empty_diagnostics(window)

    updated = dict(snapshot)
    if not window:
        updated["future_games_provenance"] = diagnostics
        return updated

    schedule_fetcher = schedule_fetcher or mlb_stats.fetch_team_schedule
    team_resolver = team_resolver or mlb_stats.team_id_by_abbreviation
    schedule_cache: dict[int, tuple[list[dict[str, Any]], str | None]] = {}

    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else None
    if roster and isinstance(roster.get("rows"), list):
        updated["roster"] = {
            **roster,
            "rows": [
                _enrich_row(
                    row,
                    window=window,
                    season=season,
                    schedule_fetcher=schedule_fetcher,
                    team_resolver=team_resolver,
                    schedule_cache=schedule_cache,
                    diagnostics=diagnostics,
                )
                for row in roster.get("rows") or []
            ],
        }

    all_rosters = snapshot.get("all_team_rosters")
    if isinstance(all_rosters, dict):
        enriched_rosters: dict[str, Any] = {}
        for team_id, team in all_rosters.items():
            if not isinstance(team, dict):
                enriched_rosters[team_id] = team
                continue
            rows = team.get("rows")
            if not isinstance(rows, list):
                enriched_rosters[team_id] = team
                continue
            enriched_rosters[team_id] = {
                **team,
                "rows": [
                    _enrich_row(
                        row,
                        window=window,
                        season=season,
                        schedule_fetcher=schedule_fetcher,
                        team_resolver=team_resolver,
                        schedule_cache=schedule_cache,
                        diagnostics=diagnostics,
                    )
                    for row in rows
                ],
            }
        updated["all_team_rosters"] = enriched_rosters

    free_agents = snapshot.get("free_agents")
    if isinstance(free_agents, dict) and isinstance(free_agents.get("players"), list):
        updated["free_agents"] = {
            **free_agents,
            "players": [
                _enrich_row(
                    row,
                    window=window,
                    season=season,
                    schedule_fetcher=schedule_fetcher,
                    team_resolver=team_resolver,
                    schedule_cache=schedule_cache,
                    diagnostics=diagnostics,
                )
                for row in free_agents.get("players") or []
            ],
        }

    diagnostics["schedule_fetch_count"] = len(schedule_cache)
    updated["future_games_provenance"] = diagnostics
    return updated


def _enrich_row(
    row: Any,
    *,
    window: dict[str, date],
    season: int | None,
    schedule_fetcher: ScheduleFetcher,
    team_resolver: TeamResolver,
    schedule_cache: dict[int, tuple[list[dict[str, Any]], str | None]],
    diagnostics: dict[str, Any],
) -> Any:
    if not isinstance(row, dict):
        return row

    updated = dict(row)
    diagnostics["rows_seen"] += 1
    fantrax_abbr = _team_abbr(row)
    if not fantrax_abbr:
        diagnostics["missing_team_rows"] += 1
        if _future_game_count(row) > 0:
            diagnostics["rows_preserved_existing_future_games"] += 1
            return updated
        return _mark_failed(
            updated,
            status="unresolved_team",
            reason="player row has no MLB team abbreviation",
            team_abbr=None,
            team_id=None,
            window=window,
            diagnostics=diagnostics,
        )

    mlb_team_id = team_resolver(fantrax_abbr, season)
    if mlb_team_id is None:
        diagnostics["unmapped_team_abbrs"][fantrax_abbr] = diagnostics["unmapped_team_abbrs"].get(fantrax_abbr, 0) + 1
        return _mark_failed(
            updated,
            status="unresolved_team",
            reason=f"could not resolve Fantrax team abbreviation {fantrax_abbr}",
            team_abbr=fantrax_abbr,
            team_id=None,
            window=window,
            diagnostics=diagnostics,
        )

    games, fetch_error = _schedule_for_team(
        int(mlb_team_id),
        window=window,
        season=season,
        schedule_fetcher=schedule_fetcher,
        schedule_cache=schedule_cache,
    )
    if fetch_error:
        diagnostics["schedule_fetch_failures"] += 1
        return _mark_failed(
            updated,
            status="fetch_error",
            reason=fetch_error,
            team_abbr=fantrax_abbr,
            team_id=int(mlb_team_id),
            window=window,
            diagnostics=diagnostics,
        )

    player_kind = _player_kind(row)
    base_fields = {
        "future_games_source": SCHEDULE_SOURCE,
        "future_games_status": "ok",
        "future_games_team": fantrax_abbr,
        "future_games_team_id": int(mlb_team_id),
        "future_games_window": _window_json(window),
    }
    if player_kind == "pitcher":
        probable_starts = [_pitcher_game(row, game) for game in games]
        probable_starts = [game for game in probable_starts if game]
        updated.update(base_fields)
        updated["future_games_scope"] = "pitcher_probable_starts"
        updated["future_games"] = probable_starts
        updated["team_future_games"] = games
        updated["future_games_count"] = len(probable_starts)
        if not probable_starts:
            updated["future_games_status"] = "pitcher_probables_unavailable"
        diagnostics["pitcher_rows"] += 1
        diagnostics["countable_future_games"] += len(probable_starts)
    else:
        updated.update(base_fields)
        updated["future_games_scope"] = "team_games"
        updated["future_games"] = games
        updated["future_games_count"] = len(games)
        diagnostics["hitter_rows"] += 1
        diagnostics["countable_future_games"] += len(games)

    diagnostics["mapped_rows"] += 1
    diagnostics["status_counts"][updated["future_games_status"]] = (
        diagnostics["status_counts"].get(updated["future_games_status"], 0) + 1
    )
    return updated


def _schedule_for_team(
    team_id: int,
    *,
    window: dict[str, date],
    season: int | None,
    schedule_fetcher: ScheduleFetcher,
    schedule_cache: dict[int, tuple[list[dict[str, Any]], str | None]],
) -> tuple[list[dict[str, Any]], str | None]:
    if team_id not in schedule_cache:
        try:
            games = schedule_fetcher(team_id, window["start"], window["end"], season=season, now=window["now"])
            schedule_cache[team_id] = (list(games or []), None)
        except Exception as exc:
            schedule_cache[team_id] = ([], str(exc))
    return schedule_cache[team_id]


def _mark_failed(
    row: dict[str, Any],
    *,
    status: str,
    reason: str,
    team_abbr: str | None,
    team_id: int | None,
    window: dict[str, date],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    row.update({
        "future_games": [],
        "future_games_source": SCHEDULE_SOURCE,
        "future_games_status": status,
        "future_games_reason": reason,
        "future_games_team": team_abbr,
        "future_games_team_id": team_id,
        "future_games_window": _window_json(window),
        "future_games_count": 0,
    })
    diagnostics["failed_rows"] += 1
    diagnostics["status_counts"][status] = diagnostics["status_counts"].get(status, 0) + 1
    return row


def _pitcher_game(row: dict[str, Any], game: dict[str, Any]) -> dict[str, Any] | None:
    probable = game.get("probable_pitcher")
    if not _value_matches_player(probable, row):
        return None
    out = dict(game)
    out["probable_start"] = True
    out["pitcher_match_source"] = "mlb_schedule_probable_pitcher"
    return out


def _value_matches_player(value: Any, row: dict[str, Any]) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_value_matches_player(candidate, row) for candidate in value.values())
    ids = {_text(row.get("mlb_id")), _text(row.get("mlbId"))}
    names = {_normalize_name(row.get("name"))}
    text = str(value).strip()
    return bool(text and (text in ids or _normalize_name(text) in names))


def _player_kind(row: dict[str, Any]) -> str:
    slot = _slot(row)
    tokens = _position_tokens(row)
    if slot in PITCHER_POSITIONS:
        return "pitcher"
    if tokens & HITTER_POSITIONS:
        return "hitter"
    if tokens & PITCHER_POSITIONS:
        return "pitcher"
    return "hitter"


def _position_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("slot", "positions", "all_positions", "pos"):
        value = row.get(key)
        values = value if isinstance(value, list) else str(value or "").replace("/", ",").split(",")
        for raw in values:
            token = str(raw or "").strip().upper()
            token = POSITION_ALIASES.get(token, token)
            if token:
                tokens.add(token)
    return tokens


def _slot(row: dict[str, Any]) -> str:
    return str(row.get("slot") or "").strip().upper()


def _team_abbr(row: dict[str, Any]) -> str | None:
    value = row.get("team")
    if value in (None, ""):
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        scorer = raw.get("scorer") if isinstance(raw.get("scorer"), dict) else {}
        value = scorer.get("teamShortName") or scorer.get("teamName")
    text = str(value or "").strip().upper()
    return text or None


def _future_game_count(row: dict[str, Any]) -> int:
    value = row.get("future_games")
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 0


def _schedule_window(matchup: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    end = _parse_date(matchup.get("end"))
    if end is None:
        return None
    start = _parse_date(matchup.get("start")) or now.date()
    start = max(start, now.date())
    if start > end:
        start = end
    return {"start": start, "end": end, "now": now}


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _aware_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _window_json(window: dict[str, Any]) -> dict[str, str]:
    return {"start": window["start"].isoformat(), "end": window["end"].isoformat()}


def _empty_diagnostics(window: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "source": SCHEDULE_SOURCE,
        "window": _window_json(window) if window else None,
        "state": "ok" if window else "missing",
        "reason": None if window else "matchup period end is unavailable",
        "rows_seen": 0,
        "mapped_rows": 0,
        "failed_rows": 0,
        "missing_team_rows": 0,
        "rows_preserved_existing_future_games": 0,
        "hitter_rows": 0,
        "pitcher_rows": 0,
        "countable_future_games": 0,
        "schedule_fetch_count": 0,
        "schedule_fetch_failures": 0,
        "unmapped_team_abbrs": {},
        "status_counts": {},
    }


def _normalize_name(value: Any) -> str:
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""
