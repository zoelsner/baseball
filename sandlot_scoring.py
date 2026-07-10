"""League-exact fantasy scoring for the H2H points league.

Weights transcribed from the Fantrax "League Rules Summary" (Dynasty Baseball
Smoke, 2026). They apply to the normalized per-game rows produced by
``mlb_stats.fetch_game_log`` / ``_normalize_split``.

Differences vs the generic Yahoo-style ``fpts_estimated`` baseline that ships
with mlb_stats: IP pays 3.0 (not 2.25), quality starts pay 3, holds pay 3.5,
losses cost 2, wins pay 2 (not 5), saves pay 4 (not 5), hitter K costs only
0.5, and HBP/CS are scored. Fantrax repeats hitter categories under the
pitching scoring group for two-way players; the lineup runner scores their
hitting and pitching logs separately, then combines the two opportunity models.
"""

from __future__ import annotations

from typing import Any

HITTING = {
    "single": 1.0,
    "double": 2.0,
    "triple": 3.0,
    "hr": 4.0,
    "run": 1.0,
    "rbi": 1.0,
    "bb": 1.0,
    "hbp": 1.0,
    "sb": 1.0,
    "cs": -0.5,
    "so": -0.5,
}

PITCHING = {
    "ip": 3.0,
    "k": 1.0,
    "er": -2.0,
    "hit": -1.0,
    "bb": -1.0,
    "win": 2.0,
    "loss": -2.0,
    "qs": 3.0,
    "save": 4.0,
    "hold": 3.5,
}


def hitting_points(game: dict[str, Any]) -> float:
    h = game.get("h") or 0
    doubles = game.get("doubles") or 0
    triples = game.get("triples") or 0
    hr = game.get("hr") or 0
    singles = max(0, h - doubles - triples - hr)
    return round(
        HITTING["single"] * singles
        + HITTING["double"] * doubles
        + HITTING["triple"] * triples
        + HITTING["hr"] * hr
        + HITTING["run"] * (game.get("r") or 0)
        + HITTING["rbi"] * (game.get("rbi") or 0)
        + HITTING["bb"] * (game.get("bb") or 0)
        + HITTING["hbp"] * (game.get("hbp") or 0)
        + HITTING["sb"] * (game.get("sb") or 0)
        + HITTING["cs"] * (game.get("cs") or 0)
        + HITTING["so"] * (game.get("k") or 0),
        2,
    )


def pitching_points(game: dict[str, Any]) -> float:
    return round(
        PITCHING["ip"] * float(game.get("ip") or 0.0)
        + PITCHING["k"] * (game.get("k") or 0)
        + PITCHING["er"] * (game.get("er") or 0)
        + PITCHING["hit"] * (game.get("h") or 0)
        + PITCHING["bb"] * (game.get("bb") or 0)
        + (PITCHING["win"] if game.get("win") else 0.0)
        + (PITCHING["loss"] if game.get("loss") else 0.0)
        + (PITCHING["qs"] if game.get("qs") else 0.0)
        + (PITCHING["save"] if game.get("save") else 0.0)
        + (PITCHING["hold"] if game.get("hold") else 0.0),
        2,
    )


def game_points(game: dict[str, Any], group: str) -> float:
    """Score one normalized game row for the given stat group."""
    if group == "pitching":
        return pitching_points(game)
    return hitting_points(game)
