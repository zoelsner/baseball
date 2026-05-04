"""Decision engine: narrows the universe of possible roster moves down to a
short list of high-impact decisions, then asks Claude to synthesize a final
TL;DR + detailed-findings report using the research package as evidence.

Inputs:
  - my_roster (rows from fantrax_data.extract_roster, hydrated with pybaseball)
  - all_team_rosters (from fantrax_data.extract_all_team_rosters, hydrated)
  - free_agents
  - league_rules
  - transactions

The narrowing logic is pure Python — no LLM. Once we have the candidate set,
research_layer fetches grounded info and Claude synthesizes the report
via the Anthropic SDK with prompt caching on the synthesis instructions.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

import anthropic

log = logging.getLogger(__name__)

SYNTHESIS_MODEL = "claude-opus-4-7"
SYNTHESIS_MAX_TOKENS = 8192


def _to_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def positional_strength(team_rosters: dict[str, dict]) -> dict[str, dict[str, float]]:
    """Returns {team_id: {position_short: avg_fppg}} so we can identify
    league-wide mismatches."""
    out: dict[str, dict[str, float]] = {}
    for tid, tdata in team_rosters.items():
        rows = tdata.get("rows") or []
        by_pos: dict[str, list[float]] = defaultdict(list)
        for p in rows:
            fppg = _to_float(p.get("fppg"))
            if fppg <= 0:
                continue
            for pos in (p.get("all_positions") or [p.get("positions")] or []):
                if not pos:
                    continue
                pos_str = str(pos).strip()
                if pos_str in ("Hit", "Pit", "All", ""):
                    continue
                by_pos[pos_str].append(fppg)
        out[tid] = {pos: sum(vs) / len(vs) for pos, vs in by_pos.items()}
    return out


def identify_drop_candidates(my_roster_rows: list[dict], top_k: int = 4) -> list[dict]:
    """Pick the worst-performing rosterable players, but exclude clearly young
    upside plays — dynasty context. Returns up to top_k candidates."""
    candidates = []
    for p in my_roster_rows:
        fppg = _to_float(p.get("fppg"))
        age = p.get("age")
        # Dynasty exclusions: don't suggest dropping young players solely on
        # current FP/G. Let Claude decide if a 23yo is droppable; just don't
        # surface them automatically.
        try:
            age_int = int(age) if age is not None else None
        except (TypeError, ValueError):
            age_int = None
        if age_int is not None and age_int <= 24:
            continue
        # Need some FP/G data to evaluate
        if fppg <= 0:
            # could be injured / IR — handle separately, not as a drop cand
            continue
        candidates.append({
            "name": p.get("name"),
            "positions": p.get("positions") or "",
            "fppg": fppg,
            "age": age_int,
            "slot": p.get("slot"),
            "injury": p.get("injury"),
        })
    candidates.sort(key=lambda x: x["fppg"])
    return candidates[:top_k]


def identify_my_weakest_positions(my_roster_rows: list[dict], top_k: int = 4) -> list[str]:
    """Positions where my roster's average FP/G is weakest. Used to focus FA
    search."""
    by_pos: dict[str, list[float]] = defaultdict(list)
    for p in my_roster_rows:
        fppg = _to_float(p.get("fppg"))
        if fppg <= 0:
            continue
        # Use primary position only here for "my average at SS"
        pos = (p.get("positions") or "").split(",")[0].strip()
        if pos and pos not in ("Hit", "Pit", "All"):
            by_pos[pos].append(fppg)
    avgs = [(pos, sum(v) / len(v)) for pos, v in by_pos.items() if len(v) >= 1]
    avgs.sort(key=lambda x: x[1])
    return [pos for pos, _ in avgs[:top_k]]


def _score_fa(p: dict) -> float:
    """Extract a sortable score from a free agent's stats cells. Tries common
    column names; returns 0 if nothing usable is found."""
    stats = p.get("stats") or {}
    if not isinstance(stats, dict):
        return 0.0
    # Preferred ranking columns, in order
    for key in ("FP/G", "FPG", "FPts/G", "Score", "FPts", "ProjFPts", "FP"):
        v = stats.get(key)
        if v is None:
            continue
        try:
            return float(str(v).replace(",", "").strip())
        except (ValueError, TypeError):
            continue
    # Fallback: scan _cells for the largest numeric value (raw)
    cells = stats.get("_cells")
    if isinstance(cells, list):
        nums = []
        for c in cells:
            try:
                nums.append(float(str(c).replace(",", "").strip()))
            except (ValueError, TypeError):
                continue
        if nums:
            return max(nums)
    return 0.0


def identify_fa_targets(fa_players: list[dict], my_weak_positions: list[str],
                         top_k: int = 6) -> list[dict]:
    """For each weak position, surface the top-scoring FA(s) eligible there.
    Returns a flat list, deduped by player name, ordered by weakness priority.
    """
    if not fa_players:
        return []
    weak_set = [p.upper() for p in my_weak_positions]
    seen_names: set[str] = set()
    out: list[dict] = []

    for weak_pos in weak_set:
        # Find FAs eligible at this position
        candidates = []
        for p in fa_players:
            name = p.get("name")
            if not name or name in seen_names:
                continue
            pos_str = (p.get("positions") or "").upper()
            eligible = {tok.strip() for tok in pos_str.replace("/", ",").split(",") if tok.strip()}
            if weak_pos in eligible:
                candidates.append((p, _score_fa(p)))
        candidates.sort(key=lambda x: -x[1])
        for p, score in candidates[:2]:  # top 2 per weak position
            if p.get("name") in seen_names:
                continue
            out.append({
                "name": p.get("name"),
                "positions": p.get("positions"),
                "team": p.get("team"),
                "fills_position": weak_pos,
                "fa_score": score,
            })
            seen_names.add(p.get("name"))
            if len(out) >= top_k:
                return out
    return out


def identify_trade_paths(positional_strength_by_team: dict[str, dict[str, float]],
                          my_team_id: str, top_k: int = 3) -> list[dict]:
    """Find counterparties whose positional strengths are inverse to ours."""
    me = positional_strength_by_team.get(my_team_id) or {}
    if not me:
        return []
    paths = []
    for tid, them in positional_strength_by_team.items():
        if tid == my_team_id or not them:
            continue
        # For each position, my_strength - their_strength. Want some positions
        # where I'm strong + they're weak (I send), and some where the inverse
        # holds (I receive).
        i_strong_they_weak = []
        they_strong_i_weak = []
        for pos in set(me) | set(them):
            mine = me.get(pos, 0.0)
            theirs = them.get(pos, 0.0)
            if mine - theirs > 1.5:
                i_strong_they_weak.append((pos, mine, theirs, mine - theirs))
            if theirs - mine > 1.5:
                they_strong_i_weak.append((pos, theirs, mine, theirs - mine))
        if not (i_strong_they_weak and they_strong_i_weak):
            continue
        # score = best inverse mismatch
        i_strong_they_weak.sort(key=lambda x: -x[3])
        they_strong_i_weak.sort(key=lambda x: -x[3])
        score = i_strong_they_weak[0][3] + they_strong_i_weak[0][3]
        paths.append({
            "team_id": tid,
            "score": round(score, 2),
            "i_send_from": [{"pos": p, "my_avg": round(m, 2), "their_avg": round(t, 2)} for p, m, t, _ in i_strong_they_weak[:3]],
            "i_want_from": [{"pos": p, "their_avg": round(t, 2), "my_avg": round(m, 2)} for p, t, m, _ in they_strong_i_weak[:3]],
        })
    paths.sort(key=lambda x: -x["score"])
    return paths[:top_k]


def build_decision_set(snapshot: dict, max_research_players: int = 12) -> list[dict]:
    """Top-level: produces a structured list of decisions to research.
    Each decision names 1-2 players that need grounded research."""
    decisions: list[dict] = []
    roster = snapshot.get("roster") or {}
    my_rows = roster.get("rows") if isinstance(roster, dict) else []
    fa = snapshot.get("free_agents") or {}
    fa_players = fa.get("players") if isinstance(fa, dict) else []
    all_teams = snapshot.get("all_team_rosters") or {}
    my_team_id = snapshot.get("team_id")

    # --- Drop candidates ---
    drops = identify_drop_candidates(my_rows or [], top_k=4)
    for d in drops:
        decisions.append({
            "decision_id": f"drop_{d['name']}",
            "kind": "drop_candidate",
            "action": f"Evaluate dropping {d['name']} ({d['positions']}, {d['fppg']:.2f} FP/G)",
            "rationale_seed": f"FP/G is among lowest on roster ({d['fppg']:.2f}); age {d['age']}.",
            "players": [{
                "name": d["name"],
                "positions": d["positions"],
                "role": "current roster — drop candidate",
                "fppg": d["fppg"],
                "age": d["age"],
            }],
        })

    # --- FA pickups for weak positions ---
    weak_positions = identify_my_weakest_positions(my_rows or [], top_k=4)
    targets = identify_fa_targets(fa_players or [], weak_positions, top_k=4)
    for t in targets:
        decisions.append({
            "decision_id": f"add_{t['name']}",
            "kind": "fa_target",
            "action": f"Evaluate adding FA {t['name']} ({t['positions']}) — fills weak {t['fills_position']}",
            "rationale_seed": f"My roster is weak at {t['fills_position']}; this player is on waivers.",
            "players": [{
                "name": t["name"],
                "positions": t["positions"],
                "role": "free agent — pickup candidate",
            }],
        })

    # --- Trade paths ---
    if all_teams and my_team_id:
        strength = positional_strength(all_teams)
        paths = identify_trade_paths(strength, my_team_id, top_k=2)
        for path in paths:
            other_team = all_teams.get(path["team_id"]) or {}
            decisions.append({
                "decision_id": f"trade_{path['team_id']}",
                "kind": "trade_path",
                "action": f"Explore trade with {other_team.get('team_name', path['team_id'])} — positional mismatch (score {path['score']})",
                "rationale_seed": f"I'm strong at {[s['pos'] for s in path['i_send_from']]}, they're strong at {[s['pos'] for s in path['i_want_from']]}.",
                "players": [],  # trade paths research the team, not specific players
                "trade_path": path,
                "counterparty_roster": [
                    {"name": p.get("name"), "positions": p.get("positions"), "fppg": p.get("fppg")}
                    for p in (other_team.get("rows") or [])[:25]
                ],
            })

    # Cap how many we deep-research to keep cost/time bounded.
    # Trade paths last; drops/adds first since they're more actionable.
    capped = decisions[:max_research_players]
    log.info("Decision set: %d total, %d will be researched", len(decisions), len(capped))
    return capped


SYNTHESIS_SYSTEM = """You are writing a weekly fantasy baseball intel report for a smart but baseball-novice user. They want to make moves themselves but need confidence the recommendations are grounded in real, recent evidence — and they want SPECIFIC NAMES on both sides of every move, not vague advice like "add from the waiver wire."

