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

import sandlot_data_quality
import sandlot_db
import sandlot_skipper

log = logging.getLogger(__name__)

BRIEF_TYPE_GRADE = "trade_grade"
BRIEF_TYPE_COUNTER = "trade_counter"
MIN_COUNTER_ADD_FPPG = 0.75

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

COUNTER_SYSTEM_PROMPT = """You explain deterministic fantasy baseball counter offers.

Rules:
- Use only the supplied JSON. Do not invent players, reorder counters, change tiers, or estimate odds.
- Do not output percentages or calibrated acceptance probabilities.
- Output compact JSON only: [{"tier":"strong","rationale":"..."}, ...].
- Rationale must be one sentence under 26 words and mention why this counter fits the roster shape."""


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
    data_quality = sandlot_data_quality.snapshot_data_quality(data)

    give_players, missing_give = _resolve_players(data, give_ids)
    get_players, missing_get = _resolve_players(data, get_ids)
    missing = missing_give + missing_get
    if missing:
        raise TradeGradeError(
            "player(s) not found in snapshot: " + ", ".join(missing)
        )

    deltas = _compute_deltas(give_players, get_players)
    counter_result = _build_counter_result(
        snapshot_row=snapshot_row,
        data_quality=data_quality,
        give_ids=give_ids,
        get_ids=get_ids,
        give_players=give_players,
        get_players=get_players,
        deltas=deltas,
    )
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
        "grade": deltas["letter_grade"],
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
        "counters": counter_result["counters"],
        "my_weakest_position": counter_result["my_weakest_position"],
        "no_counter_reason": counter_result["no_counter_reason"],
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


def _build_counter_result(
    *,
    snapshot_row: dict[str, Any],
    data_quality: dict[str, Any],
    give_ids: list[str],
    get_ids: list[str],
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    deltas: dict[str, Any],
) -> dict[str, Any]:
    data = snapshot_row.get("data") or {}
    my_rows = (data.get("roster") or {}).get("rows") or []
    my_weak_positions = _weak_positions(my_rows)
    my_weakest_position = my_weak_positions[0] if my_weak_positions else None

    if not data_quality.get("recommendations_ready"):
        return _counter_result(
            my_weakest_position,
            "Counter guidance paused: "
            + sandlot_data_quality.short_reason(data_quality, purpose="recommendations")
            + ".",
        )
    if deltas["my_delta"] >= 2.0:
        return _counter_result(my_weakest_position, "Offer already grades strong; no counter needed.")

    counterparty = _counterparty_team(data, get_players)
    if not counterparty:
        return _counter_result(
            my_weakest_position,
            "Counter guidance needs the get side to come from one roster.",
        )

    team_id, team = counterparty
    opponent_rows = [row for row in (team.get("rows") or []) if isinstance(row, dict)]
    opponent_weak_positions = _weak_positions(opponent_rows)
    candidates = _counter_candidates(
        give_players=give_players,
        get_players=get_players,
        opponent_rows=opponent_rows,
        my_weak_positions=my_weak_positions,
        opponent_weak_positions=opponent_weak_positions,
        deltas=deltas,
    )
    picked = _pick_counter_tiers(candidates)
    if not picked:
        return _counter_result(
            my_weakest_position,
            "No deterministic counter clears a meaningful improvement threshold.",
        )

    counters = [
        _counter_card(
            tier=tier,
            candidate=candidate,
            give_players=give_players,
            get_players=get_players,
            my_weakest_position=my_weakest_position,
            opponent_need=candidate.get("opponent_need"),
        )
        for tier, candidate in picked
    ]
    _overlay_counter_rationales(
        snapshot_id=int(snapshot_row.get("id") or 0),
        give_ids=give_ids,
        get_ids=get_ids,
        counters=counters,
        team_id=str(team_id),
    )
    return {"counters": counters, "my_weakest_position": my_weakest_position, "no_counter_reason": None}


def _counter_result(my_weakest_position: str | None, reason: str) -> dict[str, Any]:
    return {"counters": [], "my_weakest_position": my_weakest_position, "no_counter_reason": reason}


