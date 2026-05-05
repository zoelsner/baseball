"""Deterministic waiver-swap board plus cached AI explanations.

The API path is intentionally cache-first: it derives ranked waiver cards from
the latest Fantrax snapshot and overlays cached AI text if present. OpenRouter
is only used by the warmup path after refresh/cron.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import sandlot_db
import sandlot_skipper

log = logging.getLogger(__name__)

TRUE_FPG_KEYS = ("FP/G", "FPG", "FPts/G", "FP/Gm", "Avg")
FALLBACK_SCORE_KEYS = ("Score", "FPts", "ProjFPts", "FP")

BRIEF_TYPE_SWAP = "waiver_swap"
BRIEF_TYPE_REFRESH = "refresh_brief"
REFRESH_SUBJECT = "latest"

CARD_LIMIT = 8
WEAK_POSITION_COUNT = 3

POSITION_ALIASES = {
    "LF": "OF",
    "CF": "OF",
    "RF": "OF",
    "MI": "SS",
    "CI": "3B",
}
POSITION_TOKENS = {"C", "1B", "2B", "3B", "SS", "OF", "UT", "SP", "RP", "P"}
GENERIC_POSITIONS = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "HIT", "PIT", "ALL", "UTIL"}
PITCHER_POSITIONS = {"SP", "RP", "P"}
HITTER_POSITIONS = {"C", "1B", "2B", "3B", "SS", "OF", "UT"}
STATUS_ISSUE_TOKENS = ("DTD", "OUT", "IL", "IL10", "IL60", "IR", "SUSP", "NA", "D/L")

WAIVER_SWAP_SYSTEM_PROMPT = """You explain one deterministic fantasy baseball waiver swap.

Rules:
- Use only the supplied JSON. Do not invent news, clips, injuries, lineups, or stats.
- Do not rank, reorder, choose different players, or change net delta/confidence.
- Do not imply Sandlot can apply, execute, add, drop, or submit the move.
- Output JSON only: {"why":"one sentence","risk":"one sentence"}.
- Mention only the add player and move-out player in the JSON.
- Keep each sentence under 28 words."""

REFRESH_BRIEF_SYSTEM_PROMPT = """You write a cached Skipper refresh brief from deterministic waiver data.

