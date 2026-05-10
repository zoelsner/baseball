"""Deterministic win-probability for the current head-to-head matchup.

Pure-Python compute over the snapshot blob — no I/O, no model calls. Same
inputs always produce the same output, so the answer is consistent and
auditable. Method tag is bumped (`roster_form_v1` → `roster_form_v2` etc.)
when the math changes.

The model:
- Project remaining FP per side as the sum of expected per-day contributions
  for each rostered player on the active side.
- Hitters contribute fppg * form_factor on every day their MLB team plays.
- Starting pitchers contribute fppg_per_start * form_factor on probable-start
  days (or every-5th-day if probables aren't published yet).
- Relievers contribute fppg * form_factor * RP_RATE on team-game days.
- form_factor = mean(last_7_games_fpts_estimated) / season_fppg, clamped to
  [0.5, 2.0]. Falls back to 1.0 when game logs aren't available.
- Bench/IR slots contribute zero by default but their projected upside is
  surfaced as `bench_upside` so Skipper can call out start/sit swaps.
- Win probability uses a normal-CDF approximation on the projected margin
  with sigma scaled by sqrt(days_remaining).
"""

from __future__ import annotations

import logging
import math
from datetime import date as _date, datetime, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

METHOD = "roster_form_v1"
RECENT_FORM_GAMES = 7
FORM_FLOOR = 0.5
FORM_CEILING = 2.0
RP_APPEARANCE_RATE = 0.4
SP_FALLBACK_INTERVAL_DAYS = 5
PER_DAY_VOLATILITY = 12.0  # FP std-dev per remaining day (rough — calibratable)
SIGMA_FLOOR = 25.0
RESERVE_SLOTS = {"BN", "RES", "MIN"}
INJURED_SLOTS = {"IL", "IR"}
PITCHER_TOKENS = {"P", "SP", "RP"}


def compute(snapshot: dict[str, Any], today: _date | None = None) -> dict[str, Any] | None:
    """Return win-probability payload for the current matchup, or None.

    None is returned when there isn't enough signal to be useful (no matchup,
    no opponent roster, period already complete, period hasn't started).
    """
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    if not matchup:
        return None
    if matchup.get("complete"):
        return _final_payload(matchup)

    today = today or datetime.now(timezone.utc).date()
    start = _parse_date(matchup.get("start"))
    end = _parse_date(matchup.get("end"))
    if not start or not end or today > end:
        return None

    days_total = _days_inclusive(start, end)
    elapsed_days = max(0, _days_inclusive(start, today) - 1)
    days_remaining = max(0, _days_inclusive(today, end))
    if days_remaining <= 0:
        return _final_payload(matchup)

    my_score = _num(matchup.get("my_score")) or 0.0
    opp_score = _num(matchup.get("opponent_score")) or 0.0

    my_rows = ((snapshot.get("roster") or {}).get("rows")) or []
    opp_rows = _opponent_rows(snapshot, matchup)

    recent = snapshot.get("mlb_recent_games") or {}
    probables = snapshot.get("probable_pitchers") or {}

    my_proj_remaining, my_active_breakdown, my_bench_upside = _project_team(
        my_rows, today, end, recent, probables,
    )
    opp_proj_remaining, _opp_breakdown, _opp_bench = _project_team(
        opp_rows, today, end, recent, probables,
    )

    proj_my_total = round(my_score + my_proj_remaining, 2)
    proj_opp_total = round(opp_score + opp_proj_remaining, 2)
    margin_now = round(my_score - opp_score, 2)
    margin_proj = round(proj_my_total - proj_opp_total, 2)

    sigma = max(SIGMA_FLOOR, PER_DAY_VOLATILITY * math.sqrt(days_remaining * 2))
    win_pct = _normal_cdf(margin_proj / sigma)

    confidence = _confidence(
        days_remaining=days_remaining,
        opp_rows=opp_rows,
        probables=probables,
        recent=recent,
        my_rows=my_rows,
    )

    return {
        "method": METHOD,
        "win_pct": round(win_pct, 4),
        "win_pct_pretty": f"{round(win_pct * 100):d}%",
        "my_score": my_score,
        "opp_score": opp_score,
        "my_proj_remaining": round(my_proj_remaining, 2),
        "opp_proj_remaining": round(opp_proj_remaining, 2),
        "my_proj_total": proj_my_total,
        "opp_proj_total": proj_opp_total,
        "margin_now": margin_now,
        "margin_proj": margin_proj,
        "days_total": days_total,
        "elapsed_days": elapsed_days,
        "days_remaining": days_remaining,
        "sigma": round(sigma, 2),
        "confidence": confidence,
        "bench_upside": round(my_bench_upside, 2),
        "active_breakdown": my_active_breakdown[:8],
    }


