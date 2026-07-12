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

import sandlot_data_quality
import sandlot_db
import sandlot_skipper

log = logging.getLogger(__name__)

TRUE_FPG_KEYS = ("FP/G", "FPG", "FPts/G", "FP/Gm", "FP/Game", "Fantasy Points/Game")
FANTRAX_AVG_KEYS = ("Avg", "Average")
SCORE_CONTEXT_KEYS = ("Score", "FPts", "ProjFPts", "FP", "Fantasy Points")
MIN_PLAUSIBLE_FPG = 0.5
MAX_PLAUSIBLE_FPG = 25.0

BRIEF_TYPE_SWAP = "waiver_swap"
BRIEF_TYPE_REFRESH = "refresh_brief"
REFRESH_SUBJECT = "latest"

CARD_LIMIT = 8
WEAK_POSITION_COUNT = 3
DYNASTY_PROTECTED_MAX_AGE = 24
# Owner-level hard stops outrank projections, rankings, and upstream data.
# Keep this deliberately explicit and small: these names can never appear on
# the move-out side of an add/drop recommendation.
NEVER_DROP_PLAYER_NAMES = {"aaron judge"}

POSITION_ALIASES = {
    "LF": "OF",
    "CF": "OF",
    "RF": "OF",
    "MI": "SS",
    "CI": "3B",
}
POSITION_TOKENS = {"C", "1B", "2B", "3B", "SS", "OF", "UT", "SP", "RP", "P"}
GENERIC_POSITIONS = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "MIN", "MINORS", "HIT", "PIT", "ALL", "UTIL"}
PITCHER_POSITIONS = {"SP", "RP", "P"}
HITTER_POSITIONS = {"C", "1B", "2B", "3B", "SS", "OF", "UT"}
PROTECTED_MOVE_OUT_SLOTS = {"IL", "IR", "MIN", "MINORS"}
PROTECTED_PLAYER_FLAGS = {
    "protected",
    "is_protected",
    "keeper",
    "is_keeper",
    "keeper_protected",
    "minor_league",
    "minors",
    "is_minor_leaguer",
}
STATUS_ISSUE_TOKENS = {"DTD", "OUT", "IL", "IL10", "IL60", "IR", "SUSP", "NA", "D/L"}
INJURY_STASH_TOKENS = {"IL", "IL10", "IL60", "IR", "D/L"}

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
    freshness = _freshness(taken_at)
    data_quality = sandlot_data_quality.snapshot_data_quality(data)

    message = None
    diagnostics: dict[str, Any] = {
        "weak_positions": [],
        "free_agent_count": len(fa_players),
        "move_out_count": 0,
        "protected_move_out_count": 0,
        "protected_move_outs": [],
    }
    if data_quality.get("add_drop_recommendations_ready") is not True:
        cards: list[dict[str, Any]] = []
        message = (
            "Waiver recommendations paused: "
            + sandlot_data_quality.short_reason(data_quality, purpose="add_drop_recommendations")
            + "."
        )
    elif not fa_players:
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
        _overlay_cached_swap_explanations(snapshot_row, cards)
        brief = _cached_refresh_brief(
            snapshot_row,
            {
                "freshness": freshness,
                "cards": cards,
                "diagnostics": diagnostics,
                "data_quality": data_quality,
            },
        )
    else:
        brief = {"state": "missing", "text": None, "model": None, "generated_at": None}

    return {
        "snapshot_id": snapshot_id,
        "taken_at": taken_at,
        "freshness": freshness,
        "cards": cards,
        "brief": brief,
        "message": message,
        "diagnostics": diagnostics,
        "data_quality": data_quality,
    }


