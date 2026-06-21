"""Pull roster, standings, transactions, pending trades, and free-agent pool
from Fantrax. Uses the fantraxapi library (v1.0.x) for what it wraps and raw
fxpa/req calls for the FA pool, which the library doesn't expose.

Each section is wrapped independently — a single failure shouldn't take out
the whole snapshot. Raw object data is preserved in a `raw` field on roster
rows so we can debug shape mismatches against MLB without changing parser code.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date as date_cls, datetime, time as time_cls, timezone
from typing import Any

import requests
from fantraxapi import FantraxAPI

try:
    from fantraxapi import api as _fantrax_api
except ImportError:
    class _FantraxApiCompat:
        @staticmethod
        def get_pending_transactions(api: FantraxAPI) -> dict[str, Any]:
            if hasattr(api, "_request"):
                # Current PyPI fantraxapi exposes only the object parser publicly.
                return api._request("getPendingTransactions")  # noqa: SLF001
            raise AttributeError("fantraxapi raw pending-transactions endpoint is unavailable")

    _fantrax_api = _FantraxApiCompat()

log = logging.getLogger(__name__)

FXPA_URL = "https://www.fantrax.com/fxpa/req"


# ---------------------------------------------------------------------------
# fantraxapi monkey-patch: League.reset_info crashes on MLB date formats
# ---------------------------------------------------------------------------
# The upstream library was tested on NHL where periodList entries look like
# "1 (Mon, Mar 25)" — single dates. MLB returns ranges like
# "1 (Mar 25 - Mar 29)", and the upstream s[5:-1] slice yields garbage that
# fails the dates lookup with a KeyError.
#
# Fix: parse the date range with a regex, map every day in the dates list to
# the period whose range contains it. Patch is applied at module import time.

def _patched_reset_info(self) -> None:  # type: ignore[no-redef]
    from fantraxapi import api as _api_mod
    from fantraxapi.objs.position import Position
    from fantraxapi.objs.scoring_period import ScoringPeriod
    from fantraxapi.objs.status import Status

    responses = _api_mod.get_init_info(self)
    self.name = responses[0]["fantasySettings"]["leagueName"]
    self.year = responses[0]["fantasySettings"]["subtitle"]
    self.start_date = datetime.fromtimestamp(responses[0]["fantasySettings"]["season"]["startDate"] / 1e3)
    self.end_date = datetime.fromtimestamp(responses[0]["fantasySettings"]["season"]["endDate"] / 1e3)
    self.positions = {k: Position(self, v) for k, v in responses[0]["positionMap"].items()}
    self.status = {k: Status(self, v) for k, v in responses[1]["allObjs"].items() if "name" in v}

    # Parse "<period> (<MMM DD> - <MMM DD>)" → list[(period, start_date)]
    season_year = self.start_date.year
    period_starts: list[tuple[int, date_cls]] = []
    for s in responses[4].get("displayedLists", {}).get("periodList", []):
        m = re.match(r"^\s*(\d+)\s*\(\s*([A-Za-z]{3}\s+\d+)\s*[-–]\s*([A-Za-z]{3}\s+\d+)\s*\)\s*$", s)
        if not m:
            continue
        try:
            period = int(m.group(1))
            start_str = m.group(2).strip()
            start_dt = datetime.strptime(f"{start_str} {season_year}", "%b %d %Y").date()
            period_starts.append((period, start_dt))
        except Exception:
            continue
    period_starts.sort(key=lambda x: x[0])

    # Build scoring_dates: one canonical date per period (the period's start).
    self.scoring_dates = {}
    for i, (period, sd) in enumerate(period_starts):
        self.scoring_dates[period] = sd

    # Filter out "Full Season" entries when present.
    sp_list = responses[3].get("displayedLists", {}).get("scoringPeriodList", [])
    self.scoring_periods = {
        p["value"]: ScoringPeriod(self, p)
        for p in sp_list
        if p.get("name") != "Full Season"
    }
    self._scoring_periods_lookup = None
    self._update_teams(responses[3]["fantasyTeams"])


# Apply the patch — once, at import.
try:
    from fantraxapi.objs.league import League as _League
    _League.reset_info = _patched_reset_info  # type: ignore[method-assign]
    log.debug("Applied MLB-friendly reset_info patch to fantraxapi.objs.league.League")
except Exception as _e:
    log.warning("Could not apply reset_info patch: %s", _e)


# ---------------------------------------------------------------------------
# fantraxapi monkey-patch: Game.__init__ crashes on MLB future-game strings
# ---------------------------------------------------------------------------
# The upstream parser assumes every future-game content cell has two <br/> parts
# and a token like "at 7:05PM". MLB rows can omit that second token or include a
# different label, which prevents RosterRow construction before our row-level
# error handling can run. Keep the parsed metadata best-effort and never raise
# from game parsing.

def _patched_game_init(self, league: Any, player: Any, game_date: str, data: dict) -> None:  # type: ignore[no-redef]
    from fantraxapi.objs.base import FantraxBaseObject

    FantraxBaseObject.__init__(self, league, data)
    self.id = self._data.get("eventId")
    self.player = player
    self.date = _parse_fantrax_game_date(league, game_date)
    self.time = _parse_game_time(data)

    parts = _game_content_parts(data)
    team = getattr(player, "team_short_name", None) or ""
    first = parts[0] if parts else ""
    second = parts[1] if len(parts) > 1 else ""

    if self.time is not None or not second:
        opponent = _clean_team_token(first)
        home_team = team if first.strip().startswith("@") else opponent
    else:
        home_team = _clean_team_token(first)
        away_team = _clean_team_token(second)
        opponent = away_team if home_team == team else home_team

    self.opponent = opponent or _clean_team_token(first) or "TBD"
    self.home = bool(team and home_team == team)
    self.away = not self.home


def _parse_fantrax_game_date(league: Any, game_date: str) -> date_cls:
    league_start = league.start_date.date()
    league_end = league.end_date.date()
    for year in {league.start_date.year, league.end_date.year}:
        try:
            parsed = datetime.strptime(f"{game_date} {year}", "%a %m/%d %Y").date()
        except Exception:
            continue
        if league_start <= parsed <= league_end:
            return parsed
    return league_start


def _game_content_parts(data: dict) -> list[str]:
    content = str((data or {}).get("content") or "").removesuffix(" F")
    return [part.strip() for part in re.split(r"<br\s*/?>", content) if part and part.strip()]


def _parse_game_time(data: dict) -> time_cls | None:
    content = " ".join(_game_content_parts(data))
    match = re.search(r"\b(\d{1,2}:\d{2})\s*([AP]M)\b", content, flags=re.IGNORECASE)
    if not match:
        return None
    raw = f"{match.group(1)}{match.group(2).upper()}"
    try:
        return datetime.strptime(raw, "%I:%M%p").time()
    except ValueError:
        return None


def _clean_team_token(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\b\d+(\.\d+)?\b", "", text)
    text = text.replace("@", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


try:
    from fantraxapi.objs.game import Game as _Game
    _Game.__init__ = _patched_game_init  # type: ignore[method-assign]
    log.debug("Applied MLB-friendly Game.__init__ patch to fantraxapi.objs.game.Game")
except Exception as _e:
    log.warning("Could not apply Game.__init__ patch: %s", _e)


def _to_jsonable(obj: Any, depth: int = 0) -> Any:
    """Coerce arbitrary objects into JSON-safe data. Bounded recursion to avoid
    cycles between fantraxapi objects (Roster <-> RosterRow, etc.)."""
    if depth > 6:
        return repr(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v, depth + 1) for k, v in obj.items()}
    if hasattr(obj, "__dict__"):
        out = {}
        for k, v in vars(obj).items():
            if k.startswith("_"):
                continue
            # Avoid backref loops (RosterRow.roster, TradeItem.trade, etc.)
            if k in ("roster", "trade", "league", "standings"):
                continue
            try:
                out[k] = _to_jsonable(v, depth + 1)
            except Exception:
                out[k] = repr(v)
        return out
    return str(obj)


def _injury_status(player: Any) -> str | None:
    """Convert Player's boolean flags into a single short string."""
    if player is None:
        return None
    if getattr(player, "out", False):
        return "OUT"
    if getattr(player, "injured_reserve", False):
        return "IR"
    if getattr(player, "suspended", False):
        return "SUSP"
    if getattr(player, "day_to_day", False):
        return "DTD"
    return None


