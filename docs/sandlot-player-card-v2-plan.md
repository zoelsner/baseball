# Player card v2 — Skipper-forward + headshots + data foundation

## Context

We just shipped the converged player sheet (Skipper chat link → Roster → live `/api/player/{id}` data). After looking at the four side-by-side mocks, the user wants to evolve the layout in this direction:

- **Hero + Skipper take side-by-side** (left = avatar/name/team/pos/age/status; right = a vertical Skipper take). The combined section grows to compensate. This lives in the upper portion of the sheet.
- **Real player headshots** instead of the initials avatar.
- **Trend section keeps the L7 / L30 / vs Exp KPI row** and pairs it with the existing last-14 fantasy-points bar chart, plus a small directional trend arrow (↑ / → / ↓) on the chart eyebrow. The redundant "4.50 FP/G from X season avg" big-number block goes away.
- **Recent games becomes an accordion** — only the most recent game shows by default, an arrow expands the rest in place.
- **Drop splits from the inline card** for now. They'll live behind a deeper-view click-through later.

Separately, the user asked me to think through (a) whether the app should expect daily attention or weekly attention and (b) how to start aggregating real player data. My take and a phased plan are below.

## Cadence — what we're designing for

User's intuition is right: most fantasy-baseball managers in dynasty-style leagues check in **once or twice a week**, not every day. Designing for daily power-users is the wrong target. So:

- **Skipper does the per-day analysis FOR you.** The take is auto-generated per player, roster-aware, so a week's worth of judgment is condensed to "open the sheet, read 2 sentences."
- **Background data stays current daily** so when the user does check in, it's fresh.
- Later, a "Today" surface can nudge the user the few days a week where attention actually pays off (matchup-driven swap suggestions). That's where Phase 2 below pays off — but it isn't part of this PR.

## Phase 1 — UI + Skipper-take backend (this PR)

### Implementation status

Implemented in this pass:

- `web/sandlot/atoms.jsx` now exports `PlayerPhoto`, which uses MLB headshots when an MLB id is resolved and falls back to the existing initials avatar.
- `web/sandlot/v2-pages.jsx` now renders the player sheet as a compact photo + identity column beside a roster-aware Skipper take.
- The trend card now shows L7, L30, and vs Exp, paired with the last-14 fantasy-points bar chart and a directional arrow.
- The game log now defaults to the most recent game and expands up to 14 rows in place.
- `sandlot_db.py` now creates `player_takes` and exposes `get_player_take` / `set_player_take`.
- `sandlot_skipper.py` now has `SkipperClient.complete()` for non-streaming OpenRouter completions.
- `player_service.py` now includes `take` in `/api/player/{id}` payloads, caches takes by `(player_id, snapshot_id)`, and degrades gracefully if OpenRouter is unavailable.
- Player profile loading is now layered: normal `GET /api/player/{id}` is cache-only and returns Fantrax/cached Postgres data immediately; `/refresh`, background warm tasks, and cron do the expensive MLB/OpenRouter work.
- `player_media` stores MLB game-content media found from recent game logs. The profile payload returns `media.items` from cache first, so the Media Scout section can show clips when available without slowing initial sheet open.
- `sandlot_cron.py` now pre-warms roster player profiles after each successful Fantrax snapshot. By default it warms MLB ids, game logs, and media; Skipper take pre-generation is opt-in via `SANDLOT_PROFILE_WARM_TAKES=1`.

Validation completed locally:

- Python compile: `sandlot_db.py`, `sandlot_skipper.py`, `player_service.py`, `sandlot_api.py`
- Diff whitespace check: `git diff --check`
- FastAPI health smoke test without `DATABASE_URL`: returns degraded `200`
- Take-prompt helper smoke test: builds two-message request with target player context

Still needs browser/preview validation against a real Railway snapshot:

- Real `/api/player/{id}` cache-only timing, background warm behavior, and `/api/player/{id}/refresh` timing
- Headshot happy path and fallback path
- Game-log/stat accordion interaction
- MLB media availability for players with recent highlight clips
- Full sheet visual pass on the in-app browser

### Frontend (`web/sandlot/v2-pages.jsx`)

1. **New combined hero/take card** — replaces the current `V2ProfileHero`. Layout: a single card with two columns.
   - Left column (~55% width on phone): bigger headshot (~76 px), name, "TEAM · POS · age N", status + ownership pills.
   - Right column (~45% width, vertical divider via `border-left`): eyebrow `SKIPPER TAKE` (accent color), then 2-3 sentences. While `take` is loading, render a 3-line skeleton.
   - Falls back to a stacked layout (left over right) only if a sheet narrower than ~360 px is ever used; phones are wider so single row is fine.
