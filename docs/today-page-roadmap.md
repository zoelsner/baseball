# Today page — Tier 3 roadmap

The Today landing page (`V2Today` in `web/sandlot/v2-pages.jsx`) was redesigned
to answer three daily-driver questions: *am I winning my matchup, what do I
need to do today, and what changed since last visit?* Tier 1+2 (matchup donut,
"In action / Idle / Injured" lineup snapshot, week-over-week rank chip) shipped
together — this doc captures the higher-effort follow-ups so we can pick them
up when there's time, without re-deriving the design.

## Tier 3 — bigger payoff, more code

### A. Matchup category breakdown

**Why it matters.** This is the single most valuable thing Today can show in an
H2H league. *"Up in HR/AVG/OBP, trailing in SB/ERA/WHIP/K"* tells you exactly
which roster moves matter this week.

**Current state.** `extract_matchup` (`fantrax_data.py:213-282`) only stores
aggregate `my_score` / `opponent_score` / `margin` / `period_*`. The per-category
rows that Fantrax shows in its scoreboard view are not parsed.

**Work required.**
1. Inspect the `scoring_period_results()` payload for one period and confirm
   whether per-category breakdowns are nested in `matchup.away`/`matchup.home`
   or fetched via a separate endpoint. If separate, find the wrapper method
   in `fantraxapi` (Skipper deep-matchup uses it for opponent rosters).
2. Extend `extract_matchup` to return a `categories: [{ key, my, opp, leader }]`
   list. Use the same names Fantrax does (e.g. `R, HR, RBI, SB, AVG, K, ERA, WHIP`).
3. Surface the list in `_snapshot_payload` (already proxies `matchup` whole).
4. Render a new "Category breakdown" sub-block inside the matchup card on
   Today: a 2-column grid of `cat | my vs opp | who leads`. Reuse the
   donut for the headline; the categories live below as a roll-up.
5. Reuse this in Skipper's "Weekly matchup assessment" preset — the LLM does
   better with structured category data than aggregate scores.

**Estimate.** 1-2 hours including verifying field names against a real
response.

### B. Skipper insight of the day

**Why it matters.** A 1-2 line "what's interesting about your roster right now"
card makes Today feel intelligent rather than informational. We already pay
to maintain warm player takes — we should surface one.

**Current state.** `player_service._load_or_generate_take` writes to the
`player_takes` table keyed by `(player_id, snapshot_id)`. There's no curation
or "most interesting" selection.

**Work required.**
1. **Pick the player without an LLM call.** The cheapest signal is fpts delta
   vs. the player's season `fppg`: any starter whose last 7 game logs sum to
   significantly less (or more) than `7 × fppg` is interesting. Game logs are
   already cached in `mlb_stats.fetch_game_log` and stored via
   `sandlot_db.set_player_game_log`.
2. New helper `player_service.pick_insight_player(snapshot_id)` returns
   `{ player_id, headline_metric }` based on largest absolute fpts deviation
   over the last 7 days. Tie-break by snapshot order (deterministic across
   page loads on the same snapshot).
3. Surface the picked take's first sentence + the player's name. The full
   take is one tap away in `V2PlayerSheet`.
4. Add a new `/api/insight/today` endpoint (returns `{ player_id, name,
   headline, first_sentence }`) so the Today page can fetch it independently
   of `/api/snapshot/latest`. Keep the snapshot endpoint cheap.
5. Render a new "Skipper take" card on Today between Lineup and Standing.

**Estimate.** ~2 hours. Risk: picking by raw fpts delta surfaces noise (one
4-RBI game inflates the metric); may need a "minimum N games" filter.

## Possible Tier 4

- **Real position-strength panel.** The fake one was deleted in
  `62ae4fc`. A real version uses `all_team_rosters[*].rows[*].fpts` grouped by
  `slot`/`positions` to compute *"You're 3rd-best at SS, 11th at OF."* All data
  is already in the snapshot.
- **Probable pitcher / first-pitch time.** Extends the lineup-today card into
  *"4 in action — first pitch 7:05 vs LAD (Snell)."* Requires reading
  per-game `probablePitcher` from the MLB schedule endpoint, plus per-player
  game-time joining.
- **Recent transactions strip.** `extract_transactions` already runs every
  refresh (`fantrax_data.py:285`). Show the last 3 league moves on Today —
  cheap, social, helps engagement.

## Files to remember

- `mlb_stats.games_today_team_abbrs` — schedule helper added in Tier 2; reuse
  for Tier 4 first-pitch enrichment.
- `sandlot_db.snapshot_from_days_ago` — added in Tier 2; reusable for any
  future week-over-week diff.
- `_snapshot_payload` (`sandlot_api.py`) is the right place to add new
  derived fields; per-card endpoints (`/api/insight/today`) are right for
  expensive computes you don't want on every page load.