ACTIVE_SLOT_LABELS = {"ACTIVE", "STARTER", "STARTING", "LINEUP"}
ROSTER_SLOT_ALIASES = {
    "BE": "BN",
    "BENCH": "BN",
    "RES": "RES",
    "RESERVE": "RES",
    "IR": "IR",
    "IL": "IL",
    "INJ": "IR",
    "INJ RES": "IR",
    "INJURED RESERVE": "IR",
    "MIN": "MIN",
    "MINORS": "MIN",
    "MINOR": "MIN",
    "MINOR LEAGUE": "MIN",
}


def _normalize_slot_label(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text.upper())
    return ROSTER_SLOT_ALIASES.get(compact, compact)


def _status_lookup(roster: Any) -> dict[str, str]:
    data = getattr(roster, "_data", {}) if roster is not None else {}
    totals = ((data.get("miscData") or {}).get("statusTotals") or []) if isinstance(data, dict) else []
    lookup: dict[str, str] = {}
    for item in totals:
        if not isinstance(item, dict):
            continue
        label = item.get("shortName") or item.get("name") or item.get("label")
        normalized = _normalize_slot_label(label)
        if not normalized:
            continue
        for key in ("id", "statusId", "value"):
            raw = item.get(key)
            if raw not in (None, ""):
                lookup[str(raw)] = normalized
        raw_name = item.get("name")
        if raw_name:
            lookup[str(raw_name)] = normalized
    return lookup


