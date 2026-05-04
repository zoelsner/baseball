"""Grounded web research via the Anthropic SDK.

This module is the antidote to training-data hallucination. It calls Claude
with the server-side `web_search_20260209` tool — Claude actually searches
the web during the request, then returns structured JSON with cited sources.
We force a structured output (`output_config.format`) so the response is
machine-readable and we can validate every claim has a source URL.

Why the SDK and not the CLI:
- Portable to Railway / any cloud (no Claude CLI auth flow)
- Prompt caching saves ~85% of input cost across 10+ research calls
- Built-in WebSearch tool with domain whitelist + 30-day date filtering at the
  prompt level
- Adaptive thinking on Opus 4.7 lets Claude decide thinking depth per call
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 4096

# Stable preamble — gets cached so subsequent player research calls only pay
# for the variable portion. Keep this content frozen and deterministic.
RESEARCH_SYSTEM = """You are a fantasy baseball researcher gathering CURRENT, GROUNDED information. Your output informs roster decisions for a smart but baseball-novice user, so accuracy matters more than thoroughness. NEVER use training-data recall — if you don't search, you can't claim it.

REQUIRED PROCESS:
1. Use the web_search tool. Run AT LEAST 3 distinct searches per player covering different angles:
   - "{player} injury status {month_year}"
   - "{player} role lineup playing time {month_year}"
   - "{player} recent stats trend"
   Add more searches if the player is a prospect, recently called up, or recently injured.
2. Trusted sources — prefer these in your final findings: fangraphs.com, mlb.com, baseballsavant.mlb.com, rosterresource.com, theathletic.com, espn.com, rotowire.com.
3. Recency — only consider sources from the last 30 days. If you find no recent information, say so explicitly. Do NOT fall back to training data.

CITATION RULES:
- Every `findings[].source_url` MUST also appear in `sources[]`.
- Don't fabricate URLs or article titles. If you didn't fetch it via web_search, don't cite it.
- If you can't verify any current info for a player, set verifiable=false and findings=[]. Explain in `notes`.

Output a single JSON object matching the schema. No prose around it."""


RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "as_of": {"type": "string", "description": "YYYY-MM-DD"},
        "status": {
            "type": "string",
            "enum": ["active", "IR", "minors", "DTD", "unclear"],
        },
        "current_role": {"type": "string"},
        "trend": {"type": "string", "enum": ["up", "flat", "down", "unclear"]},
        "summary": {
            "type": "string",
            "description": "2-3 sentence plain-English summary for a fantasy decision-maker. Avoid jargon.",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_date": {"type": "string"},
                },
                "required": ["claim", "source_url", "source_date"],
                "additionalProperties": False,
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "publisher": {"type": "string"},
                },
                "required": ["url", "title"],
                "additionalProperties": False,
            },
        },
        "verifiable": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["name", "as_of", "status", "summary", "verifiable", "findings", "sources"],
    "additionalProperties": False,
}


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env (or your Railway secrets) to enable grounded research."
        )
    return anthropic.Anthropic()


def _strip_to_json(text: str) -> str:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def research_player(name: str, positions: str, decision_context: str,
                    client: anthropic.Anthropic | None = None) -> dict:
    """Run grounded research on one player. Returns parsed JSON dict, or a
    fallback {verifiable:false, notes:...} on failure."""
    today = date.today()
    cutoff = today - timedelta(days=30)
    month_year = today.strftime("%B %Y")

    user_prompt = (
        f"PLAYER: {name}\n"
        f"POSITION(S): {positions or 'unknown'}\n"
        f"DECISION CONTEXT: {decision_context}\n"
        f"TODAY: {today.isoformat()}\n"
        f"CONSIDER ONLY SOURCES PUBLISHED ON OR AFTER: {cutoff.isoformat()}\n"
        f"CURRENT MONTH: {month_year}\n\n"
        "Research this player using web_search and return the JSON object per the schema."
    )

    cli = client or _client()
    log.info("Researching %s (%s)", name, decision_context[:60])

    try:
        response = cli.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": RESEARCH_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": 6,
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": RESEARCH_SCHEMA}},
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIError as e:
        log.warning("research API error for %s: %s", name, e)
        return {"name": name, "verifiable": False, "notes": f"api error: {e}"}
    except Exception as e:
        log.exception("research failed for %s", name)
        return {"name": name, "verifiable": False, "notes": f"exception: {e}"}

    if response.stop_reason == "refusal":
        return {"name": name, "verifiable": False, "notes": "model refused"}

    # Pull the first text block — output_config guarantees valid JSON
    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if not text_block:
        return {"name": name, "verifiable": False, "notes": "no text block in response"}

    try:
        data = json.loads(_strip_to_json(text_block))
    except json.JSONDecodeError as e:
        log.warning("JSON parse failed for %s: %s; raw=%s", name, e, text_block[:300])
        return {"name": name, "verifiable": False, "notes": "json parse failed",
                "raw": text_block[:1000]}

    # Sanity check: every finding URL must be in sources
    sources_set = {s.get("url") for s in data.get("sources") or []}
    for f in data.get("findings") or []:
        url = f.get("source_url")
        if url and url not in sources_set:
            f["citation_warning"] = "source not in sources list"

    # Surface usage info for cost tracking
    usage = response.usage
    if usage:
        log.info(
            "research[%s] usage: input=%d cache_read=%d cache_create=%d output=%d",
            name,
            getattr(usage, "input_tokens", 0),
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0),
        )

    return data


def research_decision_set(decisions: list[dict]) -> list[dict]:
    """Research each player named in each decision. Mutates decisions in
    place by attaching `.research` to each player entry."""
    cli = _client()
    out = []
    for d in decisions:
        researched_players = []
        for p in d.get("players") or []:
            ctx = f"{d.get('action','')}. Player role: {p.get('role','')}."
            research = research_player(
                p.get("name", ""),
                p.get("positions", ""),
                ctx,
                client=cli,
            )
            researched_players.append({**p, "research": research})
        out.append({**d, "players": researched_players})
    return out