def build_waiver_cards(
    *,
    roster_rows: list[dict[str, Any]],
    fa_players: list[dict[str, Any]],
    snapshot_id: int,
    limit: int | None = CARD_LIMIT,
    allow_nonpositive_rate: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    weak_positions = _weak_positions(roster_rows)
    move_candidates, protected_move_outs = _move_out_candidates(roster_rows, weak_positions)
    parsed_add_candidates = [_add_candidate(p) for p in fa_players]
    parsed_add_candidates = [p for p in parsed_add_candidates if p and p["fpg"] > 0]
    add_candidates = [
        p
        for p in parsed_add_candidates
        if p["true_fpg"] and p["age"] is not None
    ]

    candidates: list[dict[str, Any]] = []
    for add in add_candidates:
        for move in move_candidates:
            card = _pair_card(
                snapshot_id,
                add,
                move,
                weak_positions,
                allow_nonpositive_rate=allow_nonpositive_rate,
            )
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

    selection_limit = len(candidates) if limit is None else max(0, limit)
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
        if len(selected) >= selection_limit:
            break

    if len(selected) < selection_limit:
        for card in candidates:
            cid = str(card["id"])
            if cid in selected_ids:
                continue
            selected.append(card)
            selected_ids.add(cid)
            if len(selected) >= selection_limit:
                break

    for i, card in enumerate(selected, start=1):
        card["rank"] = i

    diagnostics = {
        "weak_positions": weak_positions,
        "free_agent_count": len(fa_players),
        "parsed_add_count": len(parsed_add_candidates),
        "usable_add_count": len(add_candidates),
        "excluded_untrusted_value_count": sum(1 for p in parsed_add_candidates if not p["true_fpg"]),
        "excluded_missing_age_count": sum(1 for p in parsed_add_candidates if p["age"] is None),
        "move_out_count": len(move_candidates),
        "protected_move_out_count": len(protected_move_outs),
        "protected_move_outs": protected_move_outs[:8],
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
            context = _swap_prompt_context(row, card)
            input_hash = _hash_context(context)
            cached = sandlot_db.get_ai_brief(sid, BRIEF_TYPE_SWAP, subject_key)
            if cached and cached.get("input_hash") == input_hash:
                skipped += 1
                continue
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

        refresh_context = _refresh_prompt_context(row, payload)
        refresh_hash = _hash_context(refresh_context)
        cached_refresh = sandlot_db.get_ai_brief(sid, BRIEF_TYPE_REFRESH, REFRESH_SUBJECT)
        if cached_refresh and cached_refresh.get("input_hash") == refresh_hash:
            skipped += 1
        else:
            try:
                text, model = client.complete(
                    _refresh_messages(refresh_context),
                    max_tokens=260,
                    model_order=sandlot_skipper.default_model_order(),
                )
                sandlot_db.set_ai_brief(sid, BRIEF_TYPE_REFRESH, REFRESH_SUBJECT, text.strip(), model, refresh_hash)
                generated += 1
            except Exception as exc:
                log.warning("Refresh brief AI failed for snapshot %s: %s", sid, exc)
                errors.append(f"refresh_brief: {exc}")

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
    age, age_source = _age_with_source(row, stats)
    return {
        "id": str(row.get("id") or _slug(row.get("name") or "free-agent")),
        "name": row.get("name") or "Unknown free agent",
        "team": row.get("team") or "",
        "positions": _position_display(row, tokens),
        "tokens": tokens,
        "age": age,
        "age_source": age_source,
        "fpg": round(fpg, 2),
        "score_source": source,
        "true_fpg": true_fpg,
        "raw": row,
    }


def _move_out_candidates(rows: list[dict[str, Any]], weak_positions: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    out: list[dict[str, Any]] = []
    protected: list[str] = []
    protected_names: set[str] = set()

    def record_protected(row: dict[str, Any]) -> None:
        name = str(row.get("name") or "Unknown player")
        if name not in protected_names:
            protected_names.add(name)
            protected.append(name)

    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        tokens = _position_tokens(row)
        fpg = _number(row.get("fppg"))
        if _protect_move_out(row, fpg):
            record_protected(row)
            continue
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
                "age_source": _age_with_source(row, {})[1],
                "fpg": round(fpg if fpg is not None else 0.0, 2),
                "injury": row.get("injury") or row.get("status"),
                "is_bench": is_bench,
                "status_issue": status_issue,
                "weak_starter": weak_starter,
                "raw": row,
            }
        )
    if out:
        return out, protected

    fallback = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        fpg = _number(row.get("fppg"))
        if fpg is None:
            continue
        if _protect_move_out(row, fpg):
            record_protected(row)
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
                "age_source": _age_with_source(row, {})[1],
                "fpg": round(fpg, 2),
                "injury": row.get("injury") or row.get("status"),
                "is_bench": _is_bench(row),
                "status_issue": _has_status_issue(row),
                "weak_starter": True,
                "raw": row,
            }
        )
    fallback.sort(key=lambda p: (p["fpg"], p["name"]))
    return fallback[:6], protected


