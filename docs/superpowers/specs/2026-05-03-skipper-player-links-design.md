# Skipper Player Links + Profile Screen — Design

## Goal

When Skipper mentions a player in chat, that name is tappable. Tapping pushes a player profile screen over the current tab, showing per-game performance, season stats, and trend direction. Back arrow returns to the chat with scroll position intact.

This is the first piece of "actions from chat." The profile screen is reusable from anywhere in the app, but the first surface that wires it up is Skipper.

## User Story

> I'm chatting with Skipper. It says "Brooks Lee at 2.36 FP/G is your best 2B." I tap "Brooks Lee." A profile screen slides in showing his last 7-game trend (3.14 FP/G, +33% vs season), a season stat card (.287 / 82 H / 14 HR / 48 RBI), a 14-game sparkline of fantasy points, and a row-by-row game log with date, opponent, stat line, AVG, and FPts. There's a Sync button if I want to force a fresh MLB pull. Back arrow returns me to my chat exactly where I left it.

## Architecture Overview

```
[Skipper chat reply]                     [V2PlayerProfile (new push-view)]
  Skipper LLM emits tags:                  ┌──────────────────────────────┐
  "[[Brooks Lee|fantrax_xxx]]"             │ ← Player           ↻ Sync    │
       │                                   ├──────────────────────────────┤
  Frontend parses tags +                   │ Brooks Lee     [BL photo]    │
  name-match fallback against              │ MIN · 2B/SS · Age 25         │
  snapshot.player_index                    │ [On Reading Zohann][Active]  │
       │                                   ├──────────────────────────────┤
  Renders <PlayerLink> spans               │ Last 7 games                 │
       │                                   │ 3.14 FP/G    [+33% ↑]        │
  onClick → pushView('player',             ├──────────────────────────────┤
                     fantrax_id)           │ Season · Hitting             │
                                           │ .287 | 82 | 14 | 48          │
       ▼                                   ├──────────────────────────────┤
  GET /api/player/{fantrax_id}             │ Last 14 games (sparkline)    │
       │                                   ├──────────────────────────────┤
  Backend:                                 │ Game log:                    │
  1. Pull from snapshot (sync)             │  May 2  vs DET 2-4 HR  .500  5.5
  2. Resolve mlb_id (cache or              │  May 1  vs DET 1-3 BB  .333  2.0
     name+team match → MLB API)            │  Apr 30 @ KCR  3-5 RBI .600  4.5
  3. Pull game log from cache              │  Apr 29 @ KCR          —     DNP
     (or MLB Stats API if stale)           │  Apr 28 @ KCR  0-4 K   .000 -1.0
  4. Compute trend (last7 vs season)       └──────────────────────────────┘
  5. Return composed player payload
```

## Decisions Locked During Brainstorm

| Decision | Choice | Why |
|---|---|---|
| Where the profile lives | Push-view over current tab; back returns to chat | Reusable from any tab; preserves chat scroll; native iOS feel |
| How player names are detected | Hybrid: LLM tags + frontend name-match fallback | Tags are accurate when present; fallback covers model drift |
| Visual treatment of links | Bold + dashed underline, accent color | Quiet, prose-feeling — keeps chat readable |
| v1 content scope | Header + trend + season stats + sparkline + game log | Initials avatar (no real photo yet); no news feed yet |
| Game-log data source | MLB Stats API + lazy cache in Postgres (12h TTL) | Free, no auth, per-player endpoint; cron-free; per-league-rules accuracy not needed for v1 |
| Refresh strategy | Lazy-fetch on profile open + manual Sync button | Avoids new cron; manual override is the user's escape hatch |

## Components

### Backend

