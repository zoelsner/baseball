"""Deterministic trade grader plus cached AI rationale.

V0.1 of the Trade tab. Mirrors the `sandlot_waivers.py` shape:

- Deterministic step computes weekly Δ for each side, fairness, letter grade,
  and age delta from snapshot FP/G data.
- AI step explains the already-computed numbers via SkipperClient.complete().
- Result cached in `ai_briefs` keyed by (snapshot_id, "trade_grade", subject_key)
  with an input_hash so cache busts when relevant FP/G shifts.

V0.2 (counters + acceptance odds) and V0.3 (chat hookup) are deliberately
out of scope; see issue #3.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import sandlot_db
import sandlot_skipper

log = logging.getLogger(__name__)

BRIEF_TYPE_GRADE = "trade_grade"

# Letter-grade thresholds applied to my_delta (sum of get FP/G - sum of give FP/G).
# Tuneable. The deterministic step always emits a grade; the AI just explains it.
_GRADE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (4.0, "A+"),
    (2.0, "A"),
    (1.0, "A−"),
    (0.5, "B+"),
    (0.0, "B"),
    (-0.5, "B−"),
    (-1.0, "C"),
    (-2.0, "D"),
)
_GRADE_FLOOR = "F"

GRADE_SYSTEM_PROMPT = """You explain a deterministic fantasy baseball trade grade.

Rules:
- Use only the supplied JSON. Do not invent injuries, news, lineups, or stats.
- Do not change the letter grade, deltas, or fairness — only explain them.
- Do not recommend Fantrax write actions (accept, decline, counter, send).
- Output ONE sentence under 32 words. No markdown, no bullets, no preamble.
- Cite at least one player name and at least one supplied number."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TradeGradeError(Exception):
    """Raised for caller-visible problems (missing players, empty side, etc)."""


def grade_offer(
    snapshot_row: dict[str, Any],
    give_ids: list[str],
    get_ids: list[str],
) -> dict[str, Any]:
    """Grade a two-party offer against a stored Fantrax snapshot.

    Returns a payload suitable for the API. Raises TradeGradeError for
    caller-fixable issues (so the route can map to a 400).
    """
    if not give_ids or not get_ids:
        raise TradeGradeError("trade must have at least one player on each side")

    snapshot_id = int(snapshot_row.get("id") or 0)
    if not snapshot_id:
        raise TradeGradeError("snapshot row is missing an id")
    data = snapshot_row.get("data") or {}

    give_players, missing_give = _resolve_players(data, give_ids)
    get_players, missing_get = _resolve_players(data, get_ids)
    missing = missing_give + missing_get
    if missing:
        raise TradeGradeError(
            "player(s) not found in snapshot: " + ", ".join(missing)
        )

    deltas = _compute_deltas(give_players, get_players)
    subject_key = _subject_key(give_ids, get_ids)
    context = _grade_prompt_context(snapshot_row, give_players, get_players, deltas)
    input_hash = _hash_context(context)

    rationale, model, cached = _load_or_generate_rationale(
        snapshot_id=snapshot_id,
        subject_key=subject_key,
        input_hash=input_hash,
        context=context,
    )

    return {
        "snapshot_id": snapshot_id,
        "letter_grade": deltas["letter_grade"],
        "headline": _headline(deltas),
        "fairness": deltas["fairness"],
        "my_delta": deltas["my_delta"],
        "their_delta": deltas["their_delta"],
        "age_delta": deltas["age_delta"],
        "my_give_fppg": deltas["my_give_fppg"],
        "my_get_fppg": deltas["my_get_fppg"],
        "my_give": [_slim_player(p) for p in give_players],
        "my_get": [_slim_player(p) for p in get_players],
        "rationale": rationale,
        "model": model,
        "cached": cached,
    }


# ---------------------------------------------------------------------------
# Player resolution — mirrors sandlot_waivers.py iteration over snapshot rosters
# ---------------------------------------------------------------------------