def _raw_roster_rows(roster: Any) -> list[dict[str, Any]]:
    data = getattr(roster, "_data", {}) if roster is not None else {}
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for table in data.get("tables") or []:
        if not isinstance(table, dict):
            continue
        for row in table.get("rows") or []:
            if isinstance(row, dict) and ("scorer" in row or "posId" in row):
                out.append(row)
    return out


def _row_player_id(row: dict[str, Any]) -> str | None:
    scorer = row.get("scorer") if isinstance(row.get("scorer"), dict) else {}
    for value in (
        row.get("scorerId"),
        row.get("playerId"),
        row.get("id"),
        scorer.get("scorerId"),
        scorer.get("playerId"),
        scorer.get("id"),
    ):
        if value not in (None, ""):
            return str(value)
    return None


def _assigned_slot_from_raw(row: dict[str, Any], status_lookup: dict[str, str]) -> tuple[str | None, str | None]:
    for key in (
        "lineupSlot",
        "lineupSlotName",
        "rosterSlot",
        "rosterSlotName",
        "slot",
        "slotName",
        "status",
        "statusName",
        "statusShortName",
    ):
        normalized = _normalize_slot_label(row.get(key))
        if normalized and normalized not in ACTIVE_SLOT_LABELS:
            source = f"raw.{key}"
            return normalized, source

    status_id = row.get("statusId")
    if status_id not in (None, ""):
        label = status_lookup.get(str(status_id))
        if label and label not in ACTIVE_SLOT_LABELS:
            return label, "raw.statusId"
    return None, None


def _assigned_slot_overrides(roster: Any) -> dict[str, tuple[str, str]]:
    status_lookup = _status_lookup(roster)
    overrides: dict[str, tuple[str, str]] = {}
    for raw_row in _raw_roster_rows(roster):
        player_id = _row_player_id(raw_row)
        if not player_id:
            continue
        slot, source = _assigned_slot_from_raw(raw_row, status_lookup)
        if slot and source:
            overrides[player_id] = (slot, source)
    return overrides