def _pair_card(
    snapshot_id: int,
    add: dict[str, Any],
    move: dict[str, Any],
    weak_positions: list[str],
    *,
    allow_nonpositive_rate: bool = False,
) -> dict[str, Any] | None:
    add_tokens = add["tokens"]
    move_tokens = move["tokens"]
    direct = sorted((add_tokens & move_tokens) - {"P"})
    weak_fit = sorted(add_tokens & set(weak_positions))
    same_group = _same_position_group(add_tokens, move_tokens)
    net_delta = round(float(add["fpg"]) - float(move["fpg"]), 2)
    display_delta = round(net_delta, 1)

    if display_delta <= 0 and not allow_nonpositive_rate:
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
    fallback_penalty = 0 if add["true_fpg"] else -2.0
    loose_penalty = -0.7 if fit == "loose" else 0
    if allow_nonpositive_rate:
        remaining_games = len((add.get("raw") or {}).get("future_games") or [])
        sort_score = float(add["fpg"]) * remaining_games
    else:
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
            "age_source": add.get("age_source"),
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
            "age_source": move.get("age_source"),
            "fpg": round(float(move["fpg"]), 1),
            "injury": move["injury"],
        },
        "net_delta": display_delta,
        "sort_score": round(sort_score, 2),
        "fills_position": fills_position,
        "fit": fit,
        "confidence": confidence,
        "why": _deterministic_why(add_name, move_name, net_delta, fills_position, fit, move, bool(add["true_fpg"])),
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
        if _plausible_fpg(parsed, allow_low=True):
            return parsed, source, True

    # In Fantrax scoring tables, "Avg" is usually scoring average when it is
    # paired with a Score/FPts column. Avoid using standalone AVG because that
    # can be baseball batting average, not fantasy points per game.
    for source in FANTRAX_AVG_KEYS:
        value = _stat_by_key(stats, source)
        parsed = _number(value)
        if _has_score_context(stats) and _plausible_fpg(parsed):
            return parsed, source, True

    inferred = _infer_fpg_from_cells(stats.get("_cells"))
    if inferred is not None:
        return inferred, "_cells inferred FP/G", False
    return None, None, False


def _has_score_context(stats: dict[str, Any]) -> bool:
    return any(_stat_by_key(stats, key) is not None for key in SCORE_CONTEXT_KEYS)


def _plausible_fpg(value: float | None, *, allow_low: bool = False) -> bool:
    if value is None:
        return False
    lower = 0.0 if allow_low else MIN_PLAUSIBLE_FPG
    return lower < float(value) <= MAX_PLAUSIBLE_FPG