Rules:
- Use only the supplied JSON. Do not invent news, clips, lineups, injuries, or stats.
- Do not change rankings, net deltas, or confidence.
- Write 3 to 5 short bullets in plain English.
- Cite only supplied player names and supplied deterministic findings.
- No markdown title."""


def latest_waiver_payload(row: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot_row = row or sandlot_db.latest_successful_snapshot()
    if not snapshot_row:
        raise LookupError("No successful Fantrax snapshot has been stored yet")
    return payload_for_snapshot(snapshot_row, overlay_cached_ai=True)


def payload_for_snapshot(
    snapshot_row: dict[str, Any],
    *,
    overlay_cached_ai: bool = True,
    limit: int = CARD_LIMIT,
) -> dict[str, Any]:
    snapshot_id = int(snapshot_row.get("id") or 0)
    data = snapshot_row.get("data") or {}
    taken_at = snapshot_row.get("taken_at")
    roster_rows = (data.get("roster") or {}).get("rows") or []
    fa_players = (data.get("free_agents") or {}).get("players") or []

    message = None
    diagnostics: dict[str, Any] = {
        "weak_positions": [],
        "free_agent_count": len(fa_players),
        "move_out_count": 0,
    }
    if not fa_players:
        cards: list[dict[str, Any]] = []
        message = "Free-agent pool is missing from the latest Fantrax snapshot."
    else:
        cards, diagnostics = build_waiver_cards(
            roster_rows=roster_rows,
            fa_players=fa_players,
            snapshot_id=snapshot_id,
            limit=limit,
        )
        if not cards:
            message = "No positive waiver swaps found from the latest snapshot."

    if overlay_cached_ai and snapshot_id:
        _overlay_cached_swap_explanations(snapshot_id, cards)
        brief = _cached_refresh_brief(snapshot_id)
    else:
        brief = {"state": "missing", "text": None, "model": None, "generated_at": None}

    return {
        "snapshot_id": snapshot_id,
        "taken_at": taken_at,
        "freshness": _freshness(taken_at),
        "cards": cards,
        "brief": brief,
        "message": message,
        "diagnostics": diagnostics,
    }


def build_waiver_cards(
    *,
    roster_rows: list[dict[str, Any]],
    fa_players: list[dict[str, Any]],
    snapshot_id: int,
    limit: int = CARD_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    weak_positions = _weak_positions(roster_rows)
    move_candidates = _move_out_candidates(roster_rows, weak_positions)
    add_candidates = [_add_candidate(p) for p in fa_players]
    add_candidates = [p for p in add_candidates if p and p["fpg"] > 0]

    candidates: list[dict[str, Any]] = []
    for add in add_candidates:
        for move in move_candidates:
            card = _pair_card(snapshot_id, add, move, weak_positions)
            if card:
                candidates.append(card)

    candidates.sort(
        key=lambda c: (
            -float(c.get("sort_score") or 0),
            -float(c.get("net_delta") or 0),
            str((c.get("add") or {}).get("name") or ""),
            str((c.get("move_out") or {}).get("name") or ""),
        )
    )

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    used_adds: set[str] = set()
    used_moves: set[str] = set()

    for card in candidates:
        add_id = str((card.get("add") or {}).get("id") or "")
        move_id = str((card.get("move_out") or {}).get("id") or "")
        if add_id in used_adds or move_id in used_moves:
            continue
        selected.append(card)
        selected_ids.add(str(card["id"]))
        used_adds.add(add_id)
        used_moves.add(move_id)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for card in candidates:
            cid = str(card["id"])
            if cid in selected_ids:
                continue
            selected.append(card)
            selected_ids.add(cid)
            if len(selected) >= limit:
                break

    for i, card in enumerate(selected, start=1):
        card["rank"] = i

    diagnostics = {
        "weak_positions": weak_positions,
        "free_agent_count": len(fa_players),
        "usable_add_count": len(add_candidates),
        "move_out_count": len(move_candidates),
        "candidate_count": len(candidates),
    }
    return selected, diagnostics


def warm_latest_waiver_ai(snapshot_id: int | None = None, limit: int = CARD_LIMIT) -> dict[str, Any]:
    """Best-effort OpenRouter warmer for waiver explanations and refresh brief."""
    try:
        sandlot_db.init_schema()
        row = sandlot_db.snapshot_by_id(snapshot_id) if snapshot_id is not None else None
        if row is None:
            row = sandlot_db.latest_successful_snapshot()
        if not row:
            return {"attempted": 0, "generated": 0, "skipped": 0, "errors": ["no snapshot available"]}

        payload = payload_for_snapshot(row, overlay_cached_ai=False, limit=limit)
        cards = payload.get("cards") or []
        if not cards:
            return {"attempted": 0, "generated": 0, "skipped": 0, "errors": [payload.get("message") or "no cards"]}

        if not os.environ.get("OPENROUTER_API_KEY"):
            return {"attempted": len(cards), "generated": 0, "skipped": len(cards), "errors": ["OPENROUTER_API_KEY is not set"]}

        client = sandlot_skipper.SkipperClient()
        sid = int(row.get("id") or 0)
        generated = 0
        skipped = 0
        errors: list[str] = []

        for card in cards[:limit]:
            subject_key = str(card["id"])
            if sandlot_db.get_ai_brief(sid, BRIEF_TYPE_SWAP, subject_key):
                skipped += 1
                continue
            context = _swap_prompt_context(row, card)
            input_hash = _hash_context(context)
            try:
                raw, model = client.complete(
                    _swap_messages(context),
                    max_tokens=130,
                    model_order=sandlot_skipper.default_model_order(),
                )
                parsed = _parse_swap_ai(raw)
                text = json.dumps(parsed, ensure_ascii=True, sort_keys=True)
                sandlot_db.set_ai_brief(sid, BRIEF_TYPE_SWAP, subject_key, text, model, input_hash)
                generated += 1
            except Exception as exc:
                log.warning("Waiver swap AI failed for %s: %s", subject_key, exc)
                errors.append(f"{subject_key}: {exc}")

        if not sandlot_db.get_ai_brief(sid, BRIEF_TYPE_REFRESH, REFRESH_SUBJECT):
            context = _refresh_prompt_context(row, payload)
            input_hash = _hash_context(context)
            try:
                text, model = client.complete(
                    _refresh_messages(context),
                    max_tokens=260,
                    model_order=sandlot_skipper.default_model_order(),
                )
                sandlot_db.set_ai_brief(sid, BRIEF_TYPE_REFRESH, REFRESH_SUBJECT, text.strip(), model, input_hash)
                generated += 1
            except Exception as exc:
                log.warning("Refresh brief AI failed for snapshot %s: %s", sid, exc)
                errors.append(f"refresh_brief: {exc}")
        else:
            skipped += 1

        return {"attempted": len(cards[:limit]) + 1, "generated": generated, "skipped": skipped, "errors": errors[:8]}
    except Exception as exc:
        log.warning("Waiver AI warm failed: %s", exc)
        return {"attempted": 0, "generated": 0, "skipped": 0, "errors": [str(exc)]}


def _add_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    fpg, source, true_fpg = _extract_add_fpg(stats)
    if fpg is None:
        return None
    tokens = _position_tokens(row)
    return {
        "id": str(row.get("id") or _slug(row.get("name") or "free-agent")),
        "name": row.get("name") or "Unknown free agent",
        "team": row.get("team") or "",
        "positions": _position_display(row, tokens),
        "tokens": tokens,
        "age": _age(row, stats),
        "fpg": round(fpg, 2),
        "score_source": source,
        "true_fpg": true_fpg,
        "raw": row,
    }


def _move_out_candidates(rows: list[dict[str, Any]], weak_positions: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        tokens = _position_tokens(row)
        fpg = _number(row.get("fppg"))
        is_bench = _is_bench(row)
        status_issue = _has_status_issue(row)
        weak_starter = bool(tokens & set(weak_positions)) and not is_bench and not status_issue
        if not (is_bench or status_issue or weak_starter):
            continue
        out.append(
            {
                "id": str(row.get("id") or _slug(row.get("name") or "roster-player")),
                "name": row.get("name") or "Unknown player",
                "team": row.get("team") or "",
                "positions": _position_display(row, tokens),
                "tokens": tokens,
                "slot": row.get("slot") or row.get("slot_full") or "",
                "age": _age(row, {}),
                "fpg": round(fpg if fpg is not None else 0.0, 2),
                "injury": row.get("injury") or row.get("status"),
                "is_bench": is_bench,
                "status_issue": status_issue,
                "weak_starter": weak_starter,
                "raw": row,
            }
        )
    if out:
        return out

    fallback = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        fpg = _number(row.get("fppg"))
        if fpg is None:
            continue
        tokens = _position_tokens(row)
        fallback.append(
            {
                "id": str(row.get("id") or _slug(row.get("name") or "roster-player")),
                "name": row.get("name") or "Unknown player",
                "team": row.get("team") or "",
                "positions": _position_display(row, tokens),
                "tokens": tokens,
                "slot": row.get("slot") or row.get("slot_full") or "",
                "age": _age(row, {}),
                "fpg": round(fpg, 2),
                "injury": row.get("injury") or row.get("status"),
                "is_bench": _is_bench(row),
                "status_issue": _has_status_issue(row),
                "weak_starter": True,
                "raw": row,
            }
        )
    fallback.sort(key=lambda p: (p["fpg"], p["name"]))
    return fallback[:6]


def _pair_card(snapshot_id: int, add: dict[str, Any], move: dict[str, Any], weak_positions: list[str]) -> dict[str, Any] | None:
    add_tokens = add["tokens"]
    move_tokens = move["tokens"]
    direct = sorted((add_tokens & move_tokens) - {"P"})
    weak_fit = sorted(add_tokens & set(weak_positions))
    same_group = _same_position_group(add_tokens, move_tokens)
    net_delta = round(float(add["fpg"]) - float(move["fpg"]), 2)

    if net_delta <= 0 and not (move["status_issue"] and add["fpg"] > 0):
        return None

    if direct:
        fit = "direct"
        fills_position = direct[0]
    elif weak_fit and (move["is_bench"] or move["status_issue"] or net_delta >= 1.5):
        fit = "loose"
        fills_position = weak_fit[0]
    elif same_group and (move["is_bench"] or move["status_issue"]) and net_delta >= 1.0:
        fit = "loose"
        fills_position = sorted(add_tokens)[0] if add_tokens else None
    else:
        return None

    dynasty_note, dynasty_penalty = _dynasty_note(move)
    fallback_penalty = 0 if add["true_fpg"] else -0.8
    loose_penalty = -0.7 if fit == "loose" else 0
    sort_score = net_delta
    sort_score += 1.0 if weak_fit else 0
    sort_score += 0.7 if fit == "direct" else 0
    sort_score += 0.5 if move["is_bench"] else 0
    sort_score += 0.8 if move["status_issue"] else 0
    sort_score += loose_penalty + fallback_penalty + dynasty_penalty

    confidence = _confidence(
        net_delta=net_delta,
        fit=fit,
        weak_fit=bool(weak_fit),
        true_fpg=bool(add["true_fpg"]),
        dynasty_penalty=dynasty_penalty,
        sort_score=sort_score,
    )
    evidence = _evidence_chips(add, move, net_delta, fit, fills_position, weak_fit, dynasty_note)
    add_name = add["name"]
    move_name = move["name"]
    card_id = f"waiver:{snapshot_id}:{add['id']}:{move['id']}"
    return {
        "id": card_id,
        "rank": None,
        "add": {
            "id": add["id"],
            "name": add_name,
            "team": add["team"],
            "positions": add["positions"],
            "age": add["age"],
            "fpg": round(float(add["fpg"]), 1),
            "score_source": add["score_source"],
        },
        "move_out": {
            "id": move["id"],
            "name": move_name,
            "team": move["team"],
            "positions": move["positions"],
            "slot": move["slot"],
            "age": move["age"],
            "fpg": round(float(move["fpg"]), 1),
            "injury": move["injury"],
        },
        "net_delta": round(net_delta, 1),
        "sort_score": round(sort_score, 2),
        "fills_position": fills_position,
        "fit": fit,
        "confidence": confidence,
        "why": _deterministic_why(add_name, move_name, net_delta, fills_position, fit, move),
        "risk": _deterministic_risk(add, move, fit),
        "dynasty_note": dynasty_note,
        "evidence_chips": evidence,
        "explanation": {"state": "deterministic", "model": None, "generated_at": None},
    }


def _weak_positions(rows: list[dict[str, Any]]) -> list[str]:
    buckets: dict[str, list[float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        fpg = _number(row.get("fppg"))
        if fpg is None:
            continue
        for pos in _position_tokens(row):
            if pos in GENERIC_POSITIONS or pos == "P":
                continue
            buckets.setdefault(pos, []).append(fpg)
    averages = [
        (pos, sum(values) / len(values))
        for pos, values in buckets.items()
        if values
    ]
    averages.sort(key=lambda item: (item[1], item[0]))
    return [pos for pos, _avg in averages[:WEAK_POSITION_COUNT]]


def _extract_add_fpg(stats: dict[str, Any]) -> tuple[float | None, str | None, bool]:
    for source in TRUE_FPG_KEYS:
        value = _stat_by_key(stats, source)
        parsed = _number(value)
        if parsed is not None:
            return parsed, source, True
    for source in FALLBACK_SCORE_KEYS:
        value = _stat_by_key(stats, source)
        parsed = _number(value)
        if parsed is not None:
            return parsed, source, False
    cells = stats.get("_cells")
    if isinstance(cells, list):
        nums = [_number(v) for v in cells]
        nums = [n for n in nums if n is not None]
        if nums:
            return max(nums), "_cells", False
    return None, None, False


def _stat_by_key(stats: dict[str, Any], key: str) -> Any:
    if key in stats:
        return stats[key]
    target = _norm_key(key)
    for k, v in stats.items():
        if _norm_key(str(k)) == target:
            return v
    return None


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _position_tokens(row: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for key in ("all_positions", "positions", "multi_positions", "pos", "slot"):
        value = row.get(key)
        if value:
            values.append(value)

    tokens: set[str] = set()
    for value in values:
        if isinstance(value, list):
            parts = value
        else:
            parts = re.split(r"[/,\s]+", str(value))
        for raw in parts:
            token = str(raw or "").strip().upper()
            if not token:
                continue
            token = POSITION_ALIASES.get(token, token)
            if token in GENERIC_POSITIONS:
                continue
            if token in POSITION_TOKENS:
                tokens.add(token)
    return tokens


def _position_display(row: dict[str, Any], tokens: set[str]) -> str:
    raw = row.get("positions") or row.get("pos") or row.get("multi_positions")
    if isinstance(raw, list):
        raw = "/".join(str(v) for v in raw if v)
    if raw:
        return str(raw)
    if tokens:
        return "/".join(sorted(tokens))
    return "UT"


def _same_position_group(a: set[str], b: set[str]) -> bool:
    if a & PITCHER_POSITIONS and b & PITCHER_POSITIONS:
        return True
    if a & HITTER_POSITIONS and b & HITTER_POSITIONS:
        return True
    return False


def _has_status_issue(row: dict[str, Any]) -> bool:
    joined = " ".join(str(row.get(k) or "") for k in ("injury", "status", "slot", "slot_full")).upper()
    return any(token in joined for token in STATUS_ISSUE_TOKENS)


def _is_bench(row: dict[str, Any]) -> bool:
    joined = " ".join(str(row.get(k) or "") for k in ("slot", "slot_full")).upper()
    return any(token in joined.split() for token in ("BN", "BE", "BENCH", "RESERVE", "RES"))


def _age(row: dict[str, Any], stats: dict[str, Any]) -> int | None:
    for value in (row.get("age"), stats.get("Age"), stats.get("AGE"), stats.get("age")):
        parsed = _number(value)
        if parsed is not None and parsed > 0:
            return int(parsed)
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "--", "—"}:
        return None
    text = text.replace(",", "").replace("+", "").replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _dynasty_note(move: dict[str, Any]) -> tuple[str, float]:
    age = move.get("age")
    if age is None:
        return "Age unavailable; check dynasty context before moving him out.", -0.5
    if int(age) <= 24:
        return f"{move['name']} is age {age}; treat this as a soft dynasty warning, not an automatic drop.", -1.0
    return "No major dynasty concern from age alone.", 0.0


def _confidence(
    *,
    net_delta: float,
    fit: str,
    weak_fit: bool,
    true_fpg: bool,
    dynasty_penalty: float,
    sort_score: float,
) -> str:
    if net_delta >= 1.5 and (fit == "direct" or weak_fit) and true_fpg and dynasty_penalty == 0:
        return "High"
    if net_delta > 0 and sort_score >= 0:
        return "Medium"
    return "Low"


def _evidence_chips(
    add: dict[str, Any],
    move: dict[str, Any],
    net_delta: float,
    fit: str,
    fills_position: str | None,
    weak_fit: list[str],
    dynasty_note: str,
) -> list[str]:
    chips = [f"{_format_delta(net_delta)} FP/G"]
    if fills_position:
        chips.append(f"{fills_position} {'fit' if fit == 'direct' else 'need'}")
    if weak_fit:
        chips.append("Weak position")
    if move["is_bench"]:
        chips.append("Bench move-out")
    if move["status_issue"]:
        chips.append(str(move["injury"] or "Status issue"))
    if not add["true_fpg"]:
        chips.append(f"{add['score_source']} fallback")
    if "Age unavailable" in dynasty_note or "soft dynasty warning" in dynasty_note:
        chips.append("Dynasty check")
    return chips[:6]


def _deterministic_why(
    add_name: str,
    move_name: str,
    net_delta: float,
    fills_position: str | None,
    fit: str,
    move: dict[str, Any],
) -> str:
    fit_text = f" at {fills_position}" if fills_position else ""
    if move["status_issue"]:
        return f"{add_name} is {_format_delta(net_delta)} FP/G over {move_name}{fit_text}, and {move_name} carries a status flag."
    if move["is_bench"]:
        return f"{add_name} is {_format_delta(net_delta)} FP/G over bench option {move_name}{fit_text}."
    if fit == "direct":
        return f"{add_name} is {_format_delta(net_delta)} FP/G over {move_name} on a direct {fills_position or 'position'} fit."
    return f"{add_name} improves a weak roster area by {_format_delta(net_delta)} FP/G over {move_name}."


def _deterministic_risk(add: dict[str, Any], move: dict[str, Any], fit: str) -> str:
    if not add["true_fpg"]:
        return f"{add['name']}'s value uses {add['score_source']} instead of true FP/G, so verify the Fantrax stat column."
    if fit == "loose":
        return "Position fit is loose, so this is a roster-shape review rather than a clean one-for-one replacement."
    if move.get("age") is None or (isinstance(move.get("age"), int) and move["age"] <= 24):
        return "Dynasty context matters because the move-out player may have unknown or young-player upside."
    return "No major red flag in the snapshot; verify current role before making a waiver decision."


def _format_delta(value: float) -> str:
    return f"{value:+.1f}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "player"


def _freshness(taken_at: Any) -> dict[str, Any]:
    if not isinstance(taken_at, datetime):
        return {"state": "missing", "age_minutes": None}
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((datetime.now(timezone.utc) - taken_at).total_seconds() / 60))
    if age_minutes <= 30:
        state = "fresh"
    elif age_minutes <= 24 * 60:
        state = "stale"
    else:
        state = "old"
    return {"state": state, "age_minutes": age_minutes}


def _overlay_cached_swap_explanations(snapshot_id: int, cards: list[dict[str, Any]]) -> None:
    for card in cards:
        cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_SWAP, str(card["id"]))
        if not cached:
            continue
        parsed = _parse_swap_ai(cached.get("text") or "")
        if parsed.get("why"):
            card["why"] = parsed["why"]
        if parsed.get("risk"):
            card["risk"] = parsed["risk"]
        card["explanation"] = {
            "state": "ready",
            "model": cached.get("model"),
            "generated_at": cached.get("generated_at"),
        }


def _cached_refresh_brief(snapshot_id: int) -> dict[str, Any]:
    cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_REFRESH, REFRESH_SUBJECT)
    if not cached:
        return {"state": "missing", "text": None, "model": None, "generated_at": None}
    return {
        "state": "ready",
        "text": cached.get("text"),
        "model": cached.get("model"),
        "generated_at": cached.get("generated_at"),
    }


def _parse_swap_ai(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except Exception:
                parsed = None
    if isinstance(parsed, dict):
        why = str(parsed.get("why") or "").strip()
        risk = str(parsed.get("risk") or "").strip()
        return {"why": why[:500], "risk": risk[:500]}
    lines = [line.strip(" -\n\t") for line in text.splitlines() if line.strip()]
    return {"why": (lines[0] if lines else text)[:500], "risk": (lines[1] if len(lines) > 1 else "")[:500]}


def _swap_prompt_context(snapshot_row: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_row.get("id"),
        "taken_at": snapshot_row.get("taken_at"),
        "card_id": card.get("id"),
        "rank": card.get("rank"),
        "add": card.get("add"),
        "move_out": card.get("move_out"),
        "net_delta": card.get("net_delta"),
        "confidence": card.get("confidence"),
        "fit": card.get("fit"),
        "fills_position": card.get("fills_position"),
        "evidence_chips": card.get("evidence_chips"),
        "dynasty_note": card.get("dynasty_note"),
        "deterministic_why": card.get("why"),
        "deterministic_risk": card.get("risk"),
    }


def _refresh_prompt_context(snapshot_row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    data = snapshot_row.get("data") or {}
    return {
        "snapshot_id": snapshot_row.get("id"),
        "taken_at": snapshot_row.get("taken_at"),
        "team_name": data.get("team_name"),
        "freshness": payload.get("freshness"),
        "weak_positions": (payload.get("diagnostics") or {}).get("weak_positions"),
        "top_waiver_swaps": [
            {
                "rank": c.get("rank"),
                "add": (c.get("add") or {}).get("name"),
                "move_out": (c.get("move_out") or {}).get("name"),
                "net_delta": c.get("net_delta"),
                "confidence": c.get("confidence"),
                "evidence_chips": c.get("evidence_chips"),
                "dynasty_note": c.get("dynasty_note"),
            }
            for c in (payload.get("cards") or [])[:5]
        ],
    }


def _swap_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": WAIVER_SWAP_SYSTEM_PROMPT},
        {"role": "user", "content": "Explain this deterministic waiver card:\n" + json.dumps(context, default=str, indent=2)},
    ]


def _refresh_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": REFRESH_BRIEF_SYSTEM_PROMPT},
        {"role": "user", "content": "Write the refresh brief from this deterministic JSON:\n" + json.dumps(context, default=str, indent=2)},
    ]


def _hash_context(context: dict[str, Any]) -> str:
    body = json.dumps(context, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
