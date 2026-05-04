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
from datetime import date as _date
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://statsapi.mlb.com/api/v1"
DEFAULT_TIMEOUT = 12

_PLAYER_INDEX_CACHE: dict[int, list[dict[str, Any]]] = {}
_TEAM_ABBREV_CACHE: dict[int, dict[int, str]] = {}
_CACHE_LOCK = threading.Lock()

_TEAM_ABBREV_ALIASES = {
    # Fantrax sometimes uses different abbreviations than MLB Stats API.
    "WSH": "WSH", "WAS": "WSH",
    "CHW": "CWS", "CWS": "CWS",
    "KC":  "KC",  "KCR": "KC",
    "SD":  "SD",  "SDP": "SD",
    "SF":  "SF",  "SFG": "SF",
    "TB":  "TB",  "TBR": "TB",
    "ARI": "ARI", "AZ":  "ARI",
}


def _normalize(s: str) -> str:
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
    cached = _PLAYER_INDEX_CACHE.get(season)
    if cached is not None:
        return cached
    with _CACHE_LOCK:
        cached = _PLAYER_INDEX_CACHE.get(season)
        if cached is not None:
            return cached
        url = f"{BASE_URL}/sports/1/players"
        params = {
            "season": season,
            "fields": "people,id,fullName,firstLastName,currentTeam,abbreviation,name,primaryPosition,abbreviation,code,active",
        }
        resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        people = resp.json().get("people") or []
        _PLAYER_INDEX_CACHE[season] = people
        log.info("MLB active player index loaded for season %s (%d players)", season, len(people))
        return people


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

    try:
        people = _get_active_players(season)
    except Exception as exc:
        log.warning("MLB active player fetch failed: %s", exc)
        return None

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
    cached = _TEAM_ABBREV_CACHE.get(season)
    if cached is not None:
        return cached
    with _CACHE_LOCK:
        cached = _TEAM_ABBREV_CACHE.get(season)
        if cached is not None:
            return cached
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
        except Exception as exc:
            log.warning("MLB teams lookup failed for season %s: %s", season, exc)
            mapping = {}
        _TEAM_ABBREV_CACHE[season] = mapping
        return mapping


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
        avg_game = (h / ab) if ab else None
        base.update({
            "ab": ab, "h": h, "hr": hr, "rbi": rbi, "bb": bb, "k": k, "sb": sb,
            "r": runs, "doubles": doubles, "triples": triples,
            "avg_game": round(avg_game, 3) if avg_game is not None else None,
            "line": _hitting_line(ab, h, hr, rbi, bb, k, sb, doubles, triples),
            "fpts_estimated": _hitting_fpts(h, doubles, triples, hr, rbi, bb, sb, k, runs),
        })
        return base
    # pitching
    ip = _to_float(stat.get("inningsPitched"))
    h = _to_int(stat.get("hits"))
    er = _to_int(stat.get("earnedRuns"))
    bb = _to_int(stat.get("baseOnBalls"))
    k = _to_int(stat.get("strikeOuts"))
    win = _to_int(stat.get("wins")) > 0
    save = _to_int(stat.get("saves")) > 0
    base.update({
        "ip": ip, "h": h, "er": er, "bb": bb, "k": k,
        "win": win, "save": save,
        "avg_game": None,
        "line": _pitching_line(ip, h, er, bb, k, win, save),
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


def _pitching_line(ip: float, h: int, er: int, bb: int, k: int, win: bool, save: bool) -> str:
    ip_str = f"{ip:.1f}".rstrip("0").rstrip(".")
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