def extract_roster(api: FantraxAPI, team_id: str) -> dict:
    """Returns dict with `rows` (list of normalized players) plus roster
    capacity totals (active/reserve/IR)."""
    roster = api.team_roster(team_id)
    assigned_slots = _assigned_slot_overrides(roster)

    rows = []
    for row in getattr(roster, "rows", []) or []:
        try:
            player = getattr(row, "player", None)
            position = getattr(row, "position", None)
            player_id = getattr(player, "id", None) if player else None
            position_short = getattr(position, "short_name", None) if position else None
            position_name = getattr(position, "name", None) if position else None
            assigned_slot, slot_source = assigned_slots.get(str(player_id), (None, None))
            slot = assigned_slot or position_short or position_name
            slot_full = assigned_slot or position_name

            entry = {
                "name": getattr(player, "name", None) if player else None,
                "id": player_id,
                "team": getattr(player, "team_short_name", None) or getattr(player, "team_name", None) if player else None,
                "positions": getattr(player, "pos_short_name", None) if player else None,
                "all_positions": [getattr(p, "short_name", None) for p in getattr(player, "all_positions", []) or []] if player else [],
                "slot": slot,
                "slot_full": slot_full,
                "slot_source": slot_source or "position_fallback",
                "fpts": getattr(row, "total_fantasy_points", None),
                "fppg": getattr(row, "fantasy_points_per_game", None),
                "injury": _injury_status(player),
                "age": None,  # Fantrax doesn't expose age; populated from cache by audit.py
                "raw": _to_jsonable(row),
            }
            rows.append(entry)
        except Exception as e:
            log.warning("Failed to parse roster row: %s", e)
            rows.append({"error": str(e), "raw": _to_jsonable(row)})

    return {
        "rows": rows,
        "active": getattr(roster, "active", None),
        "active_max": getattr(roster, "active_max", None),
        "reserve": getattr(roster, "reserve", None),
        "reserve_max": getattr(roster, "reserve_max", None),
        "injured": getattr(roster, "injured", None),
        "injured_max": getattr(roster, "injured_max", None),
        "period_number": getattr(roster, "period_number", None),
        "period_date": str(getattr(roster, "period_date", "")) or None,
    }


def extract_standings(api: FantraxAPI, my_team_id: str) -> dict | None:
    try:
        standings = api.standings()
    except Exception as e:
        log.error("standings failed: %s", e)
        return None

    records = []
    my_record = None
    for rank in sorted(getattr(standings, "ranks", {}).keys()):
        rec = standings.ranks[rank]
        team = getattr(rec, "team", None)
        team_id = getattr(team, "id", None)
        d = {
            "rank": rec.rank,
            "team_id": team_id,
            "team_name": getattr(team, "name", None),
            "win": rec.win,
            "loss": rec.loss,
            "tie": rec.tie,
            "win_pct": rec.win_percentage,
            "games_back": rec.games_back,
            "points_for": rec.points_for,
            "points_against": rec.points_against,
            "streak": rec.streak,
            "fantasy_points": rec.points,
            "waiver_order": rec.wavier_wire_order,
        }
        records.append(d)
        if team_id == my_team_id:
            my_record = d
    return {"records": records, "my_record": my_record}


