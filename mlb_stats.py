"""Wrapper around statsapi.mlb.com for player lookup and per-game logs.

V1 keeps it simple: an in-process cache of the season's active player list
(used to resolve fantrax names -> MLB person ids) plus a per-game gameLog
endpoint that we normalize into chat-friendly rows. No auth required.

`fpts_estimated` is a rough Yahoo-style baseline so the profile screen has a
per-game points column. The estimation is intentionally generic — league
scoring rules vary, so treat the value as directional, not authoritative.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timezone as _timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://statsapi.mlb.com/api/v1"
DEFAULT_TIMEOUT = 12
MEDIA_GAME_LIMIT = 5
MEDIA_ITEM_LIMIT = 5

_ERROR_TTL = 120.0  # seconds a failed fetch is cached before retrying


class _TTLCache:
    """Tiny per-process TTL cache for MLB Stats API responses.

    Each instance has its own lock, held across the fetch so concurrent
    callers don't duplicate a request — but a slow fetch for one cache no
    longer serializes the others. `fetch` returns (value, ok): failures are
    cached only for `error_ttl` seconds, and if a previous good value exists
    it keeps being served through the outage until a retry succeeds.
    """

    def __init__(self, ttl: float, error_ttl: float = _ERROR_TTL):
        self._ttl = ttl
        self._error_ttl = error_ttl
        self._lock = threading.Lock()
        self._entries: dict[Any, tuple[float, Any]] = {}

    def get_or_fetch(self, key: Any, fetch) -> Any:
        entry = self._entries.get(key)
        if entry is not None and entry[0] > time.monotonic():
            return entry[1]
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > time.monotonic():
                return entry[1]
            value, ok = fetch()
            if not ok and entry is not None:
                value = entry[1]
            self._entries[key] = (time.monotonic() + (self._ttl if ok else self._error_ttl), value)
            return value


# Players get called up, traded, and activated mid-season, so the index
# refreshes a few times a day. Team abbreviations are static within a season.
_PLAYER_INDEX_CACHE = _TTLCache(ttl=6 * 3600)
_TEAM_ABBREV_CACHE = _TTLCache(ttl=24 * 3600)

_TEAM_ABBREV_ALIASES = {
    # Fantrax sometimes uses different abbreviations than MLB Stats API.
    "WSH": "WSH", "WAS": "WSH", "WSN": "WSH",
    "CHW": "CWS", "CWS": "CWS", "CHA": "CWS",
    "CHC": "CHC", "CHN": "CHC",
    "KC":  "KC",  "KCR": "KC",
    "LAA": "LAA", "ANA": "LAA",
    "MIA": "MIA", "FLA": "MIA",
    "SD":  "SD",  "SDP": "SD", "SDN": "SD",
    "SF":  "SF",  "SFG": "SF", "SFN": "SF",
    "TB":  "TB",  "TBR": "TB",
    "NYM": "NYM", "NYN": "NYM",
    "NYY": "NYY", "NYA": "NYY",
    "ARI": "ARI", "AZ":  "ARI",
}

_STATIC_TEAM_IDS_BY_ABBREV = {
    # Stable MLB Stats API team ids. Used as a fallback if /teams is
    # unavailable during a refresh.
    "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112,
    "CIN": 113, "CLE": 114, "COL": 115, "CWS": 145, "DET": 116,
    "HOU": 117, "KC": 118, "LAA": 108, "LAD": 119, "MIA": 146,
    "MIL": 158, "MIN": 142, "NYM": 121, "NYY": 147, "OAK": 133,
    "ATH": 133, "PHI": 143, "PIT": 134, "SD": 135, "SEA": 136,
    "SF": 137, "STL": 138, "TB": 139, "TEX": 140, "TOR": 141,
    "WSH": 120,
}

_EXCLUDED_SCHEDULE_STATES = {
    "cancelled",
    "canceled",
    "final",
    "game over",
    "postponed",
    "suspended",
}


def _normalize(s: str) -> str:
    # Transliterate accents first (Muñoz -> Munoz, Suárez -> Suarez) so
    # Fantrax's unaccented names match MLB's accented ones; stripping the
    # bytes instead would turn "Muñoz" into "muoz" and never match.
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _normalize_team(t: str | None) -> str | None:
    if not t:
        return None
    up = t.strip().upper()
    return _TEAM_ABBREV_ALIASES.get(up, up)


def current_season() -> int:
    return _date.today().year


def _get_active_players(season: int) -> list[dict[str, Any]]:
    def fetch() -> tuple[list[dict[str, Any]], bool]:
        try:
            url = f"{BASE_URL}/sports/1/players"
            params = {
                "season": season,
                "fields": "people,id,fullName,firstLastName,currentTeam,abbreviation,name,primaryPosition,code,active",
            }
            resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            people = resp.json().get("people") or []
            log.info("MLB active player index loaded for season %s (%d players)", season, len(people))
            return people, True
        except Exception as exc:
            log.warning("MLB active player fetch failed for season %s: %s", season, exc)
            return [], False

    return _PLAYER_INDEX_CACHE.get_or_fetch(season, fetch)


def lookup_player_by_name(
    name: str,
    team: str | None = None,
    season: int | None = None,
) -> int | None:
    """Return MLB person id matching `name` (and optionally `team` abbreviation)."""
    if not name:
        return None
    season = season or current_season()
    target_norm = _normalize(name)
    target_team = _normalize_team(team)

    people = _get_active_players(season)

    matches: list[dict[str, Any]] = []
    for p in people:
        full = p.get("fullName") or p.get("firstLastName") or ""
        if _normalize(full) == target_norm:
            matches.append(p)

    if not matches:
        return None

    if target_team and len(matches) > 1:
        team_filtered = [
            p for p in matches
            if _normalize_team((p.get("currentTeam") or {}).get("abbreviation")) == target_team
        ]
        if team_filtered:
            matches = team_filtered

    return matches[0].get("id")


def _get_team_abbreviations(season: int) -> dict[int, str]:
    def fetch() -> tuple[dict[int, str], bool]:
        try:
            url = f"{BASE_URL}/teams"
            resp = requests.get(
                url,
                params={"sportId": 1, "season": season, "fields": "teams,id,abbreviation,name"},
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            mapping = {
                int(t["id"]): t.get("abbreviation") or t.get("name") or ""
                for t in (resp.json().get("teams") or [])
                if t.get("id") is not None
            }
            return mapping, bool(mapping)
        except Exception as exc:
            log.warning("MLB teams lookup failed for season %s: %s", season, exc)
            return {}, False

    return _TEAM_ABBREV_CACHE.get_or_fetch(season, fetch)


def team_id_by_abbreviation(team_abbr: str | None, season: int | None = None) -> int | None:
    """Resolve an MLB team abbreviation to the Stats API numeric team id.

    Fantrax and MLB disagree on a few abbreviations, so callers should use this
    instead of silently treating an unknown abbreviation as an empty schedule.
    """
    normalized = _normalize_team(team_abbr)
    if not normalized:
        return None
    season = season or current_season()
    try:
        for team_id, abbrev in _get_team_abbreviations(season).items():
            if _normalize_team(abbrev) == normalized:
                return int(team_id)
    except Exception as exc:
        log.warning("MLB team id lookup failed for %s/%s: %s", team_abbr, season, exc)
    return _STATIC_TEAM_IDS_BY_ABBREV.get(normalized)


def fetch_team_schedule(
    team_id: int,
    start_date: _date | str,
    end_date: _date | str,
    *,
    season: int | None = None,
    now: _datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch remaining MLB games for one team in a date window.

    The returned list is normalized and excludes games that have already
    started by `now`, plus postponed/cancelled/suspended/final games.
    """
    start = _date_param(start_date)
    end = _date_param(end_date)
    url = f"{BASE_URL}/schedule"
    params = {
        "sportId": 1,
        "teamId": int(team_id),
        "startDate": start,
        "endDate": end,
        "hydrate": "probablePitcher",
    }
    resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    year = season or _parse_date_only(start).year
    return normalize_schedule_games(
        resp.json(),
        team_id=int(team_id),
        team_abbrev=_get_team_abbreviations(year),
        now=now,
    )


