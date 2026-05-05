# Sandlot Waiver Swaps + Cached Skipper Brief Plan

## Summary

Build the next Sandlot slice around waiver swaps, not full trades. The Adds tab becomes a ranked, scrollable board of "Add X, move out Y" recommendations. Ranking and net value are deterministic and eval-able. AI only explains the already-ranked swap and writes a cached Skipper refresh brief.

Keep three concepts separate:

- Player brief: explains one player on the player card.
- Waiver swap analysis: explains the net value of replacing one roster player with one free agent.
- Skipper refresh brief: summarizes the latest snapshot after a Fantrax refresh.

No full trade matching, no trade partner analysis, no player-to-player trade comparison, and no Fantrax write actions.

## Product Behavior

### Adds Tab

The Adds tab should load `GET /api/waiver-swaps/latest` and render the top 8 waiver swap cards.

Each card represents:

- one free agent to consider adding
- one roster player to move out, bench, drop, or deprioritize
- deterministic net value
- AI or deterministic explanation
- risk and dynasty note
- evidence chips

Recommended card copy:

- Primary action: `Review swap`
- Avoid: `Apply`, `Add & drop`, `Execute`

This keeps the app read-only and prevents implying that Sandlot can perform Fantrax actions.

### Skipper Tab

The Skipper tab should start with a cached refresh brief when available.

The brief should answer:

- biggest roster issue from the latest scrape
- best waiver swap to inspect
- strongest reason to hold off on a risky swap
- one scoring or roster-shape insight worth learning

The existing chat can remain below the brief.

### Player Card

Do not change player-card take behavior in this slice except to keep the mental model clear. Player-card takes remain single-player summaries. They should not explain waiver swaps.

## Deterministic Waiver Engine

Create a dedicated module, recommended name: `sandlot_waivers.py`.

Inputs:

- latest successful snapshot from `sandlot_db.latest_successful_snapshot()`
- `snapshot["roster"]["rows"]`
- `snapshot["free_agents"]["players"]`
- snapshot freshness metadata from the snapshot row

Output:

```json
{
  "snapshot_id": 123,
  "taken_at": "2026-05-04T...",
  "freshness": {"state": "fresh", "age_minutes": 4},
  "cards": [],
  "brief": {"state": "missing", "text": null, "model": null, "generated_at": null}
}
```

### Candidate Adds

Free-agent candidates come from `free_agents.players`.

Compute `add_fpg` by preferring these stat keys, in order:

- `FP/G`
- `FPG`
- `FPts/G`
- `FP/Gm`
- `Avg`

If none are available, fall back to:

- `Score`
- `FPts`
- `ProjFPts`
- `FP`
- largest numeric value in `_cells`

Fallback scores should lower confidence because they may not be true FP/G.

### Move-Out Candidates

Move-out candidates come from:

- all bench players
- injured, DTD, OUT, IL, or IR players
- weak starters when the free agent shares position eligibility or fills the same roster need

Use `fppg` from roster rows as `move_out_fpg`.

Young or unknown-age players are not hard-excluded. They receive a dynasty warning.

### Position Matching

Normalize positions by splitting on `/`, `,`, and whitespace where needed.

Treat these as position groups:

- hitters: `C`, `1B`, `2B`, `3B`, `SS`, `OF`, `UT`
- pitchers: `SP`, `RP`, `P`

Eligibility is a direct match when the add and move-out player share at least one specific position.

Loose fit is allowed when:

- the add fills one of the user's weakest positions
- the move-out player is a bench, injured, or low-value player
- the add is meaningfully higher value

Loose fit should reduce confidence.

### Weak Positions

Compute weak positions from roster FP/G by position.

For each roster player:

- skip rows without usable FP/G
- ignore generic positions like `Hit`, `Pit`, `All`
- include all known eligible positions when available

Sort positions by average FP/G ascending. Use the bottom 3 as weak positions.

### Scoring

For every add/move-out pair:

```text
net_delta = add_fpg - move_out_fpg
```

Base sort score:

```text
sort_score = net_delta
```

Boosts:

- `+1.0` if add fills a weak position
- `+0.7` if positions directly match
- `+0.5` if move-out player is bench
- `+0.8` if move-out player is injured, DTD, OUT, IL, or IR

Penalties:

- `-0.7` if fit is loose
- `-0.8` if add value is a fallback score rather than true FP/G
- `-0.5` if move-out age is missing
- `-1.0` if move-out age is 24 or younger

Hide cards with `net_delta <= 0` unless the move-out player has an injury/status issue and the add has a usable positive score.

Deduping:

- one card per add player
- one card per move-out player in the final top 8 when possible
- if duplicates are unavoidable, prefer higher `sort_score`

### Confidence

High:

- `net_delta >= 1.5`
- direct position fit or weak-position fit
- add score is true FP/G
- no dynasty warning

Medium:

- `net_delta > 0`
- useful fit but some uncertainty exists
- examples: fallback score, loose fit, missing age, or mild dynasty warning

Low:

- inspect-worthy but noisy
- only show if still inside top 8 after filtering and sorting

### Card Shape

Each card should include:

