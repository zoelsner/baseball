"""Monday lineup optimizer — deterministic core.

Given each rostered player's projected points for the coming scoring week,
propose the exact-optimal assignment to the league's FULL active slot
template (not just the slots the manager happened to fill — empty slots are
free points thrown away).

Projection model, on purpose, is simple and explainable:

    projected = blended per-game rate x expected games next week

  * rate      — recent per-game league-scored average blended with the
                season average (recent form matters, small samples don't).
  * games     — hitters: team's scheduled games x recent playing-time share;
                starters: rotation cadence (starts per day over the last 30)
                projected over 7 days, upgraded by posted probables;
                relievers: appearance cadence over 7 days.

No AI anywhere in this path. Every number in the output can be recomputed by
hand from the game log and the schedule.
"""

from __future__ import annotations

import math
from typing import Any

from sandlot_autopsy import (  # noqa: F401 — shared canonical rules
    INJURED_SLOTS,
    PITCHER_TOKENS,
    PROTECTED_SLOTS,
    _fits,
    _is_pitcher_slot,
    _max_assign,
)

# League config (Dynasty Baseball Smoke): 20 active slots.
FULL_ACTIVE_TEMPLATE = (
    ["C", "1B", "2B", "3B", "SS"]
    + ["OF"] * 3
    + ["UT"] * 3
    + ["SP"] * 6
    + ["RP"] * 3
)

# Injury flags that keep a player out of the proposal entirely.
BLOCKED_INJURIES = {"OUT", "SUSP", "IR", "IL", "IL10", "IL60"}
RECENT_FORM_GAMES = 10


def blended_rate(recent_avg: float, recent_n: int, season_avg: float, season_n: int) -> float:
    """Per-game scoring rate: lean recent, anchored by the season.

    Fewer than 3 recent games is noise — fall back to the season rate.
    """
    if recent_n >= 3 and season_n >= 3:
        return 0.55 * recent_avg + 0.45 * season_avg
    if season_n:
        return season_avg
    return recent_avg if recent_n else 0.0


def expected_games(
    tokens: set[str],
    *,
    team_games_next: int,
    team_games_recent: int,
    games_recent: int,
    starts_recent: int,
    probable_starts: int,
) -> float:
    """Expected scoring appearances next week.

    ``*_recent`` counts cover the same trailing window (default 30 days in
    the runner); ``probable_starts`` is how many times the player is a posted
    probable inside the coming week.
    """
    is_pure_pitcher = bool(tokens & PITCHER_TOKENS) and not (tokens - PITCHER_TOKENS)
    if is_pure_pitcher:
        starter_usage = starts_recent > 0 and starts_recent * 2 >= games_recent
        if starter_usage:
            # Rotation cadence: a healthy every-5th-day starter shows ~6
            # starts over 30 days -> ~1.4 expected. Posted probables are
            # ground truth when MLB has published them.
            est = starts_recent / 30.0 * 7.0
            return max(est, float(probable_starts))
        return games_recent / 30.0 * 7.0
    # Hitters (and two-way players, who overwhelmingly earn as hitters):
    # team schedule x playing-time share.
    share = min(1.0, games_recent / team_games_recent) if team_games_recent else 0.0
    return team_games_next * share


def project_week(
    tokens: set[str],
    *,
    hitting_season_points: list[float],
    hitting_recent_points: list[float],
    pitching_season_points: list[float],
    pitching_recent_points: list[float],
    team_games_next: int,
    team_games_recent: int,
    starts_recent: int,
    probable_starts: int,
) -> dict[str, Any]:
    """Project hitting and pitching opportunities independently.

    Two-way players cannot use one merged per-game rate: hitting follows the
    team schedule, while pitching follows starts/appearance cadence.
    """
    components: list[dict[str, Any]] = []

    def add_component(
        group: str,
        season: list[float],
        recent: list[float],
        expected: float,
        unit: str,
    ) -> None:
        recent_form = recent[-RECENT_FORM_GAMES:]
        recent_avg = sum(recent_form) / len(recent_form) if recent_form else 0.0
        season_avg = sum(season) / len(season) if season else 0.0
        rate = blended_rate(recent_avg, len(recent_form), season_avg, len(season))
        components.append({
            "group": group,
            "rate": rate,
            "expected": expected,
            "unit": unit,
            "points": rate * expected,
            "season_n": len(season),
            "recent_n": len(recent_form),
        })

    hitter_tokens = tokens - PITCHER_TOKENS
    pitcher_tokens = tokens & PITCHER_TOKENS
    if hitter_tokens:
        hitter_expected = expected_games(
            hitter_tokens,
            team_games_next=team_games_next,
            team_games_recent=team_games_recent,
            games_recent=len(hitting_recent_points),
            starts_recent=0,
            probable_starts=0,
        )
        add_component("hitting", hitting_season_points, hitting_recent_points, hitter_expected, "games")
    if pitcher_tokens:
        pitcher_expected = expected_games(
            pitcher_tokens,
            team_games_next=team_games_next,
            team_games_recent=team_games_recent,
            games_recent=len(pitching_recent_points),
            starts_recent=starts_recent,
            probable_starts=probable_starts,
        )
        starter_usage = starts_recent > 0 and starts_recent * 2 >= len(pitching_recent_points)
        add_component(
            "pitching",
            pitching_season_points,
            pitching_recent_points,
            pitcher_expected,
            "starts" if starter_usage else "outings",
        )

    return {
        "projected_total": round(sum(component["points"] for component in components), 1),
        "components": components,
    }