2. **Headshot component** — new `<PlayerPhoto>` atom in `atoms.jsx`. Props: `mlbId`, `name`, `size`. Renders an `<img>` pointing at `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/${mlbId}/headshot/67/current`. On `onError` or when `mlbId` is missing, swaps to the existing `Avatar` (initials). Add `PlayerPhoto` to the `Object.assign(window, ...)` export at the bottom of `atoms.jsx`.
3. **Trend section** — replace `V2ProfileTrend`'s content with a single combined card containing two pieces:
   - a 3-cell KPI strip: **L7 · L30 · vs Exp**. Compute L7 and L30 client-side from `data.games` (mean of `fpts_estimated` over the most recent 7 and 30 entries). For "vs Exp", reuse the existing `data.trend.pct_change` (already server-computed) — render as `+X.X%` colored green / red / muted via `data.trend.direction`. If any value is unavailable, render `—`.
   - the existing last-14 fantasy-points bar chart (`V2ProfileSparkline` is fine), with a directional arrow added to the eyebrow row: render `↑` (V2.ok) / `→` (V2.muted) / `↓` (V2.bad) driven by `data.trend.direction`. One small inline SVG, no new tokens.
   The redundant big-number block (`4.50 FP/G from 4.50 season avg` + percent pill) is removed; the KPI strip + arrow carries the same information more compactly.
4. **Recent games accordion** — replace `V2ProfileGameLog`. Default state shows only the most recent game (top row from the existing sorted list). A chevron button on the eyebrow row toggles `expanded`. When `expanded`, render the next 13 rows below, pushing remaining sheet content down naturally. Use the existing row markup; just slice the array. Local `React.useState(false)` for `expanded`.
5. **Drop the inline splits view.** The current sheet doesn't render splits, so this is just a "don't add them" decision. Future deeper-view will live elsewhere.

### Backend — Skipper take

6. **New table `player_takes`** in `sandlot_db.py` (`init_schema`):
   ```sql
   CREATE TABLE IF NOT EXISTS player_takes (
     player_id TEXT NOT NULL,
     snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
     text TEXT NOT NULL,
     model TEXT NOT NULL,
     generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
     PRIMARY KEY (player_id, snapshot_id)
   );
   ```
7. **New helpers** in `sandlot_db.py`: `get_player_take(player_id, snapshot_id)` and `set_player_take(player_id, snapshot_id, text, model)`.
8. **Add `complete(messages, *, max_tokens)` non-streaming method** to `SkipperClient` in `sandlot_skipper.py`. Same primary→fallback pattern as `stream()`, but returns a single `(text, model)` tuple. Re-uses the existing OpenRouter client.
9. **Extend `/api/player/{id}` response** to include a `take` field. Current behavior:
   - `GET /api/player/{id}` looks up `(player_id, latest_snapshot_id)` in `player_takes` and returns the cached take if present. It does not generate a take inline.
   - `POST /api/player/{id}/refresh` can generate/cache a take while also refreshing MLB profile data.
   - On Skipper failure, return `take: { text: null, error: "..." }` and let the frontend render an "unavailable" line so the rest of the card still works.
10. **Take prompt** — assembles 3 inputs:
    - Target player's data (name, team, pos, slot, recent FP/G, season totals from the existing `/api/player/{id}` payload).
    - User's roster summary (positions covered, depth chart for the player's position) — pulled from the same snapshot.
    - System message borrows tone/instructions from the Skipper chat system prompt so voice stays consistent.
    Output: 2-3 sentences. `temperature=0.3`, `max_tokens≈220`.

### Files to modify

- `web/sandlot/atoms.jsx` — add `PlayerPhoto` atom, export on `window`.
- `web/sandlot/v2-pages.jsx` — replace `V2ProfileHero` with combined hero+take card; rebuild `V2ProfileTrend` as KPI strip (L7 / L30 / vs Exp) + chart with directional arrow; replace `V2ProfileGameLog` with accordion variant. `V2PlayerSheet` already fetches `/api/player/{id}` so the new `take` field flows through automatically.
- `sandlot_db.py` — `player_takes` table in `init_schema`, `get_player_take` + `set_player_take` helpers.
- `sandlot_skipper.py` — `SkipperClient.complete()` method.
- `player_service.py` — extend `build_player_payload()` (or wherever the `/api/player/{id}` JSON is assembled) to include `take`; lazy-generate via `SkipperClient.complete()` and `set_player_take()` on cache miss.
- `sandlot_api.py` — no route changes needed; the existing `/api/player/{id}` is the surface. Confirm `/api/player/{id}/refresh` does NOT regenerate the take (refresh is for stat data; takes are tied to snapshot, not to live MLB pulls).

### Verification