```json
{
  "id": "waiver:<snapshot_id>:<add_id>:<move_out_id>",
  "rank": 1,
  "add": {
    "id": "fantrax_or_fa_id",
    "name": "Player Name",
    "team": "LAD",
    "positions": "2B/OF",
    "age": 25,
    "fpg": 7.2,
    "score_source": "FP/G"
  },
  "move_out": {
    "id": "fantrax_id",
    "name": "Roster Player",
    "team": "BOS",
    "positions": "2B",
    "slot": "BN",
    "age": 28,
    "fpg": 5.1,
    "injury": null
  },
  "net_delta": 2.1,
  "sort_score": 3.8,
  "fills_position": "2B",
  "fit": "direct",
  "confidence": "High",
  "why": "Deterministic explanation or cached AI explanation.",
  "risk": "Short risk statement.",
  "dynasty_note": "No major dynasty concern.",
  "evidence_chips": ["+2.1 FP/G", "2B fit", "Bench move-out"]
}
```

## AI Cache And Explanation Layer

Add a generic AI cache table in `sandlot_db.py`.

Recommended schema:

```sql
CREATE TABLE IF NOT EXISTS ai_briefs (
  snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  brief_type TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  text TEXT NOT NULL,
  model TEXT NOT NULL,
  input_hash TEXT,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_id, brief_type, subject_key)
);
```

Brief types:

- `waiver_swap`
- `refresh_brief`

Helpers:

- `get_ai_brief(snapshot_id, brief_type, subject_key)`
- `set_ai_brief(snapshot_id, brief_type, subject_key, text, model, input_hash=None)`

### AI Rules

AI may explain only. It must not rank, reorder, choose players, invent stats, or change net delta.

Use strict JSON context for each prompt:

- snapshot id and timestamp
- top deterministic cards
- card ids
- add and move-out player facts
- net delta
- confidence
- evidence chips
- dynasty note

Waiver swap explanation output should be short:

- why: 1 sentence
- risk: 1 sentence
- no markdown
- no stats beyond supplied facts

Refresh brief output should be:

- 3 to 5 bullets
- plain English
- can cite the top waiver swap
- can mention dynasty caveats
- no invented news or current events

### Model Config

Update `sandlot_skipper.py` so model ids can be controlled by env vars:

```bash
SANDLOT_AI_MODEL_PRIMARY=moonshotai/kimi-k2
SANDLOT_AI_MODEL_FALLBACK=tencent/hy3-preview:free
```

This lets Railway try cheap models such as DeepSeek without code changes.

## API Changes

Add:

```http
GET /api/waiver-swaps/latest
```

Behavior:

- returns deterministic cards immediately
- overlays cached AI explanations when available
- returns `brief.state = "ready"` when cached refresh brief exists
- returns `brief.state = "missing"` or `"pending"` when not cached
- does not call OpenRouter inline
- if no snapshot exists, return 404 with clear detail
- if FA pool is missing, return `cards: []` with a clear message

Suggested response:

```json
{
  "snapshot_id": 123,
  "taken_at": "2026-05-04T...",
  "freshness": {"state": "fresh", "age_minutes": 4},
  "cards": [],
  "brief": {
    "state": "ready",
    "text": "...",
    "model": "deepseek/...",
    "generated_at": "2026-05-04T..."
  },
  "message": null
}
```

## Refresh And Cron Warmup

After successful manual refresh:

- return the snapshot response normally
- schedule best-effort background warm for waiver AI cache

After successful cron refresh:

- keep current profile warm behavior
- also warm waiver AI cache

Warm function:

```python
warm_latest_waiver_ai(snapshot_id: int | None = None, limit: int = 8) -> dict
```

It should:

- build deterministic cards
- generate missing per-card explanations for top cards
- generate missing refresh brief
- catch and log errors
- never fail the refresh or cron job

## Frontend Changes

### Navigation

Reorder bottom nav:

1. Today
2. Roster
3. Adds
4. Skipper
5. Trade
6. League

### Adds Page

Replace mock `FREE_AGENTS` rendering with API data.

States:

- loading
- real cards
- no FA pool
- no positive swaps
- API error with mock fallback only on `file://`

Card layout should stay in the current Sandlot visual language:

- cream surface
- serif player names
- compact evidence chips
- one primary `Review swap` button
- optional secondary `Open player` actions if easy

### Skipper Page

At the top of the Skipper page, render the cached refresh brief if present.

If missing:

- show a compact state: `Brief will appear after the next refresh.`

Keep chat below the brief.

## Tests

Backend checks:

- Python compile for changed modules.
- Same snapshot produces same top 8 card order.
- Every card has exactly one add and one move-out player.
- `net_delta` equals visible `add.fpg - move_out.fpg`.
- Young move-out players get `dynasty_note`, not silent exclusion.
- Non-positive swaps are hidden unless move-out status justifies inspection.
- Missing FA pool returns empty cards and clear message.
- AI cache miss does not fail the API.
- AI failure does not fail refresh or cron.

Frontend checks:

- Adds page renders top 8 real cards from API.
- Adds page does not use mock cards when API succeeds.
- Skipper tab shows cached refresh brief when ready.
- Tab order is correct.
- No button says or implies automatic execution.

Eval checks:

- AI explanation only names players present in the card payload.
- AI explanation preserves deterministic net delta and confidence.
- AI brief cites only supplied deterministic findings.
- Repeated calls for the same snapshot do not change rankings.

## Out Of Scope

- Full trade matching.
- Trade partner analysis.
- Player-to-player trade comparisons.
- Fantrax add/drop/lineup/trade execution.
- X/Twitter current-events integration.
- Roster next-game context, unless already available in snapshot data.

## Acceptance Criteria

- `GET /api/waiver-swaps/latest` returns deterministic top 8 waiver swaps from the latest Fantrax snapshot.
- Adds tab shows real waiver swap cards.
- Skipper tab can show a cached refresh brief.
- Manual refresh and cron do not fail if AI generation fails.
- Rankings are stable and explainable without AI.
- Secrets remain in `.env` or Railway variables only.