def normalize_schedule_games(
    payload: dict[str, Any],
    *,
    team_id: int,
    team_abbrev: dict[int, str] | None = None,
    now: _datetime | None = None,
) -> list[dict[str, Any]]:
    """Normalize MLB schedule payloads into projection-safe game rows."""
    if now is None:
        now = _datetime.now(_timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_timezone.utc)

    games: list[dict[str, Any]] = []
    for date_entry in payload.get("dates") or []:
        for game in date_entry.get("games") or []:
            normalized = _normalize_schedule_game(game, team_id=team_id, team_abbrev=team_abbrev or {}, now=now)
            if normalized:
                games.append(normalized)
    games.sort(key=lambda game: (game.get("gameDate") or "", game.get("game_pk") or 0))
    return games


def fetch_game_log(
    mlb_id: int,
    season: int,
    group: str = "hitting",
) -> list[dict[str, Any]]:
    """Fetch the player's per-game log for the given season + stat group."""
    if group not in ("hitting", "pitching"):
        raise ValueError(f"group must be 'hitting' or 'pitching', got {group!r}")

    url = f"{BASE_URL}/people/{mlb_id}/stats"
    params = {"stats": "gameLog", "group": group, "season": season}
    resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    splits: list[dict[str, Any]] = []
    for entry in (data.get("stats") or []):
        group_name = ((entry.get("group") or {}).get("displayName") or "").lower()
        if group_name == group:
            splits = entry.get("splits") or []
            break
    if not splits and (data.get("stats") or []):
        splits = (data["stats"][0] or {}).get("splits") or []

    team_abbrev = _get_team_abbreviations(season)
    games = [_normalize_split(sp, group, team_abbrev) for sp in splits]
    games.sort(key=lambda g: g.get("date") or "", reverse=False)
    return games


