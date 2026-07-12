"""Pull roster, standings, transactions, pending trades, and free-agent pool
from Fantrax. Uses the fantraxapi library (v1.0.x) for what it wraps and raw
fxpa/req calls for the FA pool, which the library doesn't expose.

Each section is wrapped independently — a single failure shouldn't take out
the whole snapshot. Raw object data is preserved in a `raw` field on roster
rows so we can debug shape mismatches against MLB without changing parser code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
import types
from datetime import date as date_cls, datetime, time as time_cls, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from fantraxapi import FantraxAPI
try:
    from fantraxapi import api as _fantrax_api
except Exception:  # fantraxapi 0.2.x exposes raw calls through FantraxAPI._request.
    _fantrax_api = None

log = logging.getLogger(__name__)

FXPA_URL = "https://www.fantrax.com/fxpa/req"
FANTRAX_DROP_ACTION_TYPE_ID = "3"
FANTRAX_TRADE_ACTION_TYPE_ID = "4"


def _raw_request(api: Any, method: str, **data: Any) -> Any:
    errors: list[str] = []
    if _fantrax_api is not None:
        fn = getattr(_fantrax_api, _raw_request_name(method), None)
        if callable(fn):
            try:
                return fn(api, **data)
            except Exception as exc:
                errors.append(f"helper {type(exc).__name__}: {exc}")
    request = getattr(api, "_request", None)
    if callable(request):
        try:
            return request(method, **data)
        except Exception as exc:
            errors.append(f"_request {type(exc).__name__}: {exc}")
    try:
        return _direct_fxpa_request(api, method, **data)
    except Exception as exc:
        errors.append(f"direct {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    raise RuntimeError("fantraxapi raw request interface is unavailable")


def _raw_request_name(method: str) -> str:
    out = []
    for i, char in enumerate(method):
        if i and char.isupper():
            out.append("_")
        out.append(char.lower())
    return "".join(out)


def _direct_fxpa_request(api: Any, method: str, **data: Any) -> Any:
    session = getattr(api, "_session", None) or getattr(api, "session", None)
    league_id = getattr(api, "league_id", None) or data.get("leagueId")
    if session is None or not league_id:
        raise RuntimeError("missing authenticated session or league id")

    request_data = {"leagueId": league_id, **data}
    response = session.post(
        FXPA_URL,
        params={"leagueId": league_id},
        json={"msgs": [{"method": method, "data": request_data}]},
        timeout=30,
    )
    if getattr(response, "status_code", 200) >= 400:
        reason = getattr(response, "reason", "")
        raise RuntimeError(f"HTTP {response.status_code} {reason}".strip())
    try:
        response_json = response.json()
    except ValueError as exc:
        raise RuntimeError(f"invalid JSON from {method}") from exc
    page_error = response_json.get("pageError") if isinstance(response_json, dict) else None
    if page_error:
        raise RuntimeError(str(page_error))
    responses = response_json.get("responses") if isinstance(response_json, dict) else None
    if not responses:
        raise RuntimeError("missing responses")
    first = responses[0] or {}
    if first.get("error") or first.get("errorMsg") or first.get("pageError"):
        raise RuntimeError(str(first.get("error") or first.get("errorMsg") or first.get("pageError")))
    return first.get("data") if isinstance(first, dict) and "data" in first else first


class _FantraxApiCompat:
    @staticmethod
    def get_pending_transactions(api: Any, **data: Any) -> Any:
        request = getattr(api, "_request", None)
        if callable(request):
            return request("getPendingTransactions", **data)
        raise RuntimeError("fantraxapi raw request interface is unavailable")


if _fantrax_api is None:
    _fantrax_api = _FantraxApiCompat()


class _RawRoster:
    """Minimal roster wrapper for raw getTeamRosterInfo payloads."""

    def __init__(self, api: Any, team_id: str, data: dict[str, Any]) -> None:
        self._api = api
        self.team_id = team_id
        self._data = data or {}
        self.rows = []
        self.active = _status_total_from_data(self._data, {"ACTIVE"}, "total")
        self.active_max = _status_total_from_data(self._data, {"ACTIVE"}, "max")
        self.reserve = _status_total_from_data(self._data, {"RES", "BN"}, "total")
        self.reserve_max = _status_total_from_data(self._data, {"RES", "BN"}, "max")
        self.injured = _status_total_from_data(self._data, {"IR", "IL", "INJ", "INJURED"}, "total")
        self.injured_max = _status_total_from_data(self._data, {"IR", "IL", "INJ", "INJURED"}, "max")
        displayed = self._data.get("displayedSelections")
        displayed = displayed if isinstance(displayed, dict) else {}
        displayed_period = _raw_period_value(displayed, "displayedPeriod")
        displayed_scoring_period = _raw_period_value(displayed, "displayedScoringPeriod")
        self.period_conflict = bool(
            displayed_period not in (None, "")
            and displayed_scoring_period not in (None, "")
            and str(displayed_period) != str(displayed_scoring_period)
        )
        self.period_number = (
            None
            if self.period_conflict
            else displayed_period if displayed_period not in (None, "") else displayed_scoring_period
        )
        # Production proved displayedStartDate/displayedEndDate are view bounds,
        # not the scoring-period window shown in Fantrax's period selector.
        # Keep the canonical period number and source; derive dates from the
        # schedule response instead of publishing misleading roster dates.
        self.period_start = None
        self.period_end = None
        self.period_date = None
        has_displayed_period = self.period_number not in (None, "")
        self.period_source = (
            "fantrax.getTeamRosterInfo.displayedSelections"
            if has_displayed_period
            else None
        )


def _team_roster(api: Any, team_id: str) -> Any:
    raw = _raw_team_roster(api, team_id)
    if isinstance(raw, dict):
        return _RawRoster(api, team_id, raw)

    if hasattr(api, "team_roster"):
        return api.team_roster(team_id)
    if hasattr(api, "roster_info"):
        return api.roster_info(team_id)
    raise AttributeError("FantraxAPI has no team_roster/roster_info method")


def _raw_team_roster(api: Any, team_id: str) -> dict[str, Any] | None:
    try:
        raw = _raw_request(api, "getTeamRosterInfo", teamId=team_id)
    except Exception as e:
        log.debug("Raw getTeamRosterInfo failed for team %s: %s", team_id, e)
        return None
    return raw if isinstance(raw, dict) else None


LINEUP_PERIOD_EVIDENCE_VERSION = "fantrax_period_lineup_v1"


def extract_completed_lineup_evidence(
    api: Any, team_id: str, completed_matchup: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Capture one exact, final Fantrax period roster for durable outcome scoring."""
    if not isinstance(completed_matchup, dict) or not completed_matchup.get("complete"):
        return None
    if str(completed_matchup.get("my_team_id") or "") != str(team_id):
        raise ValueError("completed matchup team does not match lineup evidence target")
    if completed_matchup.get("source") != "fantrax_schedule" or completed_matchup.get("score_state") != "live_or_final":
        raise ValueError("completed matchup is not authoritative final evidence")

    period_number = str(completed_matchup.get("period_number") or completed_matchup.get("period_id") or "").strip()
    if not period_number:
        raise ValueError("completed matchup period number is required")
    current = _raw_team_roster(api, team_id)
    if not current:
        raise RuntimeError("current Fantrax roster metadata is unavailable")
    by_period_code = _by_period_season_code(current)
    raw = _direct_fxpa_request(
        api,
        "getTeamRosterInfo",
        teamId=team_id,
        period=period_number,
        seasonOrProjection=by_period_code,
        timeframeTypeCode="BY_PERIOD",
    )
    if not isinstance(raw, dict):
        raise RuntimeError("historical Fantrax roster response is invalid")
    displayed = raw.get("displayedSelections") if isinstance(raw.get("displayedSelections"), dict) else {}
    displayed_period = _raw_period_value(displayed, "displayedPeriod")
    displayed_scoring_period = _raw_period_value(displayed, "displayedScoringPeriod")
    if displayed_period in (None, "") or displayed_scoring_period in (None, ""):
        raise ValueError("historical Fantrax roster response identity is incomplete")
    if str(displayed_period) != str(displayed_scoring_period):
        raise ValueError("historical Fantrax roster returned conflicting periods")
    if str(displayed_period or "") != period_number:
        raise ValueError("historical Fantrax roster returned a different period")
    displayed_code = _selection_code(displayed.get("displayedSeasonOrProjection"))
    if displayed_code != by_period_code:
        raise ValueError("historical Fantrax roster returned a different stats selection")
    timeframe = str(displayed.get("timeframeTypeCode") or raw.get("timeframeTypeCode") or "")
    if timeframe != "BY_PERIOD":
        raise ValueError("historical Fantrax roster is not period-scoped")

    roster = _RawRoster(api, team_id, raw)
    status_lookup = _status_lookup(roster)
    players: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for table in raw.get("tables") or []:
        if not isinstance(table, dict):
            continue
        role, scoring_column, scoring_column_index = _table_scoring_contract(table)
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            player_id = _row_player_id(row)
            scorer = row.get("scorer") if isinstance(row.get("scorer"), dict) else {}
            name = str(scorer.get("name") or scorer.get("fullName") or row.get("name") or "").strip()
            if not player_id and not name:
                continue
            if not player_id or not name or not role or not scoring_column:
                raise ValueError("historical Fantrax player row lacks stable identity or scoring role")
            identity = (player_id, role)
            if identity in seen:
                raise ValueError("historical Fantrax roster contains a duplicate player scoring role")
            seen.add(identity)
            slot, slot_source = _assigned_slot_from_raw(row, status_lookup, api)
            if not slot or slot_source not in {"raw.posId", "raw.statusId"}:
                raise ValueError(f"historical Fantrax slot is untrusted for {name}")
            points = _period_points(row, scoring_column_index)
            if points is None:
                raise ValueError(f"historical Fantrax period points are invalid for {name}")
            players.append({
                "player_id": player_id,
                "player_name": name,
                "scoring_role": role,
                "slot": slot,
                "slot_source": slot_source,
                "raw_pos_id": str(row.get("posId") or "") or None,
                "raw_status_id": str(row.get("statusId") or "") or None,
                "eligibility_pos_ids": sorted({
                    str(value) for value in (
                        scorer.get("posIds") or scorer.get("allPositionIds") or scorer.get("posIdsNoFlex") or []
                    ) if value not in (None, "")
                }),
                "period_fpts": points,
                "period_fpts_source": scoring_column,
            })
    if not players:
        raise ValueError("historical Fantrax roster contains no player evidence")
    players.sort(key=lambda item: (item["scoring_role"], item["slot"], item["player_id"]))
    inactive_slots = {"RES", "IR", "MIN", "IL", "INJ", "BENCH", "BN", "BE"}
    active = [item for item in players if item["slot"] not in inactive_slots]
    active_ids = [item["player_id"] for item in active]
    if len(active_ids) != len(set(active_ids)):
        raise ValueError("historical Fantrax roster has ambiguous active two-way scoring")
    observed_total = _decimal_text(completed_matchup.get("my_score"))
    active_total = _decimal_text(sum(Decimal(item["period_fpts"]) for item in active))
    if Decimal(active_total) != Decimal(observed_total):
        raise ValueError("historical Fantrax active-player total does not match completed team score")
    league_id = str(getattr(api, "league_id", "") or "").strip()
    if not league_id:
        raise ValueError("Fantrax league identity is unavailable")
    response_team = str(displayed.get("teamId") or displayed.get("displayedTeamId") or "").strip()
    if not response_team:
        raise ValueError("historical Fantrax roster response team identity is missing")
    if response_team != str(team_id):
        raise ValueError("historical Fantrax roster returned a different team")
    evidence = {
        "evidence_version": LINEUP_PERIOD_EVIDENCE_VERSION,
        "league_id": league_id,
        "team_id": str(team_id),
        "period": {
            "number": period_number,
            "start": str(completed_matchup.get("start") or ""),
            "end": str(completed_matchup.get("end") or ""),
        },
        "source": {
            "method": "getTeamRosterInfo",
            "request_identity": {
                "league_id": league_id,
                "team_id": str(team_id),
                "period": period_number,
                "season_or_projection": by_period_code,
                "timeframe_type_code": "BY_PERIOD",
            },
            "response_identity": {
                "team_id": response_team,
                "period": period_number,
                "season_or_projection": displayed_code,
                "timeframe_type_code": timeframe,
            },
            "matchup_key": str(completed_matchup.get("matchup_key") or ""),
        },
        "observed_team_total": observed_total,
        "active_player_total": active_total,
        "active_player_count": len(active),
        "players": players,
    }
    return {**evidence, "evidence_hash": lineup_period_evidence_hash(evidence)}