def propose(entries: list[dict[str, Any]], template: list[str] | None = None) -> dict[str, Any]:
    """Exact-optimal lineup from projected entries.

    ``entries``: [{"name", "tokens", "proj", ...}] — already filtered to
    players who can legally start (no IL/OUT/SUSP).
    Returns {"lineup": [(slot, name)], "projected_total", "unfilled": [slot]}.
    """
    template = list(template or FULL_ACTIVE_TEMPLATE)
    hit_slots = [s for s in template if not _is_pitcher_slot(s)]
    pit_slots = [s for s in template if _is_pitcher_slot(s)]

    hit_only, pit_only, two_way = [], [], []
    for e in entries:
        slot = str(e.get("slot") or "").strip().upper()
        injury = str(e.get("injury") or "").strip().upper()
        try:
            projected = float(e.get("proj"))
        except (TypeError, ValueError):
            continue
        if (
            not math.isfinite(projected)
            or slot in INJURED_SLOTS
            or slot in PROTECTED_SLOTS
            or injury in BLOCKED_INJURIES
        ):
            continue
        tokens = e["tokens"]
        can_hit = bool(tokens - PITCHER_TOKENS)
        can_pit = bool(tokens & PITCHER_TOKENS)
        if can_hit and can_pit:
            hitter_value = _side_projection(e, "hitter", projected)
            pitcher_value = _side_projection(e, "pitcher", projected)
            two_way.append(
                (
                    (hitter_value, e["name"], tokens),
                    (pitcher_value, e["name"], tokens),
                )
            )
        elif can_pit:
            pit_only.append((projected, e["name"], tokens))
        elif can_hit:
            hit_only.append((projected, e["name"], tokens))

    best_total, best_asg = float("-inf"), []
    for combo in range(1 << len(two_way)):
        hside, pside = list(hit_only), list(pit_only)
        for i, (hitter_item, pitcher_item) in enumerate(two_way):
            (hside if combo & (1 << i) else pside).append(
                hitter_item if combo & (1 << i) else pitcher_item
            )
        hv, ha = _max_assign(hit_slots, hside)
        pv, pa = _max_assign(pit_slots, pside)
        if hv + pv > best_total:
            best_total, best_asg = hv + pv, ha + pa
    if best_total == float("-inf"):
        best_total, best_asg = 0.0, []

    filled = list(best_asg)
    used = {}
    for slot, _ in filled:
        used[slot] = used.get(slot, 0) + 1
    unfilled = []
    want = {}
    for slot in template:
        want[slot] = want.get(slot, 0) + 1
    for slot, n in want.items():
        for _ in range(n - used.get(slot, 0)):
            unfilled.append(slot)

    return {
        "lineup": filled,
        "projected_total": round(best_total, 1),
        "unfilled": unfilled,
    }


def _side_projection(entry: dict[str, Any], side: str, fallback: float) -> float:
    field = "hitter_proj" if side == "hitter" else "pitcher_proj"
    try:
        value = float(entry.get(field))
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) else fallback


def projected_for_slot(entry: dict[str, Any], slot: str) -> float:
    """Return the scoring value Fantrax awards from this assigned slot group."""
    try:
        fallback = float(entry.get("proj") or 0.0)
    except (TypeError, ValueError):
        fallback = 0.0
    side = "pitcher" if _is_pitcher_slot(slot) else "hitter"
    return _side_projection(entry, side, fallback)