The user provides a structured payload: snapshot of their roster + league context, plus pre-researched decision candidates. Each decision has `players[].research` containing fetched, cited, dated facts. USE ONLY THE FACTS IN `research`. Do not invent stats. Do not use training-data recall.

The decisions in the payload come in three kinds:
- `drop_candidate` — players on the user's roster who might be cut
- `fa_target` — specific free agents we've researched as pickup candidates, each tagged with `fills_position` (the user's weak spot it would address)
- `trade_path` — counterparties whose roster is positionally inverse to the user's

CRITICAL OUTPUT RULE: Every drop recommendation MUST be paired with a specific add recommendation drawn from the `fa_target` decisions in the payload. The pairing should match positions when possible (a dropped 3B → a `fa_target` with `fills_position: "3B"`). If no fa_target fits the position, say "drop X, no good replacement on waivers right now — hold the slot for a streamer" — but never recommend a generic "add from the waiver wire."

Produce the report in this exact structure:

# Weekly League Intel — <team_name> — <date>

## TL;DR
4-7 one-line recommendations, ranked by impact. Each swap recommendation MUST name both players. Each line:
- **[Action]** — [one-sentence why, naming both players] _(Confidence: High/Medium/Low)_

Examples of good lines (note: BOTH names always appear):
- **Drop Yandy Diaz, add Kyle Manzardo** — Diaz is 33 and showing -1.5 FP/G drop; Manzardo just took over everyday 1B in Cleveland (MLB.com 2026-04-23). _(Confidence: High)_
- **Drop Luis Rengifo for Brendan Donovan** — Rengifo is platoon-only at .173 OPS; Donovan is hitting .310 with 2B/3B/OF eligibility (Fangraphs 2026-04-25). _(Confidence: High)_
- **Hold Junior Caminero through May** — 21 yo, recent power surge in research (no swap needed). _(Confidence: Medium)_