def _final_payload(matchup: dict[str, Any]) -> dict[str, Any]:
    my_score = _num(matchup.get("my_score")) or 0.0
    opp_score = _num(matchup.get("opponent_score")) or 0.0
    margin_now = round(my_score - opp_score, 2)
    if margin_now > 0:
        win_pct = 1.0
    elif margin_now < 0:
        win_pct = 0.0
    else:
        win_pct = 0.5
    return {
        "method": METHOD,
        "win_pct": win_pct,
        "win_pct_pretty": f"{int(win_pct * 100)}%",
        "my_score": my_score,
        "opp_score": opp_score,
        "my_proj_remaining": 0.0,
        "opp_proj_remaining": 0.0,
        "my_proj_total": my_score,
        "opp_proj_total": opp_score,
        "margin_now": margin_now,
        "margin_proj": margin_now,
        "days_total": None,
        "elapsed_days": None,
        "days_remaining": 0,
        "sigma": 0.0,
        "confidence": "final",
        "bench_upside": 0.0,
        "active_breakdown": [],
    }


def _opponent_rows(snapshot: dict[str, Any], matchup: dict[str, Any]) -> list[dict[str, Any]]:
    all_rosters = snapshot.get("all_team_rosters") or {}
    if not isinstance(all_rosters, dict):
        return []
    opp_id = matchup.get("opponent_team_id")
    opp = all_rosters.get(opp_id) if opp_id else None
    if not isinstance(opp, dict):
        opp_name = matchup.get("opponent_team_name")
        for team in all_rosters.values():
            if isinstance(team, dict) and team.get("team_name") == opp_name:
                opp = team
                break
    if not isinstance(opp, dict):
        return []
    return opp.get("rows") or []


def _project_team(
    rows: list[dict[str, Any]],
    today: _date,
    end: _date,
    recent: dict[str, Any],
    probables: dict[str, Any],
) -> tuple[float, list[dict[str, Any]], float]:
    """Sum expected remaining FP for one side. Returns (active_total, breakdown, bench_upside)."""
    if not rows:
        return 0.0, [], 0.0

    by_pitcher_dates = (probables.get("by_pitcher_mlb_id") or {}) if isinstance(probables, dict) else {}
    by_date = (probables.get("by_date") or {}) if isinstance(probables, dict) else {}
    fetched_for = set((probables.get("fetched_for") or [])) if isinstance(probables, dict) else set()

    remaining_dates = list(_date_range(today, end))
    if not remaining_dates:
        return 0.0, [], 0.0

    active_total = 0.0
    bench_upside = 0.0
    breakdown: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        slot = (row.get("slot") or "").upper()
        injury = row.get("injury")
        if slot in INJURED_SLOTS or injury in {"OUT", "IR", "SUSP"}:
            continue
        fppg = _num(row.get("fppg")) or 0.0
        if fppg <= 0:
            continue

        is_pitcher = _is_pitcher(row)
        is_starter = _is_starter(row)
        team_abbr = _team(row)
        form = _form_factor(row, recent)

        per_player = _expected_remaining_fp(
            row=row,
            fppg=fppg,
            form=form,
            is_pitcher=is_pitcher,
            is_starter=is_starter,
            team_abbr=team_abbr,
            remaining_dates=remaining_dates,
            by_pitcher_dates=by_pitcher_dates,
            by_date=by_date,
            fetched_for=fetched_for,
        )

        if slot in RESERVE_SLOTS:
            bench_upside += per_player
        else:
            active_total += per_player
            breakdown.append({
                "id": row.get("id"),
                "name": row.get("name"),
                "slot": slot,
                "team": team_abbr,
                "fppg": fppg,
                "form": round(form, 2),
                "expected_remaining": round(per_player, 2),
            })

    breakdown.sort(key=lambda b: b.get("expected_remaining") or 0.0, reverse=True)
    return active_total, breakdown, bench_upside