def extract_matchup(api: FantraxAPI, my_team_id: str) -> dict | None:
    """Return the current weekly matchup from Fantrax's schedule view.

    This is the data Skipper needs for "how is my matchup going?" questions.
    Fantrax exposes it through the standings SCHEDULE view, wrapped by
    fantraxapi as scoring_period_results().
    """
    try:
        periods = api.scoring_period_results(season=True, playoffs=False)
    except Exception as e:
        log.error("matchup schedule failed: %s", e)
        return None

    if not periods:
        return None

    today = datetime.now(timezone.utc).date()
    current = None
    for period in periods.values():
        start = getattr(period, "start", None)
        end = getattr(period, "end", None)
        if start and end and start <= today <= end:
            current = period
            break
    if current is None:
        current = next((p for p in periods.values() if getattr(p, "current", False)), None)
    if current is None:
        roster_period = None
        try:
            roster_period = api.team_roster(my_team_id).period_number
        except Exception:
            pass
        if roster_period is not None:
            current = periods.get(roster_period)
    if current is None:
        return None

    for matchup in getattr(current, "matchups", []) or []:
        away = getattr(matchup, "away", None)
        home = getattr(matchup, "home", None)
        away_id = getattr(away, "id", None)
        home_id = getattr(home, "id", None)
        if my_team_id not in (away_id, home_id):
            continue

        is_away = my_team_id == away_id
        opponent = home if is_away else away
        my_score = matchup.away_score if is_away else matchup.home_score
        opponent_score = matchup.home_score if is_away else matchup.away_score
        margin = round(float(my_score or 0) - float(opponent_score or 0), 2)
        return {
            "source": "fantrax_schedule",
            "period_number": getattr(getattr(current, "period", None), "number", None),
            "period_name": getattr(current, "name", None),
            "start": str(getattr(current, "start", "")) or None,
            "end": str(getattr(current, "end", "")) or None,
            "days": getattr(current, "days", None),
            "complete": getattr(current, "complete", None),
            "current": getattr(current, "current", None),
            "matchup_key": getattr(matchup, "matchup_key", None),
            "my_team_id": my_team_id,
            "my_team_name": getattr(away if is_away else home, "name", None) or str(away if is_away else home),
            "my_side": "away" if is_away else "home",
            "my_score": my_score,
            "opponent_team_id": getattr(opponent, "id", None),
            "opponent_team_name": getattr(opponent, "name", None) or str(opponent),
            "opponent_score": opponent_score,
            "margin": margin,
        }
    return None


def extract_transactions(api: FantraxAPI, count: int = 50) -> list[dict]:
    try:
        txns = api.transactions(count=count)
    except Exception as e:
        log.error("transactions failed: %s", e)
        return []

    out = []
    for t in txns:
        try:
            team = getattr(t, "team", None)
            entry = {
                "id": getattr(t, "id", None),
                "date": getattr(t, "date", None).isoformat() if getattr(t, "date", None) else None,
                "team_id": getattr(team, "id", None),
                "team_name": getattr(team, "name", None),
                "players": [
                    {
                        "name": getattr(p, "name", None),
                        "id": getattr(p, "id", None),
                        "team": getattr(p, "team_short_name", None),
                        "type": getattr(p, "type", None),
                    }
                    for p in getattr(t, "players", []) or []
                ],
            }
            out.append(entry)
        except Exception as e:
            log.warning("Failed to parse transaction: %s", e)
            out.append({"error": str(e), "raw": _to_jsonable(t)})
    return out


def extract_pending_trades(api: FantraxAPI, my_team_id: str) -> list[dict]:
    try:
        trades = api.pending_trades()
    except Exception as e:
        log.info("pending_trades object parser failed; falling back to raw endpoint: %s", e)
        return _extract_pending_trades_raw(api, my_team_id)

    out = []
    for t in trades:
        try:
            proposed_by = getattr(t, "proposed_by", None)
            moves = []
            for m in getattr(t, "moves", []) or []:
                from_team = getattr(m, "from_team", None)
                to_team = getattr(m, "to_team", None)
                player = getattr(m, "player", None)
                moves.append({
                    "from_team_id": getattr(from_team, "id", None),
                    "from_team": getattr(from_team, "name", None),
                    "to_team_id": getattr(to_team, "id", None),
                    "to_team": getattr(to_team, "name", None),
                    "player": getattr(player, "name", None) if player else None,
                    "draft_pick": _to_jsonable(getattr(m, "draft_pick", None)) if hasattr(m, "draft_pick") else None,
                })

            involves_me = any(
                m.get("from_team_id") == my_team_id or m.get("to_team_id") == my_team_id for m in moves
            ) or getattr(proposed_by, "id", None) == my_team_id

            if involves_me:
                out.append({
                    "trade_id": getattr(t, "trade_id", None),
                    "proposed_by_id": getattr(proposed_by, "id", None),
                    "proposed_by": getattr(proposed_by, "name", None),
                    "proposed": str(getattr(t, "proposed", "")) or None,
                    "accepted": str(getattr(t, "accepted", "")) or None,
                    "executed": str(getattr(t, "executed", "")) or None,
                    "moves": moves,
                })
        except Exception as e:
            log.warning("Failed to parse trade: %s", e)
            out.append({"error": str(e), "raw": _to_jsonable(t)})
    return out


