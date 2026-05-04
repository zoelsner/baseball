"""Anthropic SDK wrapper for the daily audit analysis.

Builds a structured prompt from the daily snapshot, calls Claude Opus 4.7
with adaptive thinking + prompt caching on the (large, stable) instructions,
and returns the analysis as markdown text.
"""

from __future__ import annotations

import json
import logging
import os

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 4096

PROMPT_TEMPLATE = """You are coaching a smart but baseball-novice user who is trying to win their MLB dynasty fantasy league. They don't know the sport deeply. They want plain-English, actionable recommendations - not analyst jargon.

Below is today's snapshot of their team and league. Produce a concise markdown report with these sections in this exact order:

## How Your League Works
One short paragraph. If `league_rules` is in the snapshot, summarize the scoring format and roster construction in plain English (e.g. "Head-to-head points league. You face one opponent per week. Hits = 1pt, HR = 4pts..."). If `league_rules` is missing, infer from the data shape (FP/G implies a points league) and say so.

## This Week's Moves
The most actionable section. Each recommendation must follow this exact shape:
- **Action:** [Drop X, add Y] / [Start X over Y] / [Move X to IR] - in one line
- **Why:** plain English, no jargon. Explain the gap being filled.
- **Risk:** one line on what could go wrong.
- **Confidence:** High / Medium / Low.

Aim for 3-6 recommendations. Rank by impact. If the FA pool is unavailable, say so once and pivot to internal moves (slot changes, IR moves) only.

## Drop Candidates (Roster Bottom)
Two to four weakest players on the user's roster, ranked weakest first. For each: name, FP/G, **age trajectory** (e.g. "29 yo - past peak", "23 yo - on the come-up", "32 yo - aging out"), and one-line reasoning. Dynasty context: a young player with upside is almost never a drop even if their current FP/G is low. Say so explicitly when you spare a young player from the cut list.

## Players On The Come-Up (Hold or Acquire)
2-3 names. Why their arrow is pointing up: age, recent FP/G trend, role change, prospect status. Whether to hold (if owned) or acquire (if not).

## Trade Paths
If we don't have other teams' rosters in this snapshot, say "Need league-wide roster data — coming in Phase 2" and skip. Otherwise: 1-2 specific trade ideas with target team name and the positional mismatch you'd exploit.

## Standings Reality Check
One short paragraph. Where they sit, gap to playoffs, gap to first. Push, hold, or rebuild call given dynasty context.

Rules:
- NO EMOJIS. No throat-clearing intro or outro.
- Plain English. If you must use a stat name, briefly define it ("xFIP measures pitcher skill independent of luck").
- Real names from the snapshot. Real numbers cited.
- If data is missing for a section, say "Data unavailable" and move on - do not invent.
- Total length under 700 words.

---SNAPSHOT---
{snapshot_json}
---END SNAPSHOT---
"""

MAX_PROMPT_BYTES = 180_000


def _trim_for_prompt(snapshot: dict) -> dict:
    """Strip the verbose `raw` blobs from roster rows; trim FA pool if huge."""
    trimmed = json.loads(json.dumps(snapshot, default=str))
    roster = trimmed.get("roster")
    if isinstance(roster, dict):
        for player in roster.get("rows") or []:
            player.pop("raw", None)
    elif isinstance(roster, list):
        for player in roster:
            player.pop("raw", None)
    fa = trimmed.get("free_agents")
    if fa and isinstance(fa, dict):
        players = fa.get("players") or []
        if len(players) > 60:
            fa["players"] = players[:60]
            fa["truncated_to"] = 60
    txns = trimmed.get("transactions")
    if isinstance(txns, list) and len(txns) > 30:
        trimmed["transactions"] = txns[:30]
    return trimmed


def analyze(snapshot: dict) -> str:
    """Run the daily audit analysis. Returns markdown."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "_Daily analysis skipped: ANTHROPIC_API_KEY not set._"

    trimmed = _trim_for_prompt(snapshot)
    snapshot_json = json.dumps(trimmed, default=str, indent=2)
    if len(snapshot_json) > MAX_PROMPT_BYTES:
        if trimmed.get("free_agents"):
            trimmed["free_agents"] = {
                "note": "omitted: prompt too large",
                "method": trimmed["free_agents"].get("method"),
            }
        snapshot_json = json.dumps(trimmed, default=str, indent=2)

    user_prompt = (
        "SNAPSHOT (JSON):\n```json\n" + snapshot_json + "\n```\n\n"
        "Produce the daily audit report following the rules above."
    )

    client = anthropic.Anthropic()
    try:
        # Stream so long synthesis doesn't hit timeouts; collect the final
        # message at the end.
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": PROMPT_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            final = stream.get_final_message()
    except anthropic.APIError as e:
        log.error("daily analysis API error: %s", e)
        return f"_Daily analysis failed: {e}_"
    except Exception as e:
        log.exception("daily analysis failed")
        return f"_Daily analysis error: {e}_"

    if final.stop_reason == "refusal":
        return "_Daily analysis: model declined to produce a response._"

    text = "".join(b.text for b in final.content if b.type == "text").strip()
    return text or "_Daily analysis: empty response._"