**`mlb_stats.py` (new)** — wraps `statsapi.mlb.com`.
- `lookup_player_by_name(name: str, team: str | None) -> int | None` — returns MLB person ID via `/people/search`
- `fetch_game_log(mlb_id: int, season: int, group: 'hitting' | 'pitching') -> list[dict]` — returns clean per-game rows: `{date, opponent, home, ab, h, hr, rbi, avg_game, fpts_estimated, line: "2-4, HR, RBI"}`
- Internal: stat-line formatter that turns the raw box score into "2-4, HR, RBI" / "5 IP, 6 K, 1 ER"
- `fpts_estimated` is a rough computation using a fixed baseline scoring formula (configurable later); v1 can pull season totals + per-game raw stats and approximate. **Decision:** if approximation is unreliable, omit per-game FPts column from v1 and only show stat line + AVG. Will decide during impl based on what stats API returns.

**`player_service.py` (new)** — orchestrates the player profile payload.
- `get_player_profile(fantrax_id: str) -> dict` — composes:
  1. Snapshot row for the player (name, team, slot, positions, age, season FPts/FPpg, injury, ownership)
  2. mlb_id resolution (cache hit, or name+team lookup; cached forever once resolved)
  3. Game log (cache hit if <12h old, else MLB API + write-through cache)
  4. Trend computation: `last7_avg_fpts` vs `season_avg_fpts` → `pct_change`, plus `last7_avg_batting`
- `force_refresh(fantrax_id: str) -> dict` — bypass cache, re-pull MLB game log, return fresh payload

**Schema additions** (`sandlot_db.py`):

