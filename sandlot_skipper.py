"""Skipper chat — roster Q&A grounded in the latest Fantrax snapshot.

Uses OpenRouter's OpenAI-compatible API. Kimi (Moonshot) is the primary model;
Tencent Hunyuan free is a fallback if Kimi errors before any tokens stream.

Context tier:
- Tier 2 (default): system prompt + my roster + standings
- Tier 3: + every team's roster (escalated by keyword match)

Yields SSE-shaped dicts: {"type":"token","text":...} and a closing
{"type":"done","tier":...,"model":...}. The API layer JSON-encodes them
into `data: {...}\\n\\n` frames.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterator

from openai import OpenAI

import sandlot_data_quality

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PRIMARY_MODEL = "moonshotai/kimi-k2"
# Verified via OpenRouter /v1/models: hunyuan-a13b-instruct has no `:free`
# variant anymore; the current free Tencent model is hy3-preview:free.
FALLBACK_MODEL = "tencent/hy3-preview:free"
ALLOWED_CHAT_MODELS = (
    PRIMARY_MODEL,
    FALLBACK_MODEL,
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
)
ALLOWED_REASONING_EFFORTS = ("minimal", "low", "medium", "high")

# Keywords that escalate to tier 3 (load every team's roster). Kept short so
# we err toward tier 2 — tier 3 is ~10x larger context.
TIER3_KEYWORDS = (
    "league", "everyone", "all teams", "other team", "other teams",
    "compare", "comparison", "vs ", "versus", "against",
    "trade", "trading", "trader",
    "rivals", "rival", "opponent", "opponents",
    "standings",  # standings are tier 2 already, but rosters add color
    "weakness", "weakest team", "best team",
)

# Phrases that mean "compare me only against the team I'm playing this
# week". Deep matchup needs the opponent's roster, but NOT every team's —
# we extract just the opponent and stay at tier 2 to keep context small.
DEEP_MATCHUP_KEYWORDS = (
    "matchup analysis", "deep matchup", "matchup deep",
    "deep weekly matchup", "thorough matchup",
)

MATCHUP_KEYWORDS = (
    "matchup", "match-up", "match up", "against my", "against this",
    "this week against", "how am i doing", "how's it going",
    "how is it going", "anything i should be worried",
    "anything should i be worried", "worried about",
)

DEPTH_KEYWORDS = (
    "deep", "deeper", "in-depth", "in depth", "thorough", "detailed",
    "slot by slot", "slot-by-slot", "analyze", "analysis",
)


def is_deep_matchup_request(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(kw in p for kw in DEEP_MATCHUP_KEYWORDS)


def is_matchup_request(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(kw in p for kw in MATCHUP_KEYWORDS)

SYSTEM_PROMPT = """You are Skipper, a fantasy baseball assistant for a 12-team Fantrax keeper league.

You answer questions about the user's roster grounded in the snapshot data they provide. You are direct, neutral, and concise — not a hype-man, not a strategist. The user wants facts and a quick read, not opinion.

Rules:
- Answer only from the snapshot. If a field is missing, say exactly what is missing and what nearby snapshot data is available. Do not speculate.
- Never answer with only "Data", "Data unavailable", or another one-word refusal.
- Cite players by name. Cite numbers when relevant (FP/G, FPts, age, slot, injury status).
- For matchup questions, prefer `matchup.projection` and `matchup.projection.drivers` when present. If `data_quality.projection_ready` is false, say data is incomplete and keep the answer score-based.
- Describe projection confidence with plain bands ("comfortable edge", "slight edge", "toss-up", "uphill"), not precise percentages. Do not invent probability math.
- No emojis. No throat-clearing intros ("Great question!"). No filler outros.
- Markdown allowed for short lists or **emphasis**. Avoid headers and tables for chat-length replies. When you use a bulleted list, put each "- " marker on its own line (no inline " - " separators).
- The user's team rows are flagged with `is_me: true`. Other teams (when present) are tier 3 context.
- If asked about strategy, trade grading, or anything beyond what the data shows, say what you can from the snapshot and note that deeper analysis is a separate feature.
- When you name a player from the snapshot, you can optionally wrap them as [[Full Name|id]] using the row's `id` field — this turns the name into a tappable link. Skip this if you don't have an exact id; the UI auto-links full names anyway.