def _extract_pending_trades_raw(api: FantraxAPI, my_team_id: str) -> list[dict]:
    try:
        response = _fantrax_api.get_pending_transactions(api)
    except Exception as e:
        log.warning("pending_trades raw endpoint failed: %s", e)
        return []

    out = []
    for trade in response.get("tradeInfoList") or []:
        if not isinstance(trade, dict):
            continue
        try:
            normalized = _normalize_pending_trade_raw(api, trade, my_team_id)
            if normalized:
                out.append(normalized)
        except Exception as e:
            log.warning("Failed to parse raw pending trade: %s", e)
            out.append({"error": str(e), "raw": _to_jsonable(trade)})
    return out


def _normalize_pending_trade_raw(api: FantraxAPI, trade: dict[str, Any], my_team_id: str) -> dict | None:
    info = _trade_info_map(trade)
    proposed_by_id = trade.get("creatorTeamId")
    moves = [_normalize_trade_move_raw(api, move) for move in trade.get("moves") or [] if isinstance(move, dict)]
    involves_me = any(
        m.get("from_team_id") == my_team_id or m.get("to_team_id") == my_team_id for m in moves
    ) or proposed_by_id == my_team_id
    if not involves_me:
        return None
    return {
        "trade_id": trade.get("txSetId") or trade.get("tradeId"),
        "proposed_by_id": proposed_by_id,
        "proposed_by": _team_name(api, proposed_by_id),
        "proposed": info.get("Proposed"),
        "accepted": info.get("Accepted"),
        "executed": info.get("To be executed"),
        "moves": moves,
    }


def _normalize_trade_move_raw(api: FantraxAPI, move: dict[str, Any]) -> dict[str, Any]:
    from_team_id = _team_ref_id(move.get("from"))
    to_team_id = _team_ref_id(move.get("to"))
    scorer = move.get("scorer") if isinstance(move.get("scorer"), dict) else None
    draft_pick = move.get("draftPick") if isinstance(move.get("draftPick"), dict) else None
    return {
        "from_team_id": from_team_id,
        "from_team": _team_name(api, from_team_id),
        "to_team_id": to_team_id,
        "to_team": _team_name(api, to_team_id),
        "player": scorer.get("name") if scorer else None,
        "player_id": (scorer.get("scorerId") or scorer.get("id")) if scorer else None,
        "draft_pick": _to_jsonable(draft_pick) if draft_pick else None,
    }