```sql
CREATE TABLE IF NOT EXISTS player_id_map (
  fantrax_id TEXT PRIMARY KEY,
  mlb_id BIGINT,                      -- NULL means "looked up but not found"
  resolved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS player_game_logs (
  mlb_id BIGINT PRIMARY KEY,
  group_type TEXT NOT NULL CHECK (group_type IN ('hitting', 'pitching')),
  season INTEGER NOT NULL,
  games JSONB NOT NULL,               -- list of normalized per-game rows
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Helpers: `get_mlb_id`, `set_mlb_id`, `get_player_game_log`, `set_player_game_log`. TTL check (`fetched_at < now() - interval '12 hours'`) is done in `player_service`.

**API endpoints** (`sandlot_api.py`):
- `GET /api/player/{fantrax_id}` → composed payload
- `POST /api/player/{fantrax_id}/refresh` → force-pull + return composed payload

**Skipper system prompt update** (`sandlot_skipper.py`):

Add to `SYSTEM_PROMPT`:
> When you mention a player who appears in the snapshot, wrap their name in double brackets with their fantrax_id like this: `[[Brooks Lee|fr_xxxxxxx]]`. Use the exact `id` field from the snapshot. Do not wrap a name if you cannot find an id for it. Do not wrap team names, league names, or anything other than individual players.

`build_context()` already includes the `id` field for every player row, so the model has what it needs. Tier 3 questions get other-team rosters too (also with ids) — those names should be wrapped too.

### Frontend

**Routing extension** (`web/sandlot/v2-pages.jsx`):

Current `V2App` is a tab-switch state machine. Add a parallel "push stack" state:
```jsx
const [pushed, setPushed] = useState(null);  // { type: 'player', id: 'fr_xxx' } | null
```

Render order:
- `pushed` is null → render the active tab as today
- `pushed` is set → render the active tab underneath, the pushed view as an overlay slid in from the right

Push: `setPushed({ type: 'player', id })`. Pop: `setPushed(null)`. Pop also fires when pressing the OS back button (browser `popstate`); use `history.pushState` to register a synthetic state on push so the back button works.

**`<PlayerLink>` component (new)** in `v2-pages.jsx`:
- Props: `{ id, name, snapshotIndex }`
- Renders a `<span>` with the bold + dashed-underline + accent style
- onClick → `pushView({ type: 'player', id })`

**Chat link parser** in `V2Skipper`:
1. First, regex out `[[Name|fantrax_id]]` tags → emit `<PlayerLink id={id} name={name}/>`
2. For remaining text, walk the snapshot's player index (built once on mount: `Map<lowercased_name, fantrax_id>`) and wrap any matched contiguous name. Walk by token, longest match wins. Skip if already inside a tag span.
3. Apply this to both streamed and persisted messages.

Build the snapshot index by fetching `/api/snapshot/latest` once on Skipper mount (already loaded), iterating `roster.rows` + `all_team_rosters[*].rows` + `free_agents.players`. Store as `{ [lowercase_name]: fantrax_id }`. Re-build when snapshot changes.

**`<V2PlayerProfile>` component (new)**, structure per the v4 mockup:
- Top bar: `← Player` (left), `↻ Sync` (right — calls `POST /api/player/{id}/refresh` with a small spinner state)
- Hero: name + meta + pills + 78×78 photo placeholder (rounded square, accent gradient, white-bordered, initials inside)
- Trend strip: "Last 7 games" + big FP/G + sub-line ("up from X season avg · .YYY AVG") + green/red arrow pill
- Season card: "Season · Hitting" or "Season · Pitching" + 4-cell stats grid (AVG/H/HR/RBI for hitters; ERA/IP/K/WHIP for pitchers)
- Sparkline: "Last 14 games · Fantasy points" + 14 bars (DNP/zero days are gray)
- Game log: table with columns Date | vs · Line | AVG | FPts. DNP rows show "—" / "DNP". Negative FPts colored red.

Loading state: skeleton bars in each card while `GET /api/player/{id}` is in flight. Error state: a single retry banner inline.

## Data Flow Detail

### First time a profile is opened for player X

1. Frontend: `pushView({type:'player', id:'fr_xxx'})` → renders `<V2PlayerProfile id="fr_xxx"/>`
2. Profile fetches `GET /api/player/fr_xxx`
3. Backend `player_service.get_player_profile`:
   - Reads snapshot.roster + standings + (if needed) all_team_rosters → finds row with `id='fr_xxx'`
   - Looks up `player_id_map.fantrax_id='fr_xxx'` → cache miss
   - Calls `mlb_stats.lookup_player_by_name(name='Brooks Lee', team='MIN')` → returns `mlb_id=694671`
   - Writes to `player_id_map`
   - Looks up `player_game_logs.mlb_id=694671` → cache miss
   - Calls `mlb_stats.fetch_game_log(694671, season=2026, group='hitting')` → returns list of game rows
   - Writes to `player_game_logs` with `fetched_at=now()`
   - Composes payload: `{ player: {…}, season: {…}, trend: {…}, sparkline: [..14], games: [..N] }`
4. Frontend renders

### Subsequent opens within 12h

Same as above, but cache hits. Total backend time: ~50ms.

### Manual Sync

User taps Sync → `POST /api/player/fr_xxx/refresh`
- Skip TTL check, force `mlb_stats.fetch_game_log` re-pull, write through cache, return new payload
- Frontend swaps in new data, "Sync" button shows spinner during call

### MLB lookup fails

If `lookup_player_by_name` returns `None`, write `mlb_id=NULL` to `player_id_map` so we don't re-query every open. Profile renders header + season Fantrax data only; trend/sparkline/games sections show "MLB stats not available." User can tap Sync to retry.

## Edge Cases

- **Pitcher vs hitter stat groups**: detect from snapshot positions (`SP/RP/P` → pitching; otherwise hitting). Fetch the right `group` from MLB API. Some two-way players will pick one — V1 picks based on slot.
- **Player not in snapshot at all**: shouldn't happen since the link came from snapshot data, but if `fantrax_id` isn't found, return 404 from API; frontend shows "Player not found" with back button.
- **Common-name false positives in fallback matcher**: e.g., "Brooks" matching "Brooks Lee" inside another sentence. Mitigation: only match full names (first + last), never single words. Index keys are lowercase full names only.
- **Tag leak**: if Skipper writes a malformed tag like `[[Brooks Lee|]]` or `[[Brooks Lee` (no close), parser regex requires the full `[[name|id]]` shape; malformed tags fall through and are rendered as raw text. The fallback matcher then catches the name.
- **Refresh-button rate**: no rate limit in v1; if abused, MLB API will rate-limit. Add a 5-second client-side debounce.
- **Stale snapshot**: profile shows whatever the last successful snapshot has. If the snapshot is >24h old, Sync only refreshes MLB data, not Fantrax. Header surfaces snapshot age via the existing freshness pill (pulled from the snapshot in the profile payload).

## Out of Scope for V1

- Real player photos (will need a separate image source — MLB headshot URL pattern probably works, defer)
- News feed (originally mentioned; will be a follow-up after photos are wired)
- Trade-grade button or any action shortcuts from the profile
- Push-view animation polish (basic slide-in is enough for v1)
- Deep-linkable URL routing (`/player/fr_xxx` in URL bar) — current V2App is fragment-less; this can come when we add real routing
- Comparing two players side-by-side
- Multi-player views from chat (e.g., a list of suggested adds)

## Build Order

1. **DB schema** — `player_id_map` + `player_game_logs` tables, helpers in `sandlot_db.py`
2. **MLB stats module** — `mlb_stats.py` with lookup + game-log fetch + line formatter; manual test against a known MLB player
3. **Player service** — `player_service.py` composing snapshot + cached MLB data; handle cache-miss + lookup-fail paths
4. **API endpoints** — `GET /api/player/{id}` and `POST /api/player/{id}/refresh`; smoke test with a real fantrax_id
5. **Skipper system prompt update** — add tag instruction; verify Skipper actually emits tags via local test
6. **Frontend push-view infrastructure** — extend `V2App` with `pushed` state + back/`popstate` handling; throwaway test view
7. **`<PlayerLink>` + chat parser** — render tags first, then fallback matcher; test with mock messages then live Skipper output
8. **`<V2PlayerProfile>` component** — top bar + hero + trend + season + sparkline + game log; loading + error states
9. **Sync button** — wire `POST /api/player/{id}/refresh`; debounce 5s; spinner state
10. **End-to-end test** — chat with Skipper, tap a player, see profile, sync, navigate back; reload preserves nothing (V2 is in-memory; that's fine for v1)
11. **Deploy** — push to main; Railway auto-deploys; smoke-test live

## Verification

**API**
```bash
# Compose a profile (cache miss path, then cache hit)
curl -s http://127.0.0.1:8000/api/player/<fantrax_id> | jq .

# Force refresh
curl -s -X POST http://127.0.0.1:8000/api/player/<fantrax_id>/refresh | jq .games
```

**Schema**
```sql
SELECT count(*) FROM player_id_map WHERE mlb_id IS NOT NULL;
SELECT mlb_id, jsonb_array_length(games) AS games_count, fetched_at
FROM player_game_logs ORDER BY fetched_at DESC LIMIT 5;
```

**Browser**
- Send "Who is my best 2B?" in Skipper → response should contain at least one underlined-dashed player name
- Tap the link → profile slides in, header + trend + season + sparkline + log all populate
- Tap Sync → spinner, payload updates (or no visible change if data unchanged)
- Tap back → returns to Skipper, scroll position preserved
- Browser back button = same as in-app back arrow

## Open Questions for Implementation

- **MLB API rate limits**: undocumented. If we hit them, switch to per-game caching with shorter TTL or batch requests.
- **`fpts_estimated` per-game**: Fantrax FP rules are league-specific. v1 can either (a) hide per-game FPts entirely and show only stat line + AVG, or (b) approximate using a hardcoded baseline (e.g., 1B=1, 2B=2, HR=4, RBI=1, K=-1). Decide during impl based on whether the approximation is "close enough" — if not, hide the column.
- **Trend window**: "last 7 games" is the default but pitchers play less often. Consider "last 5 starts" for pitchers. Defer; ship with 7 for everyone in v1 and observe.