def lineup_period_evidence_hash(evidence: dict[str, Any]) -> str:
    canonical_evidence = {key: value for key, value in evidence.items() if key != "evidence_hash"}
    canonical = json.dumps(canonical_evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _selection_code(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("code") or value.get("value") or value.get("id")
    text = str(value or "").strip()
    return text or None


def _by_period_season_code(raw: dict[str, Any]) -> str:
    displayed_lists = raw.get("displayedLists") if isinstance(raw.get("displayedLists"), dict) else {}
    values = displayed_lists.get("seasonOrProjections") or displayed_lists.get("seasonOrProjectionList") or []
    for item in values:
        if not isinstance(item, dict):
            continue
        if str(item.get("timeframeTypeCode") or "") == "BY_PERIOD":
            code = _selection_code(item)
            if code:
                return code
    raise ValueError("Fantrax did not advertise a BY_PERIOD stats selection")


def _table_scoring_contract(table: dict[str, Any]) -> tuple[str | None, str | None, int | None]:
    headers = table.get("headers") or table.get("header") or table.get("columns") or []
    if not isinstance(headers, list):
        return None, None, None
    matches: list[tuple[str, str, int]] = []
    for index, header in enumerate(headers):
        fingerprint = json.dumps(header, sort_keys=True)
        for role, category in (("hitter", "SCORING_CATEGORY_10"), ("pitcher", "SCORING_CATEGORY_20")):
            if category in fingerprint:
                matches.append((role, category, index))
    if len(matches) != 1:
        return None, None, None
    role, category, index = matches[0]
    return role, f"{category}:cells[{index}].content", index


def _period_points(row: dict[str, Any], column_index: int | None) -> str | None:
    cells = row.get("cells")
    if column_index is None or not isinstance(cells, list) or len(cells) <= column_index or not isinstance(cells[column_index], dict):
        return None
    try:
        return _decimal_text(cells[column_index].get("content"))
    except ValueError:
        return None


def _decimal_text(value: Any) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("period points must be an exact decimal") from exc
    if not number.is_finite():
        raise ValueError("period points must be finite")
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _getattr_any(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if value is not None:
            return value
    return default


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
    log.debug("Skipped legacy reset_info patch: %s", _e)


# ---------------------------------------------------------------------------
# fantraxapi monkey-patch: Game.__init__ crashes on MLB future-game strings
# ---------------------------------------------------------------------------
# The upstream parser assumes every future-game content cell has two <br/> parts
# and a token like "at 7:05PM". MLB rows can omit that second token or include a
# different label, which prevents RosterRow construction before our row-level
# error handling can run. Keep the parsed metadata best-effort and never raise
# from game parsing.

def _patched_game_init(self, league: Any, player: Any, game_date: str, data: dict) -> None:  # type: ignore[no-redef]
    self._data = data or {}
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


def _cell_content(data: dict, index: int) -> Any:
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list) or index >= len(cells):
        return None
    cell = cells[index]
    if isinstance(cell, dict):
        return cell.get("content") or cell.get("value")
    return cell


def _floatish(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


try:
    from fantraxapi.objs.game import Game as _Game
    _Game.__init__ = _patched_game_init  # type: ignore[method-assign]
    log.debug("Applied MLB-friendly Game.__init__ patch to fantraxapi.objs.game.Game")
except Exception as _e:
    class _CompatGame:
        def __init__(self, league: Any, player: Any, game_date: str, data: dict) -> None:
            _patched_game_init(self, league, player, game_date, data)

    _game_module = types.ModuleType("fantraxapi.objs.game")
    _game_module.Game = _CompatGame
    sys.modules.setdefault("fantraxapi.objs.game", _game_module)
    log.debug("Installed compatibility fantraxapi.objs.game.Game shim: %s", _e)


def _patched_roster_row_init(self, api: Any, data: dict) -> None:  # type: ignore[no-redef]
    from fantraxapi.objs import Player, Position

    self._api = api
    self._data = data or {}
    status_id = str(self._data.get("statusId") or "")

    if status_id == "1":
        self.pos_id = self._data.get("posId")
        self.pos = getattr(api, "positions", {}).get(self.pos_id)
        if self.pos is None:
            self.pos = Position(api, {
                "id": self.pos_id or "0",
                "name": self.pos_id or "Active",
                "shortName": self.pos_id or "ACT",
            })
    elif status_id == "3":
        self.pos_id = "-1"
        self.pos = Position(api, {"id": "-1", "name": "Injured", "shortName": "IR"})
    else:
        self.pos_id = "0"
        self.pos = Position(api, {"id": "0", "name": "Reserve", "shortName": "Res"})
    self.position = self.pos

    self.player = None
    self.fppg = None
    self.fantasy_points_per_game = None
    scorer = self._data.get("scorer") if isinstance(self._data.get("scorer"), dict) else None
    if scorer:
        self.player = Player(api, scorer)
        self.total_fantasy_points = _floatish(_cell_content(self._data, 1))
        self.fantasy_points = self.total_fantasy_points
        self.fpts = self.total_fantasy_points
        self.fppg = _floatish(_cell_content(self._data, 2))
        if self.fppg is None:
            self.fppg = _floatish(_cell_content(self._data, 3))
        self.fantasy_points_per_game = self.fppg

    content = str(_cell_content(self._data, 1) or "")
    parts = _game_content_parts({"content": content}) if re.search(r"[A-Za-z@]|<br", content) else []
    self.opponent = _clean_team_token(parts[0]) if parts else None
    self.time = _parse_game_time({"content": content})


try:
    from fantraxapi.objs import RosterRow as _RosterRow

    _RosterRow.__init__ = _patched_roster_row_init  # type: ignore[method-assign]
    log.debug("Applied MLB-friendly RosterRow.__init__ patch to fantraxapi.objs.RosterRow")
except Exception as _e:
    log.debug("Skipped RosterRow.__init__ patch: %s", _e)


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
    if getattr(player, "injured", False):
        return "INJ"
    return None


ACTIVE_SLOT_LABELS = {"ACTIVE", "STARTER", "STARTING", "LINEUP"}
RAW_ASSIGNED_SLOT_KEYS = (
    "lineupSlot",
    "lineupSlotName",
    "rosterSlot",
    "rosterSlotName",
    "slot",
    "slotName",
    "status",
    "statusName",
    "statusShortName",
)
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
UNTRUSTED_SLOT_SOURCES = {"", "position_fallback", "unknown", "fallback"}


def _normalize_slot_label(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text.upper())
    return ROSTER_SLOT_ALIASES.get(compact, compact)


def _status_totals_from_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    totals = ((data.get("miscData") or {}).get("statusTotals") or []) if isinstance(data, dict) else []
    return [item for item in totals if isinstance(item, dict)]


def _status_total_from_data(data: dict[str, Any], labels: set[str], key: str) -> Any:
    for item in _status_totals_from_data(data):
        label = item.get("shortName") or item.get("name") or item.get("label")
        if _normalize_slot_label(label) in labels:
            return item.get(key)
    return None


def _raw_period_value(data: dict[str, Any], *keys: str) -> Any:
    if not isinstance(data, dict):
        return None
    containers = [
        data.get("displayedSelections") if isinstance(data.get("displayedSelections"), dict) else {},
        data,
        data.get("miscData") if isinstance(data.get("miscData"), dict) else {},
    ]
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                return _raw_scalar_period_value(value)
    return None


def _raw_scalar_period_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("number", "periodNumber", "value", "id", "date", "periodDate"):
            nested = value.get(key)
            if nested not in (None, ""):
                return nested
        return None
    return value


def _status_lookup(roster: Any) -> dict[str, str]:
    data = getattr(roster, "_data", {}) if roster is not None else {}
    lookup: dict[str, str] = {}
    for item in _status_totals_from_data(data):
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


def _status_total_value(roster: Any, labels: set[str], key: str) -> Any:
    data = getattr(roster, "_data", {}) if roster is not None else {}
    return _status_total_from_data(data, labels, key)


def _status_value_or_attr(roster: Any, labels: set[str], key: str, attr: str) -> Any:
    value = _status_total_value(roster, labels, key)
    if value is not None:
        return value
    return getattr(roster, attr, None)


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


def _assigned_slot_from_raw(
    row: dict[str, Any],
    status_lookup: dict[str, str],
    api: Any | None = None,
) -> tuple[str | None, str | None]:
    for key in RAW_ASSIGNED_SLOT_KEYS:
        normalized = _normalize_slot_label(row.get(key))
        if normalized and normalized not in ACTIVE_SLOT_LABELS:
            source = f"raw.{key}"
            return normalized, source

    status_id = row.get("statusId")
    if status_id not in (None, ""):
        label = status_lookup.get(str(status_id))
        if label and label not in ACTIVE_SLOT_LABELS:
            return label, "raw.statusId"
        if label in ACTIVE_SLOT_LABELS:
            normalized = _normalize_slot_label(_position_label(api, row.get("posId")))
            if normalized and normalized not in ACTIVE_SLOT_LABELS:
                return normalized, "raw.posId"
    return None, None


def _assigned_slot_overrides(roster: Any, api: Any | None = None) -> dict[str, tuple[str, str]]:
    status_lookup = _status_lookup(roster)
    overrides: dict[str, tuple[str, str]] = {}
    for raw_row in _raw_roster_rows(roster):
        player_id = _row_player_id(raw_row)
        if not player_id:
            continue
        slot, source = _assigned_slot_from_raw(raw_row, status_lookup, api)
        if slot and source:
            overrides[player_id] = (slot, source)
    return overrides


def _position_label(api: Any, pos_id: Any) -> str | None:
    raw = str(pos_id or "").strip()
    if not raw:
        return None
    try:
        positions = getattr(api, "positions", {}) or {}
        position = positions.get(raw) if hasattr(positions, "get") else None
        label = _getattr_any(position, "short_name", "shortName", "name")
        if label:
            return str(label)
    except Exception:
        pass
    return raw


def _split_positions(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    else:
        values = re.split(r"[,/]", str(value or ""))
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _position_labels(api: Any, values: Any) -> list[str]:
    if isinstance(values, list):
        labels = [_position_label(api, value) for value in values]
        return [label for label in labels if label]
    return _split_positions(values)


def _raw_player_positions(api: Any, scorer: dict[str, Any]) -> tuple[str | None, list[str]]:
    positions = scorer.get("posShortNames") or scorer.get("positions")
    all_positions = _position_labels(api, scorer.get("posIds") or scorer.get("allPositionIds"))
    if not all_positions:
        all_positions = _split_positions(positions)
    if not positions and all_positions:
        positions = ",".join(all_positions)
    return str(positions) if positions else None, all_positions


def _raw_fppg(row: dict[str, Any], scorer: dict[str, Any]) -> float | None:
    for source in (row, scorer):
        for key in ("fantasyPointsPerGame", "fppg", "fpPerGame", "average"):
            value = _floatish(source.get(key))
            if value is not None:
                return value
    return _floatish(_cell_content(row, 2))


def _raw_fpts(row: dict[str, Any], scorer: dict[str, Any]) -> float | None:
    for source in (row, scorer):
        for key in ("totalFantasyPoints", "fantasyPoints", "fpts", "points"):
            value = _floatish(source.get(key))
            if value is not None:
                return value
    return _floatish(_cell_content(row, 1))


ROSTER_AGE_FIELDS = ("age", "Age", "playerAge", "player_age")
MIN_PLAUSIBLE_ROSTER_AGE = 16
MAX_PLAUSIBLE_ROSTER_AGE = 50


def _plausible_roster_age(value: Any) -> int | None:
    parsed = _floatish(value)
    if parsed is None or not parsed.is_integer():
        return None
    age = int(parsed)
    if not MIN_PLAUSIBLE_ROSTER_AGE <= age <= MAX_PLAUSIBLE_ROSTER_AGE:
        return None
    return age


def _raw_roster_age(row: dict[str, Any], scorer: dict[str, Any]) -> tuple[int | None, str | None]:
    """Return roster age only when its source is explicit or schema-verified."""
    for source, prefix in ((row, "raw"), (scorer, "raw.scorer")):
        for key in ROSTER_AGE_FIELDS:
            age = _plausible_roster_age(source.get(key))
            if age is not None:
                return age, f"{prefix}.{key}"

    age = _plausible_roster_age(_cell_content(row, 0))
    has_roster_stat_fingerprint = (
        _floatish(_cell_content(row, 1)) is not None
        and _floatish(_cell_content(row, 2)) is not None
    )
    if age is not None and has_roster_stat_fingerprint:
        return age, "raw.cells[0]"
    return None, None


def _object_roster_age(row: Any, player: Any) -> tuple[int | None, str | None]:
    for source, prefix in ((player, "player"), (row, "row")):
        if source is None:
            continue
        for attr in ("age", "player_age", "playerAge"):
            try:
                value = getattr(source, attr)
            except Exception:
                continue
            age = _plausible_roster_age(value)
            if age is not None:
                return age, f"{prefix}.{attr}"
    return None, None


def _raw_injury_status(scorer: dict[str, Any]) -> str | None:
    for key in ("injuryStatus", "status", "playerStatus", "statusShortName"):
        value = scorer.get(key)
        if value:
            normalized = str(value).strip().upper()
            if normalized in {"DTD", "OUT", "IR", "IL", "INJ", "SUSP"}:
                return normalized
    for icon in scorer.get("icons") or []:
        if not isinstance(icon, dict):
            continue
        type_id = str(icon.get("typeId") or icon.get("id") or "").strip()
        label = str(icon.get("label") or icon.get("name") or icon.get("title") or "").upper()
        if type_id == "1" or "DTD" in label or "DAY" in label:
            return "DTD"
        if type_id == "2" or "OUT" in label:
            return "OUT"
        if type_id == "6" or "IL" in label or "IR" in label or "INJ" in label:
            return "IR"
        if "SUSP" in label:
            return "SUSP"
    return None


def _normalize_raw_future_game(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("date", "gameDate", "eventDate", "eventId", "opponent", "home", "away", "probable_start"):
        if key in value and value.get(key) not in (None, ""):
            out[key] = value.get(key)
    if not out and value:
        out = _to_jsonable(value)
    return out or None


def _raw_future_games(row: dict[str, Any]) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    for key in ("future_games", "futureGames", "games", "scheduledGames", "upcomingGames"):
        value = row.get(key)
        if isinstance(value, list):
            for item in value:
                normalized = _normalize_raw_future_game(item)
                if normalized:
                    games.append(normalized)
    for cell in row.get("cells") or []:
        if not isinstance(cell, dict) or cell.get("eventId") in (None, ""):
            continue
        normalized = _normalize_raw_future_game(cell)
        if normalized:
            games.append(normalized)
    return games


def _normalize_roster_raw_row(
    api: Any,
    raw_row: dict[str, Any],
    assigned_slots: dict[str, tuple[str, str]],
    status_lookup: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    scorer = raw_row.get("scorer") if isinstance(raw_row.get("scorer"), dict) else {}
    player_id = _row_player_id(raw_row)
    if not player_id:
        return None

    positions, all_positions = _raw_player_positions(api, scorer)
    position_short = _position_label(api, raw_row.get("posId")) or (all_positions[0] if all_positions else positions)
    assigned_slot, slot_source = assigned_slots.get(str(player_id), (None, None))
    slot = assigned_slot or position_short
    slot_full = assigned_slot or position_short
    age, age_source = _raw_roster_age(raw_row, scorer)
    entry = {
        "name": scorer.get("name") or scorer.get("fullName") or scorer.get("shortName"),
        "id": player_id,
        "team": scorer.get("teamShortName") or scorer.get("teamName"),
        "positions": positions,
        "all_positions": all_positions,
        "slot": slot,
        "slot_full": slot_full,
        "slot_source": slot_source or "position_fallback",
        "fpts": _raw_fpts(raw_row, scorer),
        "fppg": _raw_fppg(raw_row, scorer),
        "injury": _raw_injury_status(scorer),
        "age": age,
        "age_source": age_source,
        "raw": _to_jsonable(raw_row),
    }
    lineup_eligibility = _normalized_lineup_eligibility(api, raw_row, status_lookup or {})
    if lineup_eligibility:
        entry["lineup_eligibility"] = lineup_eligibility
    transaction_eligibility = _normalized_transaction_eligibility(raw_row)
    if transaction_eligibility:
        entry["transaction_eligibility"] = transaction_eligibility
    future_games = _raw_future_games(raw_row)
    if future_games:
        entry["future_games"] = future_games
    return entry


def _normalized_lineup_eligibility(
    api: Any,
    raw_row: dict[str, Any],
    status_lookup: dict[str, str],
) -> dict[str, Any] | None:
    """Normalize Fantrax's current destination-level lineup legality proof."""
    eligible_status_ids = _string_list(raw_row.get("eligibleStatusIds"))
    eligible_position_ids = _string_list(raw_row.get("eligiblePosIds"))
    if not eligible_status_ids and not eligible_position_ids:
        return None

    current_status_id = _string_or_none(raw_row.get("statusId"))
    current_position_id = _string_or_none(raw_row.get("posId"))
    return {
        "source": "fantrax.raw.eligibleStatusIds+eligiblePosIds",
        "current_status_id": current_status_id,
        "current_status": status_lookup.get(current_status_id) if current_status_id else None,
        "current_position_id": current_position_id,
        "current_position": _position_label(api, current_position_id),
        "eligible_status_ids": eligible_status_ids,
        "eligible_statuses": [
            status_lookup[status_id]
            for status_id in eligible_status_ids
            if status_id in status_lookup
        ],
        "eligible_position_ids": eligible_position_ids,
        "eligible_positions": _position_labels(api, eligible_position_ids),
    }


def _normalized_transaction_eligibility(raw_row: dict[str, Any]) -> dict[str, Any] | None:
    actions = raw_row.get("actions")
    if not isinstance(actions, list):
        return None
    type_ids = [
        str(action.get("typeId"))
        for action in actions
        if isinstance(action, dict) and action.get("typeId") not in (None, "")
    ]
    return {
        "source": "fantrax.raw.actions.typeId",
        "action_type_ids": type_ids,
        "drop_available": FANTRAX_DROP_ACTION_TYPE_ID in type_ids,
        "trade_available": FANTRAX_TRADE_ACTION_TYPE_ID in type_ids,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _string_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _trusted_slot_override(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    if value.get("conflicts"):
        return None
    slot = _normalize_slot_label(value.get("slot"))
    source = str(value.get("slot_source") or value.get("source") or "").strip()
    if not slot or slot in ACTIVE_SLOT_LABELS:
        return None
    if source.casefold() in UNTRUSTED_SLOT_SOURCES:
        return None
    return slot, source


def apply_trusted_slot_overrides(
    roster_data: dict[str, Any],
    slot_overrides: dict[str, dict[str, Any]] | None,
    *,
    replace_trusted: bool = False,
) -> dict[str, Any]:
    """Return roster data with trusted external slot proof applied to rows.

    By default, trusted existing slot sources are preserved. This keeps DOM
    evidence from clobbering raw Fantrax reserved-slot proof; it only upgrades
    rows whose slot source is currently inferred or missing.
    """
    if not slot_overrides:
        return roster_data
    out = dict(roster_data or {})
    rows = []
    for row in out.get("rows") or []:
        if not isinstance(row, dict):
            rows.append(row)
            continue
        updated = dict(row)
        player_id = str(updated.get("id") or "")
        override = _trusted_slot_override(slot_overrides.get(player_id))
        existing_source = str(updated.get("slot_source") or "").strip().casefold()
        should_apply = override and (replace_trusted or existing_source in UNTRUSTED_SLOT_SOURCES)
        if should_apply:
            slot, source = override
            updated["slot"] = slot
            updated["slot_full"] = slot
            updated["slot_source"] = source
        rows.append(updated)
    out["rows"] = rows
    return out


def extract_roster(
    api: FantraxAPI,
    team_id: str,
    slot_overrides: dict[str, dict[str, Any]] | None = None,
    *,
    capture_lineup_policy: bool = False,
) -> dict:
    """Returns dict with `rows` (list of normalized players) plus roster
    capacity totals (active/reserve/IR)."""
    roster = _team_roster(api, team_id)
    assigned_slots = _assigned_slot_overrides(roster, api)
    status_lookup = _status_lookup(roster)

    rows = []
    raw_rows = _raw_roster_rows(roster)
    object_rows = getattr(roster, "rows", []) or []
    if raw_rows and (isinstance(roster, _RawRoster) or not object_rows):
        for raw_row in raw_rows:
            try:
                entry = _normalize_roster_raw_row(api, raw_row, assigned_slots, status_lookup)
                if entry:
                    rows.append(entry)
            except Exception as e:
                log.warning("Failed to parse raw roster row: %s", e)
                rows.append({"error": str(e), "raw": _to_jsonable(raw_row)})
    else:
        for row in object_rows:
            try:
                player = getattr(row, "player", None)
                position = _getattr_any(row, "position", "pos")
                player_id = getattr(player, "id", None) if player else None
                position_short = _getattr_any(position, "short_name", "shortName") if position else None
                position_name = _getattr_any(position, "name") if position else None
                assigned_slot, slot_source = assigned_slots.get(str(player_id), (None, None))
                slot = assigned_slot or position_short or position_name
                slot_full = assigned_slot or position_name
                age, age_source = _object_roster_age(row, player)

                entry = {
                    "name": getattr(player, "name", None) if player else None,
                    "id": player_id,
                    "team": getattr(player, "team_short_name", None) or getattr(player, "team_name", None) if player else None,
                    "positions": getattr(player, "pos_short_name", None) if player else None,
                    "all_positions": [_getattr_any(p, "short_name", "shortName") for p in getattr(player, "all_positions", []) or []] if player else [],
                    "slot": slot,
                    "slot_full": slot_full,
                    "slot_source": slot_source or "position_fallback",
                    "fpts": _getattr_any(row, "total_fantasy_points", "fantasy_points", "fpts"),
                    "fppg": _getattr_any(row, "fantasy_points_per_game", "fppg"),
                    "injury": _injury_status(player),
                    "age": age,
                    "age_source": age_source,
                    "raw": _to_jsonable(row),
                }
                rows.append(entry)
            except Exception as e:
                log.warning("Failed to parse roster row: %s", e)
                rows.append({"error": str(e), "raw": _to_jsonable(row)})

    roster_data = {
        "rows": rows,
        "active": _status_value_or_attr(roster, {"ACTIVE"}, "total", "active"),
        "active_max": _status_value_or_attr(roster, {"ACTIVE"}, "max", "active_max"),
        "reserve": _status_value_or_attr(roster, {"RES", "BN"}, "total", "reserve"),
        "reserve_max": _status_value_or_attr(roster, {"RES", "BN"}, "max", "reserve_max"),
        "injured": _status_value_or_attr(roster, {"IR", "IL", "INJ", "INJURED"}, "total", "injured"),
        "injured_max": _status_value_or_attr(roster, {"IR", "IL", "INJ", "INJURED"}, "max", "injured_max"),
        "period_number": getattr(roster, "period_number", None),
        "period_date": getattr(roster, "period_date", None) or None,
        "period_start": getattr(roster, "period_start", None) or getattr(roster, "period_date", None) or None,
        "period_end": getattr(roster, "period_end", None) or None,
        "period_source": getattr(roster, "period_source", None) or None,
        "period_conflict": getattr(roster, "period_conflict", False) is True,
    }
    if roster_data["period_source"]:
        roster_data["period_selection"] = {
            "period": roster_data["period_number"],
            "source": roster_data["period_source"],
        }
    if capture_lineup_policy and isinstance(roster, _RawRoster):
        roster_data["_lineup_change_policy"] = {
            **_lineup_change_policy_observation(roster._data, method="getTeamRosterInfo"),
            "methods_checked": ["getTeamRosterInfo"],
            "successful_methods": ["getTeamRosterInfo"],
        }
    return apply_trusted_slot_overrides(roster_data, slot_overrides)


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


def extract_matchup(
    api: FantraxAPI,
    my_team_id: str,
    *,
    editable_period_number: Any = None,
) -> dict | None:
    """Return the current weekly matchup from Fantrax's schedule view.

    This is the data Skipper needs for "how is my matchup going?" questions.
    Fantrax exposes it through the standings SCHEDULE view, wrapped by
    fantraxapi as scoring_period_results().
    """
    return extract_matchup_contexts(
        api,
        my_team_id,
        editable_period_number=editable_period_number,
    ).get("matchup")


def extract_matchup_contexts(
    api: FantraxAPI,
    my_team_id: str,
    *,
    editable_period_number: Any = None,
) -> dict[str, Any]:
    """Return chronological-current and roster-editable matchups independently."""
    try:
        periods = api.scoring_period_results(season=True, playoffs=False)
    except Exception as e:
        log.error("matchup schedule failed: %s", e)
        return {"matchup": None, "editable_matchup": None}
    if not periods:
        return {"matchup": None, "editable_matchup": None}

    today = datetime.now(timezone.utc).date()
    current = next(
        (
            period
            for period in periods.values()
            if getattr(period, "start", None)
            and getattr(period, "end", None)
            and getattr(period, "start") <= today <= getattr(period, "end")
        ),
        None,
    )
    if editable_period_number is None:
        try:
            editable_period_number = _team_roster(api, my_team_id).period_number
        except Exception:
            pass
    editable_period = _period_by_number(periods, editable_period_number)

    current_payload = _matchup_payload(current, my_team_id) if current is not None else None
    editable_payload = _matchup_payload(editable_period, my_team_id) if editable_period is not None else None

    completed_periods = [
        period
        for period in periods.values()
        if getattr(period, "end", None) and getattr(period, "end") < today
    ]
    for period in sorted(completed_periods, key=lambda item: getattr(item, "end"), reverse=True):
        completed_payload = _matchup_payload(period, my_team_id, complete=True)
        if completed_payload is None:
            continue
        if current_payload and completed_payload.get("period_number") != current_payload.get("period_number"):
            current_payload["latest_completed"] = completed_payload
        break
    if (
        editable_payload
        and current_payload
        and editable_payload.get("period_number") == current_payload.get("period_number")
    ):
        editable_payload = None
    return {"matchup": current_payload, "editable_matchup": editable_payload}


def _period_by_number(periods: dict[Any, Any], number: Any) -> Any:
    if number in (None, ""):
        return None
    for key, period in periods.items():
        period_number = getattr(getattr(period, "period", None), "number", None)
        if str(key) == str(number) or str(period_number) == str(number):
            return period
    return None


def _matchup_payload(period: Any, my_team_id: str, *, complete: bool | None = None) -> dict | None:
    for matchup in getattr(period, "matchups", []) or []:
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
        period_start = getattr(period, "start", None)
        not_started = bool(period_start and period_start > datetime.now(timezone.utc).date())
        score_state = "live_or_final"
        if not_started:
            zero_like = lambda value: value is None or str(value).strip() in {"0", "0.0"}
            if zero_like(my_score) and zero_like(opponent_score):
                my_score = 0
                opponent_score = 0
                score_state = "not_started"
            else:
                score_state = "invalid_future_score"
        margin = None
        if my_score is not None and opponent_score is not None:
            margin = round(float(my_score) - float(opponent_score), 2)
        return {
            "source": "fantrax_schedule",
            "period_number": getattr(getattr(period, "period", None), "number", None),
            "period_name": getattr(period, "name", None),
            "start": str(getattr(period, "start", "")) or None,
            "end": str(getattr(period, "end", "")) or None,
            "days": getattr(period, "days", None),
            "complete": bool(complete) if complete is not None else getattr(period, "complete", None),
            "current": getattr(period, "current", None),
            "matchup_key": getattr(matchup, "matchup_key", None),
            "my_team_id": my_team_id,
            "my_team_name": getattr(away if is_away else home, "name", None) or str(away if is_away else home),
            "my_side": "away" if is_away else "home",
            "my_score": my_score,
            "opponent_team_id": getattr(opponent, "id", None),
            "opponent_team_name": getattr(opponent, "name", None) or str(opponent),
            "opponent_score": opponent_score,
            "margin": margin,
            "score_state": score_state,
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
        response = _raw_request(api, "getPendingTransactions")
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


def _lineup_change_policy_observation(node: Any, *, method: str) -> dict[str, Any]:
    """Capture sanitized rule evidence without guessing cadence semantics.

    Fantrax rule payloads vary by endpoint and sport. Until an exact live path
    is fixture-backed, matching scalar fields are diagnostic evidence only and
    must never unlock the day-by-day optimizer.
    """
    candidates: list[dict[str, Any]] = []
    visited = 0

    def walk(value: Any, path: tuple[str, ...] = (), depth: int = 0) -> None:
        nonlocal visited
        visited += 1
        if depth > 12 or visited > 5000 or len(candidates) >= 40:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if visited >= 5000 or len(candidates) >= 40:
                    break
                walk(child, (*path, str(key)), depth + 1)
            return
        if isinstance(value, list):
            for index, child in enumerate(value[:50]):
                if visited >= 5000 or len(candidates) >= 40:
                    break
                walk(child, (*path, str(index)), depth + 1)
            return
        if not path or not _is_lineup_policy_path(path):
            return
        if value is None or isinstance(value, (str, int, float, bool)):
            hint = _lineup_policy_value_hint(value, path=path)
            if isinstance(value, str) and hint is None:
                return
            safe_path = _safe_lineup_policy_path(path)
            if safe_path:
                candidates.append({
                    "path": safe_path,
                    "value_type": type(value).__name__,
                    "hint": hint,
                })

    walk(node)
    if candidates:
        log.info(
            "Observed unclassified Fantrax lineup-policy paths via %s: %s",
            method,
            [candidate["path"] for candidate in candidates],
        )
    state = "observed_unclassified" if candidates else "missing"
    reason = (
        "Fantrax exposed possible lineup-policy fields, but no exact cadence/lock mapping is trusted yet."
        if candidates
        else "Fantrax league rules did not expose a recognizable lineup cadence or lock field."
    )
    return {
        "state": state,
        "cadence": None,
        "lock_scope": None,
        "change_limit": None,
        "source": f"fantrax.{method}.raw",
        "reason": reason,
        "candidates": candidates,
    }


def _is_lineup_policy_path(path: tuple[str, ...]) -> bool:
    leaf = re.sub(r"[^a-z0-9]", "", path[-1].casefold()) if path else ""
    if leaf in {"daily", "origdaily", "applytofutureperiods", "autosubmitlineupchanges"}:
        return True
    policy_tokens = ("change", "period", "lock", "deadline", "frequency", "system")
    if "lineup" in leaf and any(token in leaf for token in policy_tokens):
        return True
    return "roster" in leaf and any(token in leaf for token in policy_tokens)


def _safe_lineup_policy_path(path: tuple[str, ...]) -> str | None:
    safe: list[str] = []
    for part in path:
        if part.isdigit():
            safe.append("[]")
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", part):
            safe.append(part)
        else:
            return None
    return ".".join(safe)


def _lineup_policy_value_hint(value: Any, *, path: tuple[str, ...] = ()) -> str | None:
    leaf = re.sub(r"[^a-z0-9]", "", path[-1].casefold()) if path else ""
    if isinstance(value, bool) and leaf in {"daily", "origdaily"}:
        return "daily" if value else "not_daily"
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    if "daily" in normalized:
        return "daily"
    if "weekly" in normalized:
        return "weekly"
    if "individual" in normalized and "game" in normalized:
        return "player_game"
    if "global" in normalized and "day" in normalized:
        return "global_day"
    if normalized in {"period", "scoringperiod"}:
        return "period"
    if normalized == "classic":
        return "classic"
    if normalized in {"easyclick", "easyclicks"}:
        return "easy_click"
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
    table_header = data.get("tableHeader")
    header_cells = table_header.get("cells") if isinstance(table_header, dict) else None
    if isinstance(header_cells, list) and header_cells:
        keys = [
            item.get("shortName") or item.get("name") or item.get("key") or ""
            for item in header_cells
            if isinstance(item, dict)
        ]
        if keys and len(keys) == len(header_cells) and any(keys):
            return keys
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

    age = None
    age_source = None
    for key in ROSTER_AGE_FIELDS:
        age = _plausible_roster_age(scorer.get(key))
        if age is not None:
            age_source = f"raw.scorer.{key}"
            break
    if age is None:
        for key in ("Age", "AGE", "age"):
            age = _plausible_roster_age(stats.get(key))
            if age is not None:
                age_source = f"stats.{key}"
                break

    return {
        "id": scorer.get("scorerId") or scorer.get("playerId") or scorer.get("id"),
        "name": scorer.get("name") or scorer.get("fullName"),
        "team": scorer.get("teamShortName") or scorer.get("teamName"),
        "positions": scorer.get("posShortNames") or scorer.get("positions"),
        "multi_positions": entry.get("multiPositions"),
        "age": age,
        "age_source": age_source,
        "stats": stats,
    }


def _promote_roster_lineup_policy(snapshot: dict[str, Any]) -> None:
    """Move private roster evidence into the canonical league-rules slot."""
    roster = snapshot.get("roster")
    if not isinstance(roster, dict):
        return
    policy = roster.pop("_lineup_change_policy", None)
    if not isinstance(policy, dict):
        return
    snapshot["league_rules"] = {
        "method": "getTeamRosterInfo",
        "lineup_change_policy": policy,
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
        "editable_matchup": None,
        "completed_lineup_evidence": None,
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
        ("roster",           lambda: extract_roster(api, team_id, capture_lineup_policy=True)),
        ("all_team_rosters", lambda: extract_all_team_rosters(api, team_id)),
        ("standings",        lambda: extract_standings(api, team_id)),
        (
            "matchup_contexts",
            lambda: extract_matchup_contexts(
                api,
                team_id,
                editable_period_number=(snapshot.get("roster") or {}).get("period_number"),
            ),
        ),
        (
            "completed_lineup_evidence",
            lambda: extract_completed_lineup_evidence(
                api,
                team_id,
                ((snapshot.get("matchup_contexts") or {}).get("matchup") or {}).get("latest_completed"),
            ),
        ),
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

    matchup_contexts = snapshot.pop("matchup_contexts", None)
    if isinstance(matchup_contexts, dict):
        snapshot["matchup"] = matchup_contexts.get("matchup")
        snapshot["editable_matchup"] = matchup_contexts.get("editable_matchup")

    _promote_roster_lineup_policy(snapshot)

    return snapshot