def _trade_info_map(trade: dict[str, Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for item in trade.get("usefulInfo") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name:
            out[str(name)] = str(item.get("value") or "") or None
    return out


def _team_ref_id(ref: Any) -> str | None:
    if isinstance(ref, dict):
        return ref.get("teamId") or ref.get("id")
    return None


def _team_name(api: FantraxAPI, team_id: str | None) -> str | None:
    if not team_id:
        return None
    try:
        return getattr(api.team(team_id), "name", None)
    except Exception:
        try:
            team = getattr(api, "team_lookup", {}).get(team_id)
            return getattr(team, "name", None)
        except Exception:
            return None


def extract_all_team_rosters(api: FantraxAPI, my_team_id: str) -> dict:
    """Pull every team's roster. Returns {team_id: {name, is_me, rows, ...}}.

    Used by league_intel.py to compute positional FP/G heatmaps and identify
    trade-path mismatches across the league.
    """
    teams_out: dict[str, dict] = {}
    try:
        team_lookup = api.team_lookup
    except Exception as e:
        log.error("team_lookup failed: %s", e)
        return teams_out

    for tid, team in team_lookup.items():
        try:
            roster_data = extract_roster(api, tid)
            teams_out[tid] = {
                "team_id": tid,
                "team_name": getattr(team, "name", None),
                "team_short": getattr(team, "short", None),
                "is_me": tid == my_team_id,
                **roster_data,
            }
        except Exception as e:
            log.warning("Failed to pull roster for team %s (%s): %s",
                        tid, getattr(team, "name", "?"), e)
            teams_out[tid] = {
                "team_id": tid,
                "team_name": getattr(team, "name", None),
                "is_me": tid == my_team_id,
                "error": str(e),
            }
    return teams_out


def extract_league_rules(session: requests.Session, league_id: str) -> dict | None:
    """Try several fxpa/req method names to fetch league scoring + roster
    rules. Returns whatever the first working endpoint gives us, raw, plus
    any obvious scoring categories pulled out for convenience."""
    candidates = [
        "getLeagueRules",
        "getLeagueInfo",
        "getLeague",
        "getLeagueSettings",
        "getScoringSettings",
        "getRules",
    ]

    url = f"{FXPA_URL}?leagueId={league_id}"
    for method in candidates:
        try:
            payload = {"msgs": [{"method": method, "data": {"leagueId": league_id}}]}
            r = session.post(url, json=payload, timeout=30)
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except ValueError:
                continue
            responses = data.get("responses") or []
            if not responses:
                continue
            first = responses[0] or {}
            if first.get("error") or first.get("errorMsg") or first.get("pageError"):
                continue
            scoring_categories = _find_scoring_categories(first)
            log.info("League rules fetched via method=%s", method)
            return {
                "method": method,
                "scoring_categories": scoring_categories,
                "raw": first,
            }
        except Exception as e:
            log.debug("League rules method %s raised: %s", method, e)
            continue
    log.info("League rules unavailable: all candidate methods failed")
    return None


def _find_scoring_categories(node: Any, depth: int = 0) -> list[dict] | None:
    """Walk the response looking for a list of scoring-category dicts. They
    typically have keys like name/code/value/points."""
    if depth > 10:
        return None
    if isinstance(node, list):
        if node and isinstance(node[0], dict):
            keys = set(node[0].keys())
            if ({"name"} & keys) and ({"value", "points", "fpts"} & keys):
                return [
                    {k: v for k, v in d.items() if k in ("name", "code", "shortName", "value", "points", "fpts")}
                    for d in node
                ]
        for item in node:
            found = _find_scoring_categories(item, depth + 1)
            if found:
                return found
    elif isinstance(node, dict):
        for k, v in node.items():
            found = _find_scoring_categories(v, depth + 1)
            if found:
                return found
    return None


def extract_free_agents(session: requests.Session, league_id: str,
                         max_pages: int = 3, per_page: int = 100) -> dict | None:
    """Pull the free-agent pool via getPlayerStats. The endpoint requires the
    league id as a query string param (not just in the body) — without it,
    Fantrax returns INVALID_REQUEST.

    Pages through up to `max_pages` × `per_page` free agents and returns
    `{"method": ..., "players": [...]}` or None on failure.
    """
    url = f"{FXPA_URL}?leagueId={league_id}"
    method = "getPlayerStats"
    base_data = {
        "leagueId": league_id,
        "view": "STATS",
        "positionOrGroup": "ALL",
        "statusOrTeamFilter": "ALL_AVAILABLE",
        "maxResultsPerPage": str(per_page),
        "sortType": "SCORE",
        "timeframeTypeCode": "YEAR_TO_DATE",
    }

    all_players: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            data = {**base_data, "pageNumber": str(page)}
            payload = {"msgs": [{"method": method, "data": data}]}
            r = session.post(url, json=payload, timeout=30)
            if r.status_code != 200:
                log.debug("FA page %d -> HTTP %s", page, r.status_code)
                break
            j = r.json()
            resp = (j.get("responses") or [{}])[0]
            if resp.get("pageError"):
                log.debug("FA page %d pageError: %s", page, resp["pageError"].get("text", "")[:80])
                break
            payload_data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
            stats_table = payload_data.get("statsTable") if isinstance(payload_data, dict) else None
            if not stats_table:
                log.debug("FA page %d: no statsTable", page)
                break
            stat_keys = _extract_stat_keys(payload_data)
            for entry in stats_table:
                all_players.append(_normalize_fa_player(entry, stat_keys))
            # Pagination: stop early if the page wasn't full
            if len(stats_table) < per_page:
                break
        except Exception as e:
            log.warning("FA page %d raised: %s", page, e)
            break

    if not all_players:
        log.warning("Free-agent pool unavailable: getPlayerStats returned no rows")
        return None

    log.info("FA pool fetched via getPlayerStats (%d players)", len(all_players))
    return {"method": method, "players": all_players}


def _extract_stat_keys(data: dict) -> list[str]:
    """Pull the column header short-names so we can label `cells` values."""
    if not isinstance(data, dict):
        return []
    # Try known locations
    for path in (("displayedLists", "statsHeader"), ("displayedLists", "categoryList"),
                 ("scoringCategoryTypes",)):
        cur: Any = data
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, list) and cur:
            keys = []
            for item in cur:
                if isinstance(item, dict):
                    keys.append(item.get("shortName") or item.get("name") or item.get("key") or "")
            if any(keys):
                return keys
    return []


def _normalize_fa_player(entry: dict, stat_keys: list[str]) -> dict:
    """One row from statsTable -> {name, id, team, positions, stats: {...}}.

    `cells` is a parallel list to the column header. We zip with `stat_keys`
    when available, else just keep the raw cells for downstream inspection.
    """
    scorer = entry.get("scorer") or {}
    cells = entry.get("cells") or []
    cell_values = []
    for c in cells:
        if isinstance(c, dict):
            cell_values.append(c.get("content") or c.get("value"))
        else:
            cell_values.append(c)
    stats: dict[str, Any] = {}
    if stat_keys and len(stat_keys) == len(cell_values):
        for k, v in zip(stat_keys, cell_values):
            if k:
                stats[k] = v
    else:
        stats = {"_cells": cell_values}

    return {
        "id": scorer.get("scorerId") or scorer.get("playerId") or scorer.get("id"),
        "name": scorer.get("name") or scorer.get("fullName"),
        "team": scorer.get("teamShortName") or scorer.get("teamName"),
        "positions": scorer.get("posShortNames") or scorer.get("positions"),
        "multi_positions": entry.get("multiPositions"),
        "stats": stats,
    }


def collect_all(session: requests.Session, league_id: str, team_id: str) -> dict:
    """Build the daily snapshot. Each section is independently wrapped."""
    api = FantraxAPI(league_id, session=session)

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "league_id": league_id,
        "team_id": team_id,
        "team_name": None,
        "league_rules": None,
        "roster": None,
        "all_team_rosters": None,
        "standings": None,
        "matchup": None,
        "transactions": None,
        "pending_trades": None,
        "free_agents": None,
        "errors": [],
    }

    try:
        team = api.team(team_id)
        snapshot["team_name"] = getattr(team, "name", None)
    except Exception as e:
        snapshot["errors"].append(f"team_name: {e}")
        log.warning("team() failed: %s", e)

    sections = [
        ("league_rules",     lambda: extract_league_rules(session, league_id)),
        ("roster",           lambda: extract_roster(api, team_id)),
        ("all_team_rosters", lambda: extract_all_team_rosters(api, team_id)),
        ("standings",        lambda: extract_standings(api, team_id)),
        ("matchup",          lambda: extract_matchup(api, team_id)),
        ("transactions",     lambda: extract_transactions(api)),
        ("pending_trades",   lambda: extract_pending_trades(api, team_id)),
        ("free_agents",      lambda: extract_free_agents(session, league_id)),
    ]

    for key, fn in sections:
        try:
            snapshot[key] = fn()
        except Exception as e:
            log.exception("section %s failed", key)
            snapshot["errors"].append(f"{key}: {e}")

    return snapshot