def _expected_remaining_fp(
    *,
    row: dict[str, Any],
    fppg: float,
    form: float,
    is_pitcher: bool,
    is_starter: bool,
    team_abbr: str | None,
    remaining_dates: list[_date],
    by_pitcher_dates: dict[str, list[str]],
    by_date: dict[str, dict[str, dict[str, Any]]],
    fetched_for: set[str],
) -> float:
    """Per-player expected FP across remaining dates."""
    expected = 0.0

    if is_pitcher and is_starter:
        # Probable-start days, fall back to every-5th-day if MLB hasn't published.
        mlb_id = _player_mlb_id_from_probables(row, by_date, team_abbr)
        starts: list[_date] = []
        if mlb_id:
            for iso in by_pitcher_dates.get(str(mlb_id), []):
                d = _parse_date(iso)
                if d and d in remaining_dates:
                    starts.append(d)
        # Days where MLB hasn't published probables yet → assume one start
        # per SP_FALLBACK_INTERVAL_DAYS.
        unpublished = [d for d in remaining_dates if d.isoformat() not in fetched_for or d.isoformat() not in by_date]
        if not starts and unpublished:
            implied_starts = max(1, len(unpublished) // SP_FALLBACK_INTERVAL_DAYS)
            for _ in range(implied_starts):
                expected += fppg * form
            return expected
        for _ in starts:
            expected += fppg * form
        return expected

    if is_pitcher:
        # Reliever: appearance rate × team plays.
        team_play_days = sum(1 for d in remaining_dates if _team_plays(d, team_abbr, by_date))
        if not by_date and team_abbr:
            # No probable schedule loaded — assume team plays every other day.
            team_play_days = max(1, len(remaining_dates) * 2 // 3)
        expected += fppg * form * RP_APPEARANCE_RATE * team_play_days
        return expected

    # Hitter: 1 game per team-play-day.
    team_play_days = sum(1 for d in remaining_dates if _team_plays(d, team_abbr, by_date))
    if not by_date and team_abbr:
        team_play_days = max(1, len(remaining_dates) * 2 // 3)
    expected += fppg * form * team_play_days
    return expected


def _team_plays(d: _date, team_abbr: str | None, by_date: dict[str, dict[str, dict[str, Any]]]) -> bool:
    """True when the probable-pitchers payload reports this team playing on date d.

    `by_date` is keyed by ISO date → team_abbr → pitcher info. We treat any
    presence on that date (even without a probable pitcher) as the team
    having a game. When `by_date` is empty for that date, the caller falls
    back to a heuristic.
    """
    if not team_abbr:
        return False
    iso = d.isoformat()
    day = by_date.get(iso) or {}
    return team_abbr in day


def _is_pitcher(row: dict[str, Any]) -> bool:
    tokens = _position_tokens(row)
    return bool(tokens & PITCHER_TOKENS)


def _is_starter(row: dict[str, Any]) -> bool:
    tokens = _position_tokens(row)
    return "SP" in tokens


def _position_tokens(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for field in (row.get("slot"), row.get("positions")):
        if isinstance(field, str):
            out.update(t.strip().upper() for t in field.split(",") if t.strip())
    for pos in row.get("all_positions") or []:
        if isinstance(pos, str):
            out.add(pos.strip().upper())
    return out


def _team(row: dict[str, Any]) -> str | None:
    team = row.get("team")
    if not isinstance(team, str):
        return None
    return team.strip().upper() or None


def _form_factor(row: dict[str, Any], recent: dict[str, Any]) -> float:
    """recent[fantrax_id] = list of slim games. Returns clamped factor or 1.0."""
    fid = row.get("id")
    if not fid:
        return 1.0
    games = recent.get(fid) if isinstance(recent, dict) else None
    if not games:
        return 1.0
    fppg = _num(row.get("fppg"))
    if not fppg or fppg <= 0:
        return 1.0
    last_n = [g for g in games[-RECENT_FORM_GAMES:] if isinstance(g, dict)]
    fpts_values = [_num(g.get("fpts_estimated")) for g in last_n]
    fpts_values = [v for v in fpts_values if v is not None]
    if not fpts_values:
        return 1.0
    recent_avg = sum(fpts_values) / len(fpts_values)
    factor = recent_avg / fppg if fppg > 0 else 1.0
    return max(FORM_FLOOR, min(FORM_CEILING, factor))


def _player_mlb_id_from_probables(
    row: dict[str, Any],
    by_date: dict[str, dict[str, dict[str, Any]]],
    team_abbr: str | None,
) -> int | None:
    """Look up the player's MLB id in the probables index by name + team."""
    name = (row.get("name") or "").lower()
    if not name or not team_abbr:
        return None
    for day in by_date.values():
        slot = (day or {}).get(team_abbr)
        if not isinstance(slot, dict):
            continue
        if (slot.get("name") or "").lower() == name:
            mlb_id = slot.get("mlb_id")
            if isinstance(mlb_id, int):
                return mlb_id
            try:
                return int(mlb_id) if mlb_id is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _confidence(
    *,
    days_remaining: int,
    opp_rows: list[dict[str, Any]],
    probables: dict[str, Any],
    recent: dict[str, Any],
    my_rows: list[dict[str, Any]],
) -> str:
    if not opp_rows:
        return "low"
    has_probables = bool((probables or {}).get("by_date"))
    has_recent = bool(recent)
    if days_remaining >= 5 and not has_probables:
        return "low"
    if has_probables and has_recent:
        return "high"
    if has_probables or has_recent:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Math + date helpers
# ---------------------------------------------------------------------------

def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> _date | None:
    if not value:
        return None
    if isinstance(value, _date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return _date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _days_inclusive(a: _date, b: _date) -> int:
    if a > b:
        return 0
    return (b - a).days + 1


def _date_range(start: _date, end: _date) -> Iterable[_date]:
    if start > end:
        return
    for offset in range((end - start).days + 1):
        yield _date.fromordinal(start.toordinal() + offset)