Be brief by default — most answers are 1-4 sentences. When the user explicitly asks for depth (e.g. "deep", "thorough", "in-depth", "analysis", "slot by slot"), expand into a structured breakdown using bulleted markdown: lead with a one-sentence read, then 4-8 bullets covering opponent threats, your edges, injury / streak factors, and any specific players to start or sit. Stay grounded in the snapshot."""


def primary_model() -> str:
    return os.environ.get("SANDLOT_AI_MODEL_PRIMARY", PRIMARY_MODEL).strip() or PRIMARY_MODEL


def fallback_model() -> str:
    return os.environ.get("SANDLOT_AI_MODEL_FALLBACK", FALLBACK_MODEL).strip() or FALLBACK_MODEL


def default_model_order() -> tuple[str, str]:
    return (primary_model(), fallback_model())


def allowed_chat_models() -> tuple[str, ...]:
    configured = (primary_model(), fallback_model(), *ALLOWED_CHAT_MODELS)
    return tuple(dict.fromkeys(m for m in configured if m))


def model_order(selected_model: str | None = None) -> tuple[str, ...]:
    """Selected model first; Kimi remains the default primary fallback."""
    allowed = allowed_chat_models()
    selected = (selected_model or "").strip()
    ordered = []
    if selected in allowed:
        ordered.append(selected)
    ordered.extend(default_model_order())
    return tuple(dict.fromkeys(m for m in ordered if m))


def normalize_reasoning_effort(reasoning_effort: str | None) -> str | None:
    effort = (reasoning_effort or "").strip().lower()
    if effort in ("", "off", "none", "false", "0"):
        return None
    if effort in ALLOWED_REASONING_EFFORTS:
        return effort
    return "medium"


# ---------------------------------------------------------------------------
# Tier detection + context formatting
# ---------------------------------------------------------------------------

def detect_tier(prompt: str, snapshot: dict[str, Any]) -> int:
    """Return 2 or 3. Tier 3 needs all_team_rosters present in the snapshot."""
    if not snapshot.get("all_team_rosters"):
        return 2
    p = prompt.lower()
    for kw in TIER3_KEYWORDS:
        if kw in p:
            return 3
    return 2


def _slim_player(p: dict[str, Any]) -> dict[str, Any]:
    """Strip the verbose `raw` field; keep what the model actually needs."""
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "slot": p.get("slot"),
        "positions": p.get("positions"),
        "team": p.get("team"),
        "fppg": p.get("fppg"),
        "fpts": p.get("fpts"),
        "age": p.get("age"),
        "injury": p.get("injury"),
    }


def _slim_roster(roster: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
    if isinstance(roster, list):
        return {"rows": [_slim_player(p) for p in roster if isinstance(p, dict)]}
    if not isinstance(roster, dict):
        return {}
    return {
        "active": roster.get("active"),
        "active_max": roster.get("active_max"),
        "reserve": roster.get("reserve"),
        "reserve_max": roster.get("reserve_max"),
        "injured": roster.get("injured"),
        "injured_max": roster.get("injured_max"),
        "period_number": roster.get("period_number"),
        "period_date": roster.get("period_date"),
        "rows": [_slim_player(p) for p in (roster.get("rows") or [])],
    }


def _slim_standings(
    standings: dict[str, Any] | list[dict[str, Any]] | None,
    my_standing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    keep = ("rank", "team_id", "team_name", "win", "loss", "tie",
            "win_pct", "games_back", "fantasy_points", "streak", "waiver_order")
    if isinstance(standings, list):
        return {
            "my_record": {k: my_standing.get(k) for k in keep} if isinstance(my_standing, dict) else None,
            "records": [{k: r.get(k) for k in keep} for r in standings if isinstance(r, dict)],
        }
    if not isinstance(standings, dict):
        return {}
    return {
        "my_record": {k: (standings.get("my_record") or {}).get(k) for k in keep}
                     if standings.get("my_record") else None,
        "records": [{k: r.get(k) for k in keep} for r in (standings.get("records") or [])],
    }


def _slim_matchup(matchup: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(matchup, dict):
        return None
    keep = (
        "source", "period_number", "period_name", "start", "end", "days",
        "complete", "current", "my_team_id", "my_team_name", "my_side",
        "my_score", "opponent_team_id", "opponent_team_name",
        "opponent_score", "margin",
    )
    out = {k: matchup.get(k) for k in keep}
    if isinstance(matchup.get("projection"), dict):
        out["projection"] = matchup["projection"]
    return out


def _data_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    existing = snapshot.get("data_quality")
    if isinstance(existing, dict):
        return existing
    return sandlot_data_quality.snapshot_data_quality(snapshot)


def build_context(tier: int, snapshot: dict[str, Any], prompt: str = "") -> str:
    """Render the snapshot as a compact JSON-ish text block for the model."""
    import json

    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    data_quality = _data_quality(snapshot)
    ctx: dict[str, Any] = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_taken_at": snapshot.get("taken_at") or snapshot.get("timestamp"),
        "team_id": snapshot.get("team_id"),
        "team_name": snapshot.get("team_name"),
        "available_data": _available_data(snapshot),
        "data_quality": data_quality,
        "my_roster": _slim_roster(snapshot.get("roster")),
        "roster_meta": snapshot.get("roster_meta") if isinstance(snapshot.get("roster_meta"), dict) else None,
        "standings": _slim_standings(snapshot.get("standings"), snapshot.get("my_standing")),
    }
    if matchup:
        ctx["matchup"] = _slim_matchup(matchup)

    # Deep matchup: include only the opponent's roster, not every team — the
    # user is asking for a slot-by-slot read against this week's opponent
    # only. Saves ~10x context vs tier 3.
    if is_deep_matchup_request(prompt):
        opp = _opponent_roster(snapshot, matchup)
        if opp:
            ctx["opponent_roster"] = {
                "team_name": opp.get("team_name"),
                "rows": [_slim_player(p) for p in (opp.get("rows") or [])],
            }
    elif tier >= 3:
        all_rosters = snapshot.get("all_team_rosters") or {}
        ctx["all_team_rosters"] = {
            tid: {
                "team_name": team.get("team_name"),
                "is_me": team.get("is_me"),
                "rows": [_slim_player(p) for p in (team.get("rows") or [])],
            }
            for tid, team in all_rosters.items()
        }
    return "SNAPSHOT (JSON):\n```json\n" + json.dumps(ctx, default=str, indent=2) + "\n```"


def _available_data(snapshot: dict[str, Any]) -> dict[str, bool]:
    return {
        "roster": bool(snapshot.get("roster")),
        "standings": bool(snapshot.get("standings")),
        "all_team_rosters": bool(snapshot.get("all_team_rosters")),
        "free_agents": bool(snapshot.get("free_agents")),
        "matchup": bool(snapshot.get("matchup")),
        "transactions": bool(snapshot.get("transactions")),
        "pending_trades": bool(snapshot.get("pending_trades")),
        "data_quality": bool(snapshot.get("data_quality")),
        "player_index": bool(snapshot.get("player_index")),
    }


def deterministic_reply(user_msg: str, snapshot: dict[str, Any]) -> str | None:
    """Return a direct non-LLM answer for known high-value product flows."""
    text = (user_msg or "").lower()
    asks_matchup = is_matchup_request(text)
    asks_depth = any(token in text for token in DEPTH_KEYWORDS)
    if asks_matchup and not asks_depth:
        return _matchup_read_reply(snapshot)
    return None


def repair_reply(reply: str, user_msg: str, snapshot: dict[str, Any]) -> str:
    """Replace broken model refusals with a useful deterministic explanation."""
    cleaned = (reply or "").strip()
    normalized = " ".join(cleaned.lower().replace(".", " ").split())
    if normalized in {"data", "data unavailable", "unavailable", "no data"}:
        return deterministic_reply(user_msg, snapshot) or _generic_missing_reply(snapshot)
    return cleaned


def is_broken_reply(reply: str | None) -> bool:
    normalized = " ".join(str(reply or "").strip().lower().replace(".", " ").split())
    return normalized in {"data", "data unavailable", "unavailable", "no data"}


def _matchup_read_reply(snapshot: dict[str, Any]) -> str:
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else None
    data_quality = _data_quality(snapshot)
    roster = _slim_roster(snapshot.get("roster"))
    my_rows = roster.get("rows") or []
    all_rosters = snapshot.get("all_team_rosters") or {}
    opponent = _opponent_roster(snapshot, matchup)
    opponent_rows = opponent.get("rows") if opponent else []

    lines: list[str] = []
    score_line = _matchup_score_line(matchup)
    projection = matchup.get("projection") if isinstance(matchup, dict) and isinstance(matchup.get("projection"), dict) else None
    if projection and data_quality.get("projection_ready", True):
        lines.append(_projection_matchup_line(matchup, projection))
    elif matchup:
        if score_line:
            lines.append(score_line)
        else:
            opponent_name = matchup.get("opponent_team_name") or "your opponent"
            lines.append(f"I found this week's opponent ({opponent_name}), but Fantrax did not return both live scores.")
        if not data_quality.get("projection_ready", True):
            lines.append(
                "Data incomplete — score-based view only: "
                + sandlot_data_quality.short_reason(data_quality, purpose="projection")
                + "."
            )
        else:
            lines.append("Projection is unavailable, so this read stays score-based.")
    else:
        lines.append("I do not have the live matchup scoreboard in this snapshot yet, but I can still read roster pressure from your latest Fantrax data.")

    if projection and data_quality.get("projection_ready", True):
        lines.append("Biggest driver: " + _projection_driver_text(projection) + ".")
        lines.append("Move read: " + _projection_move_text(projection) + ".")

    if not projection and not data_quality.get("projection_ready", True) and not matchup:
        lines.append(
            "Data incomplete — score-based view only: "
            + sandlot_data_quality.short_reason(data_quality, purpose="projection")
            + "."
        )

    concerns = _matchup_concerns(my_rows)
    if concerns:
        lines.append("Watch: " + "; ".join(concerns[:3]) + ".")
    elif my_rows:
        lines.append("No active injury or sub-1.0 FP/G flags show up in your lineup snapshot.")

    if projection and data_quality.get("projection_ready", True):
        return " ".join(line for line in lines if line)

    if opponent_rows:
        edges = _position_edges(my_rows, opponent_rows)
        if edges:
            lines.append("Position read: " + "; ".join(edges[:3]) + ".")
        opp_top = _top_players(opponent_rows, limit=3)
        if opp_top:
            lines.append("Opponent threats: " + ", ".join(_player_label(p) for p in opp_top) + ".")
    elif matchup:
        lines.append("I have the score/opponent, but not opponent roster rows to compare individual players.")
    elif all_rosters:
        lines.append("I can see league rosters, but not the current opponent mapping in this snapshot. A fresh scrape should add the matchup object.")

    if len(lines) == 1:
        lines.append(_roster_summary_sentence(snapshot))
    return " ".join(line for line in lines if line)


def _matchup_score_line(matchup: dict[str, Any] | None) -> str | None:
    if not matchup:
        return None
    opponent_name = matchup.get("opponent_team_name") or "your opponent"
    my_score = _num(matchup.get("my_score"))
    opp_score = _num(matchup.get("opponent_score"))
    if my_score is None or opp_score is None:
        return None
    margin = round(my_score - opp_score, 2)
    if margin > 0:
        state = f"You're up {margin:g}"
    elif margin < 0:
        state = f"You're down {abs(margin):g}"
    else:
        state = "You're tied"
    period = matchup.get("period_name") or f"period {matchup.get('period_number')}"
    return f"{state} against {opponent_name}: {my_score:g} to {opp_score:g} in {period}."


def _projection_matchup_line(matchup: dict[str, Any], projection: dict[str, Any]) -> str:
    opponent_name = matchup.get("opponent_team_name") or "your opponent"
    band = _projection_band(projection)
    projected_my = _num(projection.get("projected_my"))
    projected_opp = _num(projection.get("projected_opp"))
    margin = _num((projection.get("drivers") or {}).get("projected_margin"))
    if margin is None and projected_my is not None and projected_opp is not None:
        margin = projected_my - projected_opp
    if margin is not None and margin > 0:
        read = f"You're favored with a {band}"
    elif margin is not None and margin < 0:
        read = f"You're not favored; this is a {band}"
    else:
        read = "This projects as a toss-up"
    if projected_my is not None and projected_opp is not None:
        return f"{read} against {opponent_name}: projected {projected_my:g} to {projected_opp:g}."
    return f"{read} against {opponent_name}."


def _projection_band(projection: dict[str, Any]) -> str:
    probability = _num(projection.get("win_probability"))
    drivers = projection.get("drivers") if isinstance(projection.get("drivers"), dict) else {}
    margin = _num(drivers.get("projected_margin"))
    if probability is not None:
        if probability >= 0.70:
            return "comfortable edge"
        if probability >= 0.55:
            return "slight edge"
        if probability > 0.45:
            return "toss-up"
        if probability > 0.30:
            return "slight uphill"
        return "steep uphill"
    if margin is not None:
        if margin >= 15:
            return "comfortable edge"
        if margin >= 5:
            return "slight edge"
        if margin > -5:
            return "toss-up"
        if margin > -15:
            return "slight uphill"
    return "steep uphill"


def _projection_driver_text(projection: dict[str, Any]) -> str:
    drivers = projection.get("drivers") if isinstance(projection.get("drivers"), dict) else {}
    rest_delta = _num(drivers.get("rest_of_period_delta")) or 0
    game_edge = _num(drivers.get("game_volume_edge")) or 0
    current_margin = _num(drivers.get("current_margin")) or 0
    if abs(rest_delta) >= 5:
        direction = "toward you" if rest_delta > 0 else "toward the opponent"
        return f"rest-of-period scoring swings {abs(rest_delta):g} points {direction}"
    if abs(game_edge) >= 2:
        if game_edge > 0:
            return f"schedule volume favors you by {abs(game_edge):g} remaining games"
        return f"schedule volume favors the opponent by {abs(game_edge):g} remaining games"
    if current_margin > 0:
        return f"the current {current_margin:g}-point lead is carrying the read"
    if current_margin < 0:
        return f"the current {abs(current_margin):g}-point deficit is carrying the read"
    summary = drivers.get("summary")
    if summary:
        return str(summary)
    return "remaining scoring is close enough that no single driver dominates"


def _projection_move_text(projection: dict[str, Any]) -> str:
    band = _projection_band(projection)
    drivers = projection.get("drivers") if isinstance(projection.get("drivers"), dict) else {}
    risk = str(drivers.get("risk_level") or "").lower()
    if band == "comfortable edge" and risk == "low":
        return "no forced move; avoid creating downside"
    if "uphill" in band:
        return "only chase a move that changes remaining games or upgrades the weakest active slot"
    if band == "toss-up" or risk in {"medium", "high"}:
        return "lineup and streamable game volume matter more than a cosmetic swap"
    return "small active-slot gains matter, but the score does not need a panic move"


def _missing_matchup_reply(snapshot: dict[str, Any]) -> str:
    """Backward-compatible wrapper kept for older tests/imports."""
    return _matchup_read_reply(snapshot)


def _roster_summary_sentence(snapshot: dict[str, Any]) -> str:
    standings = _slim_standings(snapshot.get("standings"), snapshot.get("my_standing"))
    mine = standings.get("my_record") or {}
    roster = _slim_roster(snapshot.get("roster"))
    roster_rows = roster.get("rows") or []
    team_name = snapshot.get("team_name") or "your team"
    record_bits = []
    if mine.get("rank") is not None:
        record_bits.append(f"rank {mine.get('rank')}")
    if mine.get("win") is not None and mine.get("loss") is not None:
        record_bits.append(f"{mine.get('win')}-{mine.get('loss')}")
    if mine.get("fantasy_points") is not None:
        record_bits.append(f"{mine.get('fantasy_points')} season FP")
    roster_bits = []
    if roster.get("active") is not None and roster.get("active_max") is not None:
        roster_bits.append(f"{roster.get('active')}/{roster.get('active_max')} active")
    if roster.get("reserve") is not None and roster.get("reserve_max") is not None:
        roster_bits.append(f"{roster.get('reserve')}/{roster.get('reserve_max')} reserve")

    lines = []
    if record_bits:
        lines.append(f"What I do have for {team_name}: " + ", ".join(str(v) for v in record_bits) + ".")
    if roster_bits or roster_rows:
        roster_text = ", ".join(roster_bits) if roster_bits else f"{len(roster_rows)} rostered players"
        lines.append(f"Roster context is available: {roster_text}.")
    return " ".join(lines)


def _opponent_roster(snapshot: dict[str, Any], matchup: dict[str, Any] | None) -> dict[str, Any] | None:
    all_rosters = snapshot.get("all_team_rosters") or {}
    if not isinstance(all_rosters, dict):
        return None
    opponent_id = matchup.get("opponent_team_id") if matchup else None
    if opponent_id and isinstance(all_rosters.get(opponent_id), dict):
        return all_rosters[opponent_id]
    opponent_name = (matchup or {}).get("opponent_team_name")
    if opponent_name:
        for team in all_rosters.values():
            if isinstance(team, dict) and team.get("team_name") == opponent_name:
                return team
    return None


def _active_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inactive = {"BN", "BENCH", "RES", "RESERVE", "IR", "IL", "INJ", "INJ RES", "MINORS"}
    out = []
    for row in rows or []:
        if not row.get("name"):
            continue
        slot = str(row.get("slot") or "").upper()
        if slot in inactive:
            continue
        out.append(row)
    return out


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _top_players(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    return sorted(
        _active_rows(rows),
        key=lambda p: _num(p.get("fppg")) if _num(p.get("fppg")) is not None else -999,
        reverse=True,
    )[:limit]


def _player_label(row: dict[str, Any]) -> str:
    fppg = _num(row.get("fppg"))
    suffix = f" ({fppg:g} FP/G)" if fppg is not None else ""
    return f"{row.get('name')}{suffix}"


def _matchup_concerns(rows: list[dict[str, Any]]) -> list[str]:
    concerns: list[str] = []
    for row in _active_rows(rows):
        injury = row.get("injury")
        if injury:
            concerns.append(f"{row.get('name')} is {injury}")
    low = [
        row for row in _active_rows(rows)
        if _num(row.get("fppg")) is not None and (_num(row.get("fppg")) or 0) < 1.0
    ]
    if low:
        names = ", ".join(row.get("name") for row in low[:3] if row.get("name"))
        concerns.append(f"low FP/G active spots: {names}")
    return concerns


def _position_list(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in (row.get("slot"), row.get("positions")):
        if isinstance(field, str):
            values.extend(part.strip().upper() for part in field.split(",") if part.strip())
        elif isinstance(field, list):
            values.extend(str(part).strip().upper() for part in field if str(part).strip())
    return values


def _position_best(rows: list[dict[str, Any]], pos: str) -> float | None:
    vals = [
        _num(row.get("fppg"))
        for row in _active_rows(rows)
        if pos in _position_list(row) and _num(row.get("fppg")) is not None
    ]
    return max(vals) if vals else None


def _position_edges(my_rows: list[dict[str, Any]], opp_rows: list[dict[str, Any]]) -> list[str]:
    edges = []
    for pos in ("C", "1B", "2B", "3B", "SS", "OF", "SP", "RP"):
        mine = _position_best(my_rows, pos)
        theirs = _position_best(opp_rows, pos)
        if mine is None or theirs is None:
            continue
        delta = round(mine - theirs, 2)
        if abs(delta) < 0.5:
            continue
        label = "edge" if delta > 0 else "pressure"
        edges.append((abs(delta), f"{pos} {label} {delta:+g} FP/G"))
    edges.sort(reverse=True, key=lambda x: x[0])
    return [text for _, text in edges]


def _generic_missing_reply(snapshot: dict[str, Any]) -> str:
    available = [name.replace("_", " ") for name, ok in _available_data(snapshot).items() if ok]
    if available:
        return "That exact field is not in the latest snapshot. Available snapshot data: " + ", ".join(available) + "."
    return "The latest snapshot does not have enough data to answer that yet. Run a refresh and try again."


# ---------------------------------------------------------------------------
# Message construction
# ---------------------------------------------------------------------------

def build_messages(
    history: list[dict[str, Any]],
    user_msg: str,
    context_block: str,
) -> list[dict[str, str]]:
    """Compose the final request payload.

    History rows from the DB look like {role, content, ...}. We pass them
    verbatim minus the assistant rows that have empty content (failed streams).
    """
    msgs: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": context_block},
    ]
    for row in history:
        role = row.get("role")
        content = row.get("content")
        if role == "assistant" and is_broken_reply(content):
            continue
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": user_msg})
    return msgs


# ---------------------------------------------------------------------------
# Streaming with fallback
# ---------------------------------------------------------------------------

class SkipperClient:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        # Defensive: strip whitespace/newlines that can sneak in via the
        # Railway/Vercel/etc. env-var UIs. httpx rejects newlines in header
        # values with a LocalProtocolError surfaced as APIConnectionError.
        key = key.strip()
        self.client = OpenAI(
            api_key=key,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                # OpenRouter rate-limit / attribution headers (optional but nice)
                "HTTP-Referer": "https://github.com/zachoelsner/fantrax-daily-audit",
                "X-Title": "Sandlot Skipper",
            },
        )

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        model_order: tuple[str, ...] | None = None,
        reasoning_effort: str | None = None,
    ) -> Iterator[tuple[str, str]]:
        """Yield ('token', text) chunks plus a final ('model', model_id) once.

        Tries the environment-configured primary model first. On error before
        any tokens stream, falls back to the configured fallback. Mid-stream
        errors are not retried (V1).
        """
        failures: list[str] = []
        extra_body = _reasoning_extra_body(reasoning_effort)
        for model in (model_order or default_model_order()):
            try:
                kwargs = {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "temperature": 0.3,
                }
                if extra_body:
                    kwargs["extra_body"] = extra_body
                stream = self.client.chat.completions.create(**kwargs)
                yielded_any = False
                for chunk in stream:
                    try:
                        delta = chunk.choices[0].delta
                    except (AttributeError, IndexError):
                        continue
                    text = getattr(delta, "content", None)
                    if text:
                        yielded_any = True
                        yield ("token", text)
                if yielded_any:
                    yield ("model", model)
                    return
                failures.append(f"{model}: empty stream")
                log.warning("Skipper model %s returned no tokens; trying fallback", model)
            except Exception as e:
                failures.append(f"{model}: {type(e).__name__}: {e}")
                log.warning("Skipper model %s failed: %s; trying fallback", model, e)
                continue
        raise RuntimeError("All Skipper models failed: " + " | ".join(failures))

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 220,
        model_order: tuple[str, ...] | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[str, str]:
        """Return a single completion plus the model id, using the stream fallback order.

        Pass `model_order` to override which model is tried first — e.g. the
        player-take call uses Tencent-first because Kimi's first-token latency
        on a cold prompt regularly pushes the profile load over a noticeable
        threshold. Defaults to the environment-configured primary/fallback order.
        """
        failures: list[str] = []
        extra_body = _reasoning_extra_body(reasoning_effort)
        for model in (model_order or default_model_order()):
            try:
                kwargs = {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                }
                if extra_body:
                    kwargs["extra_body"] = extra_body
                response = self.client.chat.completions.create(**kwargs)
                try:
                    text = response.choices[0].message.content or ""
                except (AttributeError, IndexError):
                    text = ""
                text = text.strip()
                if text:
                    return text, model
                failures.append(f"{model}: empty response")
                log.warning("Skipper model %s returned no text; trying fallback", model)
            except Exception as e:
                failures.append(f"{model}: {type(e).__name__}: {e}")
                log.warning("Skipper model %s failed: %s; trying fallback", model, e)
                continue
        raise RuntimeError("All Skipper models failed: " + " | ".join(failures))


def _reasoning_extra_body(reasoning_effort: str | None) -> dict[str, Any] | None:
    effort = normalize_reasoning_effort(reasoning_effort)
    if not effort:
        return None
    return {"reasoning": {"effort": effort, "exclude": True}}