If a decision can't be supported by the research (verifiable=false), DO NOT include it in TL;DR. Demote to "Decisions I couldn't verify" at bottom.

## Detailed Findings
For each TL;DR recommendation, write a sub-section with:

### [Action — same wording as TL;DR line]
- **Reasoning:** 2-3 sentence plain-English explanation. Use only facts from `research`.
- **Evidence:**
  - claim 1 — [Source title, date](url)
  - claim 2 — [Source title, date](url)
- **Risk:** what could change this view?
- **Confidence:** High / Medium / Low — and *why*.

## Decisions I Couldn't Verify
Bullet list of decision_ids where research failed (verifiable=false), with the reason. The user should know what we tried and why it didn't pan out.

## How Your League Works
One short paragraph explaining the scoring format from `league_rules`. If unavailable, say so.

## Sources
Numbered list of every URL referenced in this report, grouped by player. Format:
- [Source title — publisher, date](url)

RULES:
- NO emojis.
- Plain English. Define any stat term you use ("xFIP measures pitcher skill independent of luck").
- Real names from the snapshot. Real numbers cited from `research` only.
- Total length: 800-1200 words.
- Every TL;DR action must have a Detailed Findings sub-section. Every Detailed Findings claim must have at least one cited source from `research.findings` or `research.sources`."""


def synthesize_report(snapshot: dict, researched_decisions: list[dict]) -> str:
    """Final pass: ask Claude to write the user-facing report from the
    pre-researched decisions. The synthesis itself doesn't search — it only
    consumes the structured research."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "_Synthesis skipped: ANTHROPIC_API_KEY not set._"

    payload = {
        "team_name": snapshot.get("team_name"),
        "date": snapshot.get("timestamp", "")[:10],
        "league_rules": snapshot.get("league_rules"),
        "my_roster_summary": _slim_my_roster(snapshot),
        "decisions": researched_decisions,
    }
    payload_json = json.dumps(payload, default=str, indent=2)
    if len(payload_json) > 200_000:
        log.warning("Synthesis payload very large (%d bytes); trimming", len(payload_json))
        for d in payload["decisions"]:
            for p in d.get("players") or []:
                r = p.get("research") or {}
                p["research"] = {
                    "summary": r.get("summary"),
                    "status": r.get("status"),
                    "current_role": r.get("current_role"),
                    "trend": r.get("trend"),
                    "findings": r.get("findings"),
                    "sources": r.get("sources"),
                    "verifiable": r.get("verifiable"),
                    "notes": r.get("notes"),
                }
        payload_json = json.dumps(payload, default=str, indent=2)

    user_message = (
        f"PAYLOAD:\n```json\n{payload_json}\n```\n\n"
        "Produce the weekly intel report for the user, exactly per the structure "
        "and rules in your instructions. Cite sources from `research` only."
    )

    client = anthropic.Anthropic()
    try:
        with client.messages.stream(
            model=SYNTHESIS_MODEL,
            max_tokens=SYNTHESIS_MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYNTHESIS_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            final = stream.get_final_message()
    except anthropic.APIError as e:
        log.error("synthesis API error: %s", e)
        return f"_Synthesis API error: {e}_"
    except Exception as e:
        log.exception("synthesis failed")
        return f"_Synthesis error: {e}_"

    if final.stop_reason == "refusal":
        return "_Synthesis: model declined._"

    text = "".join(b.text for b in final.content if b.type == "text").strip()

    usage = final.usage
    if usage:
        log.info(
            "synthesis usage: input=%d cache_read=%d cache_create=%d output=%d",
            getattr(usage, "input_tokens", 0),
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0),
        )

    return text or "_Synthesis: empty response._"


def _slim_my_roster(snapshot: dict) -> list[dict]:
    roster = snapshot.get("roster") or {}
    rows = roster.get("rows") if isinstance(roster, dict) else []
    return [
        {
            "name": p.get("name"),
            "slot": p.get("slot"),
            "positions": p.get("positions"),
            "team": p.get("team"),
            "fppg": p.get("fppg"),
            "fpts": p.get("fpts"),
            "age": p.get("age"),
            "injury": p.get("injury"),
            "mlb_stats": p.get("mlb_stats"),
        }
        for p in rows or []
    ]