1. Babel-parse `v2-pages.jsx` and `atoms.jsx`.
2. Hit `/api/player/{id}` for a rostered player — confirm the response is cache-first and fast. If game log/media/take are missing, confirm `profile_cache.needs_refresh` is true and the web service schedules a background warm.
3. Hit `POST /api/player/{id}/refresh` — confirm MLB game logs/media are refreshed and `take.text` is generated or returned from cache.
4. Headshot: open three players (one with MLB ID resolved, one un-resolved, one with a broken image URL via dev tools) — confirm fallback to initials in each non-happy case.
5. Trend arrow: open a player on a hot streak vs. a cold streak — confirm ↑ vs ↓ direction.
6. Accordion: open a player, confirm the more-stats/game-log section expands in place.
7. Visual regression: scroll the sheet end-to-end; confirm nothing under the new hero/take card was inadvertently dropped.

## Phase 2 — Player data aggregation (plan only, separate PR)

Decision (confirmed): **scrape once a day, cache in Postgres, serve everything else from cache.** No per-request scraping. The user check-in pattern is weekly, not daily, so a 24-hour-old splits/matchup blob is fine. If staleness ever becomes a real problem on a specific feature, that feature alone gets a faster cadence.

### What "scrape data on the players" means in practice

Three categories of data, in priority order:

**(a) Daily splits + season totals refresh** — easy win.
Source: MLB Stats API (`statsapi.mlb.com`, free, public, already used in `mlb_stats.py`). Pulls `vs-RHP`, `vs-LHP`, `home`, `away`, plus current-season totals.
Storage: new `player_splits` table keyed by `(mlb_id, season, scope)` — overwritten daily. ~25 rostered players × 4 split scopes = 100 rows total. Trivial.
Cadence: nightly cron (extend `sandlot_cron.py` to run after the snapshot refresh). MLB Stats API is rate-friendly; no auth.
Surface: powers a future "Splits" deeper view from the player card; also feeds the Skipper take prompt with richer context (`vs LHP this year: 2.9 FP/G` is a much sharper input for the LLM).

**(b) Today's matchup / probable pitcher** — ~1 hour of work.
Source: MLB Stats API `schedule?date=...&hydrate=probablePitcher` — one call per day for all 30 teams. Adds the opposing team, ballpark, and probable pitcher.
Storage: new `daily_matchups` table keyed by `(team_abbr, date)`. ~30 rows per day; auto-prune anything older than 7 days.
Cadence: morning cron (~9 AM ET, before lineups lock). One job; idempotent.
Surface: enables the "Tonight" card in Mock D; lets the Skipper take say "facing a LHP today, sit him" with high confidence.

**(c) News + injury status** — punt for now.
The reliable free sources have all eroded: Twitter/X is paywalled, MLB's `/transactions` endpoint is slow, RotoWire/Yahoo require scraping with iffy ToS. Recommendation: revisit only when the absence becomes a felt gap. The Skipper take fills most of the "what should I think about this guy?" need without it.

### Cron strategy (decided: Option A)

`Procfile` already has a `cron: python sandlot_cron.py` line. Today that script just calls `run_refresh()` (Fantrax snapshot pull).

**Decision: extend `sandlot_cron.py` with `--job={refresh|splits|matchups}` CLI flags** (default `refresh` for backwards compat). Each Railway cron schedule points at the same script with a different flag. One file, multiple entry points, shared logging/error handling.

Cadences:
- `refresh` (existing Fantrax snapshot pull): every 30 min during game days.
- `splits` (per-rostered-player splits + season totals from MLB Stats API): once nightly, ~3 AM ET.
- `matchups` (today's probable pitcher per team + park from MLB Stats API): once daily, ~9 AM ET.

### Why this isn't in Phase 1

Phase 1 ships immediate user-visible value (better card, real photos, Skipper take). Phase 2 is plumbing — the splits and matchup data only become valuable after a UI surface uses them, and we haven't designed that surface yet. Locking the data layer to imagined future UI risks over-modeling. Build the felt need first.

### One bit of leverage Phase 2 unlocks for the Skipper take

The Phase 1 Skipper take prompt only sees what's already in the snapshot + game log. Once Phase 2 caches splits and tonight's matchup, those fields plug straight into the take prompt — no UI change needed for the take to start citing "vs LHP this year" or "facing Sears tonight." So Phase 2 is also the cheapest way to make Skipper smarter without writing a new feature.

## Verification (Phase 1 only)

Local babel parse → hit a Railway preview → walk through:

1. Open Roster, tap a player → sheet opens with headshot, hero+take side-by-side, trend chart with arrow, season stats, single-game accordion. First open is slow (~2-3s for the take); refresh and re-open is instant.
2. Open Skipper, click a linked player name → same sheet (already wired in last PR).
3. Tap the recent-games chevron → 13 more rows expand below, sheet scrolls naturally.
4. Force a Skipper API failure (kill the OpenRouter key in env): card still loads with all stats, take area shows "Skipper unavailable" gracefully.
5. Open a player whose MLB ID isn't resolved → headshot falls back to initials avatar.
