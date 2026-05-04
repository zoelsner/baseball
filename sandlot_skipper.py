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

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PRIMARY_MODEL = "moonshotai/kimi-k2"
# Verified via OpenRouter /v1/models: hunyuan-a13b-instruct has no `:free`
# variant anymore; the current free Tencent model is hy3-preview:free.
FALLBACK_MODEL = "tencent/hy3-preview:free"

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

SYSTEM_PROMPT = """You are Skipper, a fantasy baseball assistant for a 12-team Fantrax keeper league.

You answer questions about the user's roster grounded in the snapshot data they provide. You are direct, neutral, and concise — not a hype-man, not a strategist. The user wants facts and a quick read, not opinion.

Rules:
- Answer only from the snapshot. If the data isn't there, say "Data unavailable" and stop. Do not speculate.
- Cite players by name. Cite numbers when relevant (FP/G, FPts, age, slot, injury status).
- No emojis. No throat-clearing intros ("Great question!"). No filler outros.
- Markdown allowed for short lists or **emphasis**. Avoid headers and tables for chat-length replies.
- The user's team rows are flagged with `is_me: true`. Other teams (when present) are tier 3 context.
- If asked about strategy, trade grading, or anything beyond what the data shows, say what you can from the snapshot and note that deeper analysis is a separate feature.

Be brief. Most answers are 1-4 sentences."""


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
        "name": p.get("name"),
        "slot": p.get("slot"),
        "positions": p.get("positions"),
        "team": p.get("team"),
        "fppg": p.get("fppg"),
        "fpts": p.get("fpts"),
        "age": p.get("age"),
        "injury": p.get("injury"),
    }


def _slim_roster(roster: dict[str, Any] | None) -> dict[str, Any]:
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


def _slim_standings(standings: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(standings, dict):
        return {}
    keep = ("rank", "team_id", "team_name", "win", "loss", "tie",
            "win_pct", "games_back", "fantasy_points", "streak", "waiver_order")
    return {
        "my_record": {k: (standings.get("my_record") or {}).get(k) for k in keep}
                     if standings.get("my_record") else None,
        "records": [{k: r.get(k) for k in keep} for r in (standings.get("records") or [])],
    }


def build_context(tier: int, snapshot: dict[str, Any]) -> str:
    """Render the snapshot as a compact JSON-ish text block for the model."""
    import json

    ctx: dict[str, Any] = {
        "snapshot_taken_at": snapshot.get("timestamp"),
        "team_id": snapshot.get("team_id"),
        "team_name": snapshot.get("team_name"),
        "my_roster": _slim_roster(snapshot.get("roster")),
        "standings": _slim_standings(snapshot.get("standings")),
    }
    if tier >= 3:
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
        self.client = OpenAI(
            api_key=key,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                # OpenRouter rate-limit / attribution headers (optional but nice)
                "HTTP-Referer": "https://github.com/zachoelsner/fantrax-daily-audit",
                "X-Title": "Sandlot Skipper",
            },
        )

    def stream(self, messages: list[dict[str, str]]) -> Iterator[tuple[str, str]]:
        """Yield ('token', text) chunks plus a final ('model', model_id) once.

        Tries PRIMARY_MODEL first. On error before any tokens stream, falls
        back to FALLBACK_MODEL. Mid-stream errors are not retried (V1).
        """
        failures: list[str] = []
        for model in (PRIMARY_MODEL, FALLBACK_MODEL):
            try:
                stream = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    temperature=0.3,
                )
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