def _resolve_players(
    data: dict[str, Any],
    ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (resolved_rows, missing_ids) preserving input order.

    Looks across my roster, every other team's roster, and free agents.
    Free-agent matches are kept (issue #3 marks free-agent trades as a
    non-goal, but rejecting them here would be a 400 the route can handle
    later if we tighten the rule).
    """
    pool: dict[str, dict[str, Any]] = {}

    def absorb(row: dict[str, Any] | None) -> None:
        if not isinstance(row, dict):
            return
        pid = row.get("id")
        if not pid or pid in pool:
            return
        pool[str(pid)] = row

    for row in (data.get("roster") or {}).get("rows") or []:
        absorb(row)
    for team in (data.get("all_team_rosters") or {}).values():
        for row in (team or {}).get("rows") or []:
            absorb(row)
    for row in (data.get("free_agents") or {}).get("players") or []:
        absorb(row)

    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for pid in ids:
        row = pool.get(str(pid))
        if row is None:
            missing.append(str(pid))
        else:
            resolved.append(row)
    return resolved, missing


# ---------------------------------------------------------------------------
# Deterministic math
# ---------------------------------------------------------------------------

def _compute_deltas(
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
) -> dict[str, Any]:
    my_give_fppg = round(sum(_fppg(p) for p in give_players), 2)
    my_get_fppg = round(sum(_fppg(p) for p in get_players), 2)
    my_delta = round(my_get_fppg - my_give_fppg, 2)
    their_delta = round(-my_delta, 2)

    denom = max(my_give_fppg, my_get_fppg, 1.0)
    fairness = max(0.0, min(1.0, 1.0 - abs(my_delta) / denom))
    fairness = round(fairness, 3)

    age_delta = _age_delta(give_players, get_players)
    letter_grade = _letter_grade(my_delta)

    return {
        "my_give_fppg": my_give_fppg,
        "my_get_fppg": my_get_fppg,
        "my_delta": my_delta,
        "their_delta": their_delta,
        "fairness": fairness,
        "age_delta": age_delta,
        "letter_grade": letter_grade,
    }


def _letter_grade(my_delta: float) -> str:
    for threshold, grade in _GRADE_THRESHOLDS:
        if my_delta >= threshold:
            return grade
    return _GRADE_FLOOR


def _headline(deltas: dict[str, Any]) -> str:
    grade = deltas["letter_grade"]
    my_delta = deltas["my_delta"]
    age_delta = deltas["age_delta"]
    if grade in ("A+", "A", "A−"):
        verdict = "Take it"
    elif grade in ("B+", "B", "B−"):
        verdict = "Lean accept"
    elif grade == "C":
        verdict = "Push back"
    else:
        verdict = "Decline"
    if age_delta is not None and age_delta <= -1:
        flavor = "you get younger"
    elif age_delta is not None and age_delta >= 1:
        flavor = "you get older"
    elif my_delta >= 0.5:
        flavor = "weekly edge"
    elif my_delta <= -0.5:
        flavor = "you lose weekly points"
    else:
        flavor = "near even"
    return f"{verdict} · {flavor}"


def _age_delta(
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
) -> float | None:
    give_ages = [a for a in (_age(p) for p in give_players) if a is not None]
    get_ages = [a for a in (_age(p) for p in get_players) if a is not None]
    if not give_ages or not get_ages:
        return None
    return round(sum(get_ages) / len(get_ages) - sum(give_ages) / len(give_ages), 1)


def _fppg(row: dict[str, Any]) -> float:
    return _number(row.get("fppg")) or 0.0


def _age(row: dict[str, Any]) -> float | None:
    return _number(row.get("age"))


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _slim_player(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "slot": row.get("slot"),
        "positions": row.get("positions"),
        "team": row.get("team"),
        "fppg": _number(row.get("fppg")),
        "age": _number(row.get("age")),
        "injury": row.get("injury"),
    }


# ---------------------------------------------------------------------------
# AI rationale — cached
# ---------------------------------------------------------------------------

def _subject_key(give_ids: list[str], get_ids: list[str]) -> str:
    g = ",".join(sorted(str(i) for i in give_ids))
    r = ",".join(sorted(str(i) for i in get_ids))
    return f"give:{g}|get:{r}"


def _grade_prompt_context(
    snapshot_row: dict[str, Any],
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    deltas: dict[str, Any],
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_row.get("id"),
        "taken_at": snapshot_row.get("taken_at"),
        "my_give": [_slim_player(p) for p in give_players],
        "my_get": [_slim_player(p) for p in get_players],
        "letter_grade": deltas["letter_grade"],
        "my_delta": deltas["my_delta"],
        "their_delta": deltas["their_delta"],
        "fairness": deltas["fairness"],
        "age_delta": deltas["age_delta"],
        "my_give_fppg": deltas["my_give_fppg"],
        "my_get_fppg": deltas["my_get_fppg"],
    }


def _grade_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": GRADE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Explain this deterministic trade grade:\n"
            + json.dumps(context, default=str, indent=2),
        },
    ]


def _hash_context(context: dict[str, Any]) -> str:
    body = json.dumps(context, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_or_generate_rationale(
    *,
    snapshot_id: int,
    subject_key: str,
    input_hash: str,
    context: dict[str, Any],
) -> tuple[str, str, bool]:
    """Returns (text, model, cached). Falls back to deterministic on AI errors."""
    cached = None
    try:
        cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_GRADE, subject_key)
    except Exception as exc:
        log.warning("Trade grade cache read failed for %s/%s: %s",
                    snapshot_id, subject_key, exc)
    if cached and cached.get("input_hash") == input_hash and cached.get("text"):
        return str(cached["text"]).strip(), str(cached.get("model") or ""), True

    # Tencent-first matches the player-take pattern: faster on cold, short
    # prompts. Kimi remains the fallback if Tencent errors.
    model_order = (sandlot_skipper.fallback_model(), sandlot_skipper.primary_model())
    try:
        text, model = sandlot_skipper.SkipperClient().complete(
            _grade_messages(context),
            max_tokens=160,
            model_order=model_order,
        )
        text = (text or "").strip()
        if not text:
            raise RuntimeError("empty AI response")
    except Exception as exc:
        log.warning("Trade grade AI failed for %s/%s: %s",
                    snapshot_id, subject_key, exc)
        return _fallback_rationale(context), "", False

    try:
        sandlot_db.set_ai_brief(
            snapshot_id, BRIEF_TYPE_GRADE, subject_key, text, model, input_hash
        )
    except Exception as exc:
        log.warning("Trade grade cache write failed for %s/%s: %s",
                    snapshot_id, subject_key, exc)
    return text, model, False


def _fallback_rationale(context: dict[str, Any]) -> str:
    """Deterministic prose for when AI is unavailable. Single sentence."""
    give = ", ".join(p.get("name") or "" for p in context.get("my_give") or []) or "the give side"
    get = ", ".join(p.get("name") or "" for p in context.get("my_get") or []) or "the get side"
    my_delta = context.get("my_delta", 0.0)
    sign = "+" if my_delta >= 0 else ""
    return f"You give {give} and get {get} for a net {sign}{my_delta} weekly FP/G."