def fetch_player_media(
    mlb_id: int,
    player_name: str,
    games: list[dict[str, Any]],
    *,
    limit: int = MEDIA_ITEM_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch recent MLB content/highlights connected to a player's games.

    MLB content is organized by game, so we search recent game content and keep
    clips/news whose title or description mentions the player. Missing content
    is normal and returns an empty list.
    """
    player_tokens = _media_name_tokens(player_name)
    game_pks: list[int] = []
    for game in reversed(games or []):
        game_pk = game.get("game_pk")
        if game_pk is None:
            continue
        try:
            game_pk = int(game_pk)
        except (TypeError, ValueError):
            continue
        if game_pk not in game_pks:
            game_pks.append(game_pk)
        if len(game_pks) >= MEDIA_GAME_LIMIT:
            break

    items: list[dict[str, Any]] = []
    for game_pk in game_pks:
        try:
            items.extend(_fetch_game_media_items(game_pk, player_tokens))
        except Exception as exc:
            log.warning("MLB media fetch failed for game %s/player %s: %s", game_pk, mlb_id, exc)
        if len(items) >= limit:
            break
    return items[:limit]


def _fetch_game_media_items(game_pk: int, player_tokens: set[str]) -> list[dict[str, Any]]:
    url = f"{BASE_URL}/game/{game_pk}/content"
    resp = requests.get(url, params={"highlightLimit": 20}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    raw_items = (((data.get("highlights") or {}).get("highlights") or {}).get("items") or [])
    parsed: list[dict[str, Any]] = []
    for item in raw_items:
        title = item.get("title") or item.get("headline") or ""
        description = item.get("description") or item.get("blurb") or ""
        searchable = _normalize(f"{title} {description}")
        if player_tokens and not any(token in searchable for token in player_tokens):
            continue
        parsed.append({
            "id": item.get("id") or item.get("guid") or f"{game_pk}:{len(parsed)}",
            "kind": "video",
            "source": "MLB",
            "game_pk": game_pk,
            "date": item.get("date") or item.get("timestamp") or item.get("releaseDate"),
            "title": title,
            "caption": description,
            "url": item.get("url") or item.get("href") or _best_playback_url(item),
            "thumbnail": _best_thumbnail_url(item),
            "duration": item.get("duration"),
        })
    return parsed


def _media_name_tokens(player_name: str) -> set[str]:
    parts = _normalize(player_name).split()
    if not parts:
        return set()
    tokens = {player_name.lower(), " ".join(parts)}
    if len(parts[-1]) >= 4:
        tokens.add(parts[-1])
    return {_normalize(t) for t in tokens if t}


def _best_playback_url(item: dict[str, Any]) -> str | None:
    for playback in item.get("playbacks") or []:
        url = playback.get("url")
        if url:
            return url
    return None


def _best_thumbnail_url(item: dict[str, Any]) -> str | None:
    image = item.get("image") or {}
    cuts = image.get("cuts") or []
    if cuts:
        preferred = sorted(
            cuts,
            key=lambda c: ((c.get("width") or 0) * (c.get("height") or 0)),
            reverse=True,
        )[0]
        return preferred.get("src")
    return image.get("src")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_split(
    split: dict[str, Any],
    group: str,
    team_abbrev: dict[int, str] | None = None,
) -> dict[str, Any]:
    stat = split.get("stat") or {}
    opponent = split.get("opponent") or {}
    date = split.get("date") or split.get("officialDate")
    is_home = bool(split.get("isHome"))
    opp_id = opponent.get("id")
    opp_abbrev = (
        (team_abbrev or {}).get(int(opp_id)) if opp_id is not None else None
    ) or opponent.get("abbreviation") or opponent.get("name") or ""
    base = {
        "date": date,
        "opponent": opp_abbrev,
        "home": is_home,
        "game_pk": (split.get("game") or {}).get("gamePk") or split.get("gamePk"),
    }
    if group == "hitting":
        ab = _to_int(stat.get("atBats"))
        h = _to_int(stat.get("hits"))
        hr = _to_int(stat.get("homeRuns"))
        rbi = _to_int(stat.get("rbi"))
        bb = _to_int(stat.get("baseOnBalls"))
        k = _to_int(stat.get("strikeOuts"))
        sb = _to_int(stat.get("stolenBases"))
        runs = _to_int(stat.get("runs"))
        doubles = _to_int(stat.get("doubles"))
        triples = _to_int(stat.get("triples"))
        hbp = _to_int(stat.get("hitByPitch"))
        cs = _to_int(stat.get("caughtStealing"))
        avg_game = (h / ab) if ab else None
        base.update({
            "ab": ab, "h": h, "hr": hr, "rbi": rbi, "bb": bb, "k": k, "sb": sb,
            "r": runs, "doubles": doubles, "triples": triples,
            "hbp": hbp, "cs": cs,
            "avg_game": round(avg_game, 3) if avg_game is not None else None,
            "line": _hitting_line(ab, h, hr, rbi, bb, k, sb, doubles, triples),
            "fpts_estimated": _hitting_fpts(h, doubles, triples, hr, rbi, bb, sb, k, runs),
        })
        return base
    # pitching
    raw_ip = stat.get("inningsPitched")
    ip = _innings_pitched(raw_ip)
    h = _to_int(stat.get("hits"))
    er = _to_int(stat.get("earnedRuns"))
    bb = _to_int(stat.get("baseOnBalls"))
    k = _to_int(stat.get("strikeOuts"))
    win = _to_int(stat.get("wins")) > 0
    save = _to_int(stat.get("saves")) > 0
    loss = _to_int(stat.get("losses")) > 0
    hold = _to_int(stat.get("holds")) > 0
    gs = _to_int(stat.get("gamesStarted")) > 0
    base.update({
        "ip": ip, "ip_display": _innings_display(raw_ip, ip),
        "h": h, "er": er, "bb": bb, "k": k,
        "win": win, "save": save,
        "loss": loss, "hold": hold, "gs": gs,
        # Quality start: 6+ innings, 3 or fewer earned runs, as the starter.
        "qs": bool(gs and ip >= 6.0 and er <= 3),
        "avg_game": None,
        "line": _pitching_line(ip, h, er, bb, k, win, save, ip_display=_innings_display(raw_ip, ip)),
        "fpts_estimated": _pitching_fpts(ip, k, er, bb, h, win, save),
    })
    return base


def _hitting_line(ab: int, h: int, hr: int, rbi: int, bb: int, k: int, sb: int, doubles: int, triples: int) -> str:
    parts = [f"{h}-{ab}"]
    extras: list[str] = []
    if hr:
        extras.append(f"{hr} HR" if hr > 1 else "HR")
    if triples:
        extras.append(f"{triples} 3B" if triples > 1 else "3B")
    if doubles:
        extras.append(f"{doubles} 2B" if doubles > 1 else "2B")
    if rbi:
        extras.append(f"{rbi} RBI")
    if bb:
        extras.append(f"{bb} BB" if bb > 1 else "BB")
    if sb:
        extras.append(f"{sb} SB" if sb > 1 else "SB")
    if k and not (hr or rbi or bb or sb or doubles or triples):
        extras.append(f"{k} K" if k > 1 else "K")
    return ", ".join(parts + extras)


def _pitching_line(
    ip: float,
    h: int,
    er: int,
    bb: int,
    k: int,
    win: bool,
    save: bool,
    *,
    ip_display: str | None = None,
) -> str:
    ip_str = ip_display or f"{ip:.1f}".rstrip("0").rstrip(".")
    parts = [f"{ip_str} IP", f"{k} K", f"{er} ER"]
    if h:
        parts.append(f"{h} H")
    if bb:
        parts.append(f"{bb} BB")
    if win:
        parts.append("W")
    if save:
        parts.append("SV")
    return ", ".join(parts)


# Yahoo-style baseline scoring; per-league rules vary so this is approximate.
def _hitting_fpts(h: int, doubles: int, triples: int, hr: int, rbi: int, bb: int, sb: int, k: int, runs: int) -> float:
    singles = max(0, h - doubles - triples - hr)
    return round(
        1.0 * singles + 2.0 * doubles + 3.0 * triples + 4.0 * hr
        + 1.0 * rbi + 1.0 * bb + 1.0 * sb + 1.0 * runs - 1.0 * k,
        2,
    )


def _pitching_fpts(ip: float, k: int, er: int, bb: int, h: int, win: bool, save: bool) -> float:
    return round(
        2.25 * ip + 1.0 * k - 2.0 * er - 1.0 * bb - 1.0 * h
        + (5.0 if win else 0.0) + (5.0 if save else 0.0),
        2,
    )


def _normalize_schedule_game(
    game: dict[str, Any],
    *,
    team_id: int,
    team_abbrev: dict[int, str],
    now: _datetime,
) -> dict[str, Any] | None:
    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    home_team = home.get("team") if isinstance(home.get("team"), dict) else {}
    away_team = away.get("team") if isinstance(away.get("team"), dict) else {}
    home_id = _to_int(home_team.get("id"))
    away_id = _to_int(away_team.get("id"))
    if team_id not in {home_id, away_id}:
        return None

    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    detailed_state = str(status.get("detailedState") or status.get("abstractGameState") or "").strip()
    if _schedule_state_excluded(detailed_state):
        return None

    game_dt = _parse_mlb_datetime(game.get("gameDate"))
    if game_dt is not None and game_dt <= now:
        return None

    is_home = team_id == home_id
    opponent_id = away_id if is_home else home_id
    home_abbr = _team_abbrev(home_id, home_team, team_abbrev)
    away_abbr = _team_abbrev(away_id, away_team, team_abbrev)
    home_probable = _probable_pitcher(home.get("probablePitcher"))
    away_probable = _probable_pitcher(away.get("probablePitcher"))
    team_probable = home_probable if is_home else away_probable

    normalized: dict[str, Any] = {
        "date": game.get("officialDate") or (game_dt.date().isoformat() if game_dt else None),
        "gameDate": _format_mlb_datetime(game_dt) if game_dt else game.get("gameDate"),
        "game_pk": game.get("gamePk"),
        "status": detailed_state or None,
        "home": is_home,
        "opponent": _team_abbrev(opponent_id, away_team if is_home else home_team, team_abbrev),
        "home_team_id": home_id or None,
        "away_team_id": away_id or None,
        "home_team": home_abbr,
        "away_team": away_abbr,
        "doubleheader": game.get("doubleHeader") or None,
        "source": "mlb_schedule",
    }
    if home_probable:
        normalized["home_probable_pitcher"] = home_probable
    if away_probable:
        normalized["away_probable_pitcher"] = away_probable
    if team_probable:
        normalized["probable_pitcher"] = team_probable
    return {key: value for key, value in normalized.items() if value not in (None, "")}


def _schedule_state_excluded(state: str) -> bool:
    state_cf = state.strip().casefold()
    if not state_cf:
        return False
    return any(excluded in state_cf for excluded in _EXCLUDED_SCHEDULE_STATES)


def _team_abbrev(team_id: int, team: dict[str, Any], team_abbrev: dict[int, str]) -> str:
    return (
        team_abbrev.get(int(team_id))
        or team.get("abbreviation")
        or team.get("teamCode")
        or team.get("fileCode")
        or team.get("name")
        or str(team_id)
    )


def _probable_pitcher(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    pitcher = {
        "id": value.get("id"),
        "name": value.get("fullName") or value.get("firstLastName") or value.get("name"),
    }
    pitcher = {key: val for key, val in pitcher.items() if val not in (None, "")}
    return pitcher or None


def _date_param(value: _date | str) -> str:
    if isinstance(value, _date):
        return value.isoformat()
    parsed = _parse_date_only(value)
    return parsed.isoformat()


def _parse_date_only(value: Any) -> _date:
    if isinstance(value, _date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("date value is required")
    if "T" in text:
        dt = _parse_mlb_datetime(text)
        if dt is None:
            raise ValueError(f"invalid date value: {value!r}")
        return dt.date()
    return _date.fromisoformat(text[:10])


def _parse_mlb_datetime(value: Any) -> _datetime | None:
    if not value:
        return None
    if isinstance(value, _datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            dt = _datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_timezone.utc)
    return dt.astimezone(_timezone.utc)


def _format_mlb_datetime(value: _datetime) -> str:
    return value.astimezone(_timezone.utc).isoformat().replace("+00:00", "Z")


def _to_int(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def _to_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _innings_pitched(value: Any) -> float:
    """Convert MLB's baseball notation (6.2 = 6 innings, 2 outs) to innings."""
    text = str(value or "0").strip()
    match = re.fullmatch(r"(\d+)(?:\.([0-2]))?", text)
    if match:
        innings = int(match.group(1))
        outs = int(match.group(2) or 0)
        return innings + outs / 3.0
    parsed = _to_float(value)
    if parsed < 0:
        return 0.0
    log.warning("Unexpected inningsPitched value %r; treating as decimal innings", value)
    return parsed


def _innings_display(value: Any, innings: float) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d+(?:\.[0-2])?", text):
        return text if "." in text else f"{text}.0"
    return f"{innings:.2f}".rstrip("0").rstrip(".")