def _counterparty_team(data: dict[str, Any], get_players: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    get_ids = {str(p.get("id")) for p in get_players if p.get("id")}
    if not get_ids:
        return None
    matches: list[tuple[str, dict[str, Any]]] = []
    for tid, team in (data.get("all_team_rosters") or {}).items():
        if not isinstance(team, dict):
            continue
        team_ids = {str(row.get("id")) for row in (team.get("rows") or []) if isinstance(row, dict) and row.get("id")}
        if get_ids <= team_ids:
            matches.append((str(team.get("team_id") or tid), team))
    if len(matches) != 1:
        return None
    return matches[0]


def _counter_candidates(
    *,
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    opponent_rows: list[dict[str, Any]],
    my_weak_positions: list[str],
    opponent_weak_positions: list[str],
    deltas: dict[str, Any],
) -> list[dict[str, Any]]:
    base_get_ids = {str(p.get("id")) for p in get_players if p.get("id")}
    give_positions = set().union(*(_position_tokens(p) for p in give_players)) if give_players else set()
    opponent_need = sorted(give_positions & set(opponent_weak_positions))
    candidates: list[dict[str, Any]] = []
    for row in opponent_rows:
        if str(row.get("id")) in base_get_ids:
            continue
        fppg = _fppg(row)
        if fppg < MIN_COUNTER_ADD_FPPG or _is_unavailable(row):
            continue
        tokens = _position_tokens(row)
        weak_fit = sorted(tokens & set(my_weak_positions))
        weakest_fit = 1.4 if my_weak_positions and my_weak_positions[0] in tokens else 0.0
        fit_score = weakest_fit + (0.6 if weak_fit else 0.0)
        other_need_score = 0.6 if opponent_need else 0.0
        score = round(fppg + fit_score + other_need_score, 3)
        counter_get_fppg = round(deltas["my_get_fppg"] + fppg, 2)
        counter_delta = round(counter_get_fppg - deltas["my_give_fppg"], 2)
        candidates.append({
            "row": row,
            "fppg": fppg,
            "score": score,
            "weak_fit": weak_fit,
            "opponent_need": opponent_need,
            "counter_delta": counter_delta,
        })
    return sorted(candidates, key=lambda c: (c["score"], c["fppg"], str((c["row"] or {}).get("name") or "")), reverse=True)


def _pick_counter_tiers(candidates: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    if not candidates:
        return []
    picked: list[tuple[str, dict[str, Any]]] = []
    used: set[str] = set()

    def add(tier: str, candidate: dict[str, Any] | None) -> None:
        if not candidate:
            return
        pid = str((candidate.get("row") or {}).get("id") or "")
        if not pid or pid in used:
            return
        used.add(pid)
        picked.append((tier, candidate))

    add("strong", candidates[0])
    remaining = [c for c in candidates if str((c.get("row") or {}).get("id") or "") not in used]
    add("light", min(remaining, key=lambda c: (c["fppg"], -c["score"]), default=None))
    remaining = [c for c in candidates if str((c.get("row") or {}).get("id") or "") not in used]
    add("balanced", min(remaining, key=lambda c: (abs(c["counter_delta"] - 1.0), -c["score"]), default=None))

    order = {"strong": 0, "balanced": 1, "light": 2}
    return sorted(picked, key=lambda item: order[item[0]])


def _counter_card(
    *,
    tier: str,
    candidate: dict[str, Any],
    give_players: list[dict[str, Any]],
    get_players: list[dict[str, Any]],
    my_weakest_position: str | None,
    opponent_need: list[str],
) -> dict[str, Any]:
    row = candidate["row"]
    give = [_slim_player(p) for p in give_players]
    get = [_slim_player(p) for p in [*get_players, row]]
    counter_delta = candidate["counter_delta"]
    band = {"strong": "hard", "balanced": "balanced", "light": "easy"}[tier]
    return {
        "tier": tier,
        "give": give,
        "get": get,
        "counter_strength": tier,
        "acceptance_band": band,
        "my_delta": counter_delta,
        "their_delta": round(-counter_delta, 2),
        "added_player": _slim_player(row),
        "rationale": _counter_rationale(
            tier=tier,
            candidate=row,
            my_weakest_position=my_weakest_position,
            weak_fit=candidate.get("weak_fit") or [],
            opponent_need=opponent_need,
        ),
    }


def _counter_rationale(
    *,
    tier: str,
    candidate: dict[str, Any],
    my_weakest_position: str | None,
    weak_fit: list[str],
    opponent_need: list[str],
) -> str:
    name = candidate.get("name") or "the add-on"
    fppg = _fppg(candidate)
    fit = weak_fit[0] if weak_fit else my_weakest_position
    if fit:
        roster_text = f"adds {fit} help"
    else:
        roster_text = "adds usable weekly points"
    if opponent_need:
        need_text = f" while your give side fits their {opponent_need[0]} need"
    else:
        need_text = ""
    return f"{tier.title()} counter {roster_text} with {name} at {fppg:.1f} FP/G{need_text}."


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


def _weak_positions(rows: list[dict[str, Any]], limit: int = 3) -> list[str]:
    by_pos: dict[str, list[float]] = {}
    for row in rows or []:
        if not isinstance(row, dict) or _is_inactive(row):
            continue
        fppg = _fppg(row)
        if fppg <= 0:
            continue
        for pos in _position_tokens(row):
            by_pos.setdefault(pos, []).append(fppg)
    averages = [
        (pos, sum(values) / len(values))
        for pos, values in by_pos.items()
        if values
    ]
    averages.sort(key=lambda item: (item[1], item[0]))
    return [pos for pos, _avg in averages[:limit]]


def _position_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("all_positions", "positions", "multi_positions", "pos", "slot"):
        value = row.get(key)
        values = value if isinstance(value, list) else str(value or "").replace("/", ",").split(",")
        for raw in values:
            token = str(raw or "").strip().upper()
            if token and token not in {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "HIT", "PIT", "ALL", "UTIL"}:
                tokens.add(token)
    return tokens


def _is_inactive(row: dict[str, Any]) -> bool:
    slot = str(row.get("slot") or "").strip().upper()
    return slot in {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "INJ", "INJ RES", "MINORS"}


def _is_unavailable(row: dict[str, Any]) -> bool:
    status = str(row.get("injury") or row.get("status") or "").strip().upper()
    return status in {"OUT", "IL", "IL10", "IL60", "IR"}


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


def _overlay_counter_rationales(
    *,
    snapshot_id: int,
    give_ids: list[str],
    get_ids: list[str],
    counters: list[dict[str, Any]],
    team_id: str,
) -> None:
    if not snapshot_id or not counters:
        return
    subject_payload = {
        "give": sorted(str(pid) for pid in give_ids),
        "get": sorted(str(pid) for pid in get_ids),
        "counters": [
            {
                "tier": c.get("tier"),
                "give": [p.get("id") for p in c.get("give") or []],
                "get": [p.get("id") for p in c.get("get") or []],
            }
            for c in counters
        ],
    }
    subject_key = _hash_context(subject_payload)
    context = {
        "counterparty_team_id": team_id,
        "counters": [
            {
                "tier": c.get("tier"),
                "counter_strength": c.get("counter_strength"),
                "acceptance_band": c.get("acceptance_band"),
                "give": c.get("give"),
                "get": c.get("get"),
                "my_delta": c.get("my_delta"),
                "deterministic_rationale": c.get("rationale"),
            }
            for c in counters
        ],
    }
    input_hash = _hash_context(context)

    cached = None
    try:
        cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_COUNTER, subject_key)
    except Exception as exc:
        log.warning("Trade counter cache read failed for %s/%s: %s", snapshot_id, subject_key, exc)

    parsed: dict[str, str] = {}
    if cached and cached.get("input_hash") == input_hash and cached.get("text"):
        parsed = _parse_counter_rationales(str(cached.get("text") or ""))
    else:
        try:
            text, model = sandlot_skipper.SkipperClient().complete(
                _counter_messages(context),
                max_tokens=260,
                model_order=(sandlot_skipper.fallback_model(), sandlot_skipper.primary_model()),
            )
            parsed = _parse_counter_rationales(text)
            if parsed:
                sandlot_db.set_ai_brief(
                    snapshot_id,
                    BRIEF_TYPE_COUNTER,
                    subject_key,
                    json.dumps(parsed, ensure_ascii=True, sort_keys=True),
                    model,
                    input_hash,
                )
        except Exception as exc:
            log.warning("Trade counter AI failed for %s/%s: %s", snapshot_id, subject_key, exc)

    for counter in counters:
        rationale = parsed.get(str(counter.get("tier")))
        if rationale:
            counter["rationale"] = rationale


def _counter_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": COUNTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Explain these already-ranked deterministic trade counters:\n"
            + json.dumps(context, default=str, indent=2),
        },
    ]


def _parse_counter_rationales(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except Exception:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except Exception:
                parsed = None
    out: dict[str, str] = {}
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            tier = str(item.get("tier") or "").strip().lower()
            rationale = str(item.get("rationale") or "").strip()
            if tier in {"strong", "balanced", "light"} and rationale and "%" not in rationale:
                out[tier] = rationale[:500]
    elif isinstance(parsed, dict):
        for tier in ("strong", "balanced", "light"):
            rationale = str(parsed.get(tier) or "").strip()
            if rationale and "%" not in rationale:
                out[tier] = rationale[:500]
    return out


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