def _infer_fpg_from_cells(cells: Any) -> float | None:
    """Infer Fantrax Avg/FP-G from an unlabeled row without ever using rank.

    A typical available-player row is shaped like:
    rank, status, age, score, avg, rostered%, change%.

    The old fallback used the largest numeric cell, which turned rank 688 into
    "+688 FP/G". This only accepts a plausible per-game value and prefers the
    score -> average pair when the table headers were not captured.
    """
    if not isinstance(cells, list):
        return None

    parsed: list[tuple[int, float, str]] = []
    for idx, raw in enumerate(cells):
        text = str(raw or "").strip()
        if "%" in text:
            continue
        value = _number(raw)
        if value is None:
            continue
        parsed.append((idx, value, text))

    for current, following in zip(parsed, parsed[1:]):
        _idx, score_value, _score_text = current
        next_idx, avg_value, avg_text = following
        if next_idx < 3:
            continue
        if score_value > avg_value and _plausible_fpg(avg_value) and _has_decimal(avg_text):
            return avg_value

    candidates = [
        value
        for idx, value, text in parsed
        if idx >= 3 and _plausible_fpg(value) and _has_decimal(text)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _has_decimal(text: str) -> bool:
    return bool(re.search(r"\d+\.\d+", text))


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
    return bool(_status_tokens(row, ("injury", "status", "slot", "slot_full")) & STATUS_ISSUE_TOKENS)


def _has_injury_stash_status(row: dict[str, Any]) -> bool:
    return bool(_status_tokens(row, ("injury", "status")) & INJURY_STASH_TOKENS)


def _status_tokens(row: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    tokens: set[str] = set()
    for key in keys:
        value = str(row.get(key) or "").strip().upper()
        tokens.update(token for token in re.split(r"[^A-Z0-9/]+", value) if token)
    return tokens


def _protect_injury_stash(row: dict[str, Any], _fpg: float | None) -> bool:
    """Do not turn missing current-season production into a drop suggestion.

    Fantrax can report IL/IR players with misleading current-season lines:
    sometimes 0 FP/G because they have not played, sometimes a good FP/G from
    a small pre-injury sample. Without a separate current-news signal, injury
    status is not enough evidence to recommend moving that player out. Keep
    those players out of the waiver-drop board until a richer injury/news layer
    can classify return timing and long-absence risk.
    """
    return _has_injury_stash_status(row)


def _protect_move_out(row: dict[str, Any], fpg: float | None) -> bool:
    return (
        _protect_named_anchor(row)
        or _protect_move_out_slot(row)
        or _protect_injury_stash(row, fpg)
        or _protect_player_flag(row)
        or _protect_dynasty_asset(row)
    )


def _protect_named_anchor(row: dict[str, Any]) -> bool:
    name = " ".join(str(row.get("name") or "").split()).casefold()
    return name in NEVER_DROP_PLAYER_NAMES


def _protect_dynasty_asset(row: dict[str, Any]) -> bool:
    age = _age(row, {})
    return age is None or age <= DYNASTY_PROTECTED_MAX_AGE


def _protect_player_flag(row: dict[str, Any]) -> bool:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    scorer = raw.get("scorer") if isinstance(raw.get("scorer"), dict) else {}
    for source in (row, raw, player, scorer):
        if any(_truthy(source.get(flag)) for flag in PROTECTED_PLAYER_FLAGS):
            return True
    return False


def _protect_move_out_slot(row: dict[str, Any]) -> bool:
    tokens = " ".join(str(row.get(k) or "") for k in ("slot", "slot_full")).upper().split()
    return any(token in PROTECTED_MOVE_OUT_SLOTS for token in tokens)


def _is_bench(row: dict[str, Any]) -> bool:
    joined = " ".join(str(row.get(k) or "") for k in ("slot", "slot_full")).upper()
    return any(token in joined.split() for token in ("BN", "BE", "BENCH", "RESERVE", "RES", "MIN", "MINORS"))


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _age(row: dict[str, Any], stats: dict[str, Any]) -> int | None:
    return _age_with_source(row, stats)[0]


def _age_with_source(row: dict[str, Any], stats: dict[str, Any]) -> tuple[int | None, str | None]:
    parsed = _number(row.get("age"))
    source = str(row.get("age_source") or "").strip()
    has_raw_provenance = isinstance(row.get("raw"), dict)
    if parsed is not None and 16 <= parsed <= 50:
        if _trusted_age_source(source):
            return int(parsed), source
        # Compatibility for normalized/synthetic rows that predate explicit
        # provenance. Real Fantrax roster rows carry `raw` and must fail closed.
        if not has_raw_provenance:
            return int(parsed), "legacy.normalized_age"

    for key in ("Age", "AGE", "age"):
        parsed = _number(stats.get(key))
        if parsed is not None and 16 <= parsed <= 50:
            return int(parsed), f"stats.{key}"
    cells = stats.get("_cells")
    if isinstance(cells, list) and len(cells) >= 5:
        parsed_age = _number(cells[2])
        score = _number(cells[3])
        per_game = _number(cells[4])
        if (
            parsed_age is not None
            and score is not None
            and 16 <= parsed_age <= 50
            and _plausible_fpg(per_game)
        ):
            return int(parsed_age), "stats._cells[2]"
    return None, None


def _trusted_age_source(value: Any) -> bool:
    source = str(value or "").strip().casefold()
    return bool(source) and not any(token in source for token in ("unknown", "fallback", "inferred", "legacy"))


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
    if not true_fpg:
        return "Low"
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
        chips.append(str(add["score_source"] or "Estimated FP/G"))
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
    true_fpg: bool,
) -> str:
    fit_text = f" at {fills_position}" if fills_position else ""
    if not true_fpg:
        return f"{add_name} is a watch-list fit over {move_name}{fit_text}, but the FP/G edge is unverified."
    if move["status_issue"]:
        return f"{add_name} is {_format_delta(net_delta)} FP/G over {move_name}{fit_text}, and {move_name} carries a status flag."
    if move["is_bench"]:
        return f"{add_name} is {_format_delta(net_delta)} FP/G over bench option {move_name}{fit_text}."
    if fit == "direct":
        return f"{add_name} is {_format_delta(net_delta)} FP/G over {move_name} on a direct {fills_position or 'position'} fit."
    return f"{add_name} improves a weak roster area by {_format_delta(net_delta)} FP/G over {move_name}."


def _deterministic_risk(add: dict[str, Any], move: dict[str, Any], fit: str) -> str:
    if not add["true_fpg"]:
        return f"{add['name']}'s FP/G is inferred from unlabeled Fantrax cells; treat this as a scouting lead, not trade-value evidence."
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


def _overlay_cached_swap_explanations(snapshot_row: dict[str, Any], cards: list[dict[str, Any]]) -> None:
    snapshot_id = int(snapshot_row.get("id") or 0)
    for card in cards:
        cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_SWAP, str(card["id"]))
        if not cached:
            continue
        expected_hash = _hash_context(_swap_prompt_context(snapshot_row, card))
        if cached.get("input_hash") and cached.get("input_hash") != expected_hash:
            card["explanation"] = {
                "state": "stale",
                "model": cached.get("model"),
                "generated_at": cached.get("generated_at"),
            }
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


def _cached_refresh_brief(snapshot_row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = int(snapshot_row.get("id") or 0)
    cached = sandlot_db.get_ai_brief(snapshot_id, BRIEF_TYPE_REFRESH, REFRESH_SUBJECT)
    if not cached:
        return {"state": "missing", "text": None, "model": None, "generated_at": None}
    expected_hash = _hash_context(_refresh_prompt_context(snapshot_row, payload))
    if cached.get("input_hash") and cached.get("input_hash") != expected_hash:
        return {
            "state": "stale",
            "text": None,
            "model": cached.get("model"),
            "generated_at": cached.get("generated_at"),
        }
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
        "data_quality": payload.get("data_quality"),
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
