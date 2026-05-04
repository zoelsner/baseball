# Fantrax Dashboard — Design Spec

**Date:** 2026-05-03
**Status:** Draft, awaiting user review
**Author:** Zach + Claude

---

## Goal

Replace the email-only audit pipeline with a self-hosted web dashboard that runs autonomously (no local Mac required). Phase 1 ships a **roster understanding tool** — visualize the user's team and the league at a glance, color-coded by status, ranked by position.

## Why now

The current stack (`audit.py`, `league_intel.py`) works but:
1. Requires the user's laptop to be open at 7am for launchd to fire
2. Outputs are read-only emails — no clickable filtering, no visual ranking
3. The user explicitly said: "I don't even really know how the different players rank against each other within position." Email digests don't solve that. A dashboard does.

## Non-goals (v1)

- Auto-actions on Fantrax (drops, lineup sets, trade execution) — stay recommend-only
- Replicating pybaseball / Fangraphs hydration — defer to v2
- Mobile native app — Vercel app will be responsive; PWA shell is v2
- Multi-league support — one league, one user
- Replacing the existing `audit.py` / `league_intel.py` immediately — they keep running until the dashboard reaches feature parity, then we deprecate

## Architecture

### Stack
- **Frontend:** Next.js 16 App Router, shadcn/ui, Tailwind, TypeScript
- **Hosting:** Vercel (Hobby tier sufficient initially; Pro if cron limits hit)
- **Data store:** Supabase Postgres
- **Auth:** Supabase Auth (magic link, single user)
- **Scheduled jobs:** Vercel Cron Jobs
- **Fantrax client:** TypeScript port of the Python `fantraxapi` library (Codex-assisted)
- **Headless browser (initial cookie capture only):** Playwright + `@sparticuz/chromium-min` running in a Vercel Function, OR a one-time local script that pushes cookies to Supabase
- **AI analysis:** Anthropic SDK (Claude API) with prompt caching

### Flow

```
[Browser] ◄── Vercel app (Next.js)
              │
              ├── /api/cron/refresh-fantrax  (daily 7am UTC)
              │   └── Read cookies from Supabase
              │       Hit Fantrax JSON API
              │       Write snapshot to Supabase
              │
              ├── /api/cron/scout-trades     (every 4 days)
              │   └── Read snapshots from Supabase
              │       Call Claude API with all-roster context
              │       Write scout output to Supabase
              │
              └── /api/grade-trade           (on-demand)
                  └── Read roster from Supabase
                      Call Claude API
                      Write grade to Supabase

[Supabase Postgres]
  - cookies         (encrypted Fantrax session cookies)
  - snapshots       (daily roster + standings + FA pool)
  - reports         (typed: 'audit' | 'intel' | 'scout')
  - trade_grades    (on-demand grader history)
  - users           (just you)

[Local — one-time setup]
  - scripts/bootstrap-cookies.ts (run locally once when cookies expire)
    Uses local Playwright to log in, pushes cookies to Supabase
```

### Why this split (vs. all-in Vercel rewrite of pybaseball)
- Fantrax has a JSON API behind its UI; once you have a session cookie, you don't need a browser
- pybaseball is a Python wrapper around Fangraphs scraping — porting it to TS is weeks of work
- v1 doesn't need pybaseball: Fantrax provides FP/G, position, age, injury status — enough for the roster-understanding view
- v2 can add stat hydration via a separate weekly Vercel cron if needed

### Cookie refresh (the one local dependency)
Fantrax cookies expire every ~30 days. Two options:
- **Option A (chosen):** User runs `pnpm bootstrap-cookies` locally once a month. Playwright opens Chrome, user logs in, cookies pushed to Supabase. Five-minute monthly task.
- **Option B (deferred to v2):** Run Playwright on Vercel Function with `@sparticuz/chromium-min`, store username/password in Vercel env vars, refresh cookies via cron. Heavier; revisit if monthly bootstrap becomes annoying.

## Data model (Supabase)

```sql
-- Encrypted Fantrax session for the cron jobs
create table cookies (
  id uuid primary key default gen_random_uuid(),
  cookies_json jsonb not null,         -- encrypted at app layer
  expires_at timestamptz not null,
  refreshed_at timestamptz default now()
);

-- Daily Fantrax pulls
create table snapshots (
  id uuid primary key default gen_random_uuid(),
  taken_at timestamptz default now(),
  date date not null,
  data jsonb not null,                  -- { my_roster, standings, all_team_rosters, free_agents, transactions }
  unique (date)
);

-- Generated reports (audit, intel, scout)
create table reports (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('audit', 'intel', 'scout')),
  generated_at timestamptz default now(),
  date date not null,
  markdown text not null,
  source_snapshot_id uuid references snapshots(id)
);

-- Trade grader history
create table trade_grades (
  id uuid primary key default gen_random_uuid(),
  graded_at timestamptz default now(),
  counterparty text,
  offer_text text not null,             -- "Skubal+Montes for Yordan+Alonso"
  parsed_offer jsonb,                   -- { their_send: [...], my_send: [...] }
  verdict text,                          -- 'accept' | 'counter' | 'reject'
  analysis_md text not null,
  recommended_counter text
);

-- Single-user auth, but follow Supabase patterns
create table profiles (
  id uuid primary key references auth.users(id),
  email text not null,
  fantrax_team_id text,
  fantrax_league_id text
);
```

RLS: enable on all tables, policy = "authenticated user can read their own data."

## UI / Routes

### `/` — Overview
- Standings card (rank, record, GB, PF)
- "Latest signals" — top 3 from most recent scout output
- "This week's moves" — top 3 from latest audit
- Quick-link buttons: My Roster, League, Trade Scout, Trade Grader

### `/roster` — **Phase 1 priority. The flagship view.**

Visual position-by-position grid. For each position (C, 1B, 2B, 3B, SS, OF, SP, RP, etc.):

- **Header row:** Position name + your rank vs. league at this position (e.g. "SS — you're #5 of 12 in average FP/G here")
- **Player cards** in two columns: yours and league context
  - **Yours:** all your players at this position, sorted by FP/G desc
    - **Color = status:**
      - **Blue** = currently in your active lineup
      - **Green** = on your bench, healthy
      - **Red** = on IL or injured
    - Card shows: name, age, FP/G, season trend arrow
  - **League context:** top 5 free agents at this position by FP/G (so you can see what's available)

Filter bar at top: position dropdown, "show only injured", "show only bench."

### `/league` — All 12 teams
- Team cards in a grid (you highlighted)
- Click a team → see their full roster with same color coding
- Sortable: by record, by total FP/G, by age (youngest = rebuilders, oldest = win-now)

### `/trades` — Phase 2 + 3
- **Trade Scout section:** ranked output from latest cron run (every 4 days)
  - Each card: target team, package proposed, 5-yr value rationale, "draft DM" button (copies template to clipboard)
- **Trade Grader form:** paste an offer → instant Claude analysis, history below

### `/reports` — Markdown viewer
- List of all generated audits, intels, scout outputs
- Click to expand inline
- Same content as the email reports, just browsable

## Build phases

| Phase | Scope | Deliverable | Codex assist? |
|-------|-------|-------------|---------------|
| **0 — Bootstrap** | Next.js 16 app, Supabase project, magic-link auth, Vercel deploy | Public URL with login screen | Light |
| **1 — Roster view** | Fantrax TS client (read-only), daily cron, `/roster` page with color coding | You can see your team rendered, color-coded | **Heavy** — Codex ports `fantraxapi` to TS |
| **2 — League view** | `/league` page rendering all 12 teams from snapshot | All-team browsing | Light |
| **3 — Trade Grader** | `/api/grade-trade` route, `/trades` form, Claude API integration | Paste offer → instant analysis | Medium |
| **4 — Trade Scout** | `/api/cron/scout-trades` (every 4 days), scout cards on `/trades` page | Auto-suggested trade ideas | Medium |
| **5 — Reports parity** | Port `audit.py` + `league_intel.py` analysis logic to TS, deprecate launchd | Email pipeline retired | Heavy |

Phase 5 is the longest pole. Phases 0-3 are the v1 ship.

## Key risks + mitigations

| Risk | Mitigation |
|------|-----------|
| Fantrax JSON API changes / rate limits | Cache aggressively (one daily pull, snapshot reused all day). Add retry + alert on parse failure. |
| Cookie refresh forgotten → cron fails silently | Cron writes a "last successful run" timestamp; dashboard shows red banner if stale > 36 hours. |
| Vercel cron limits on Hobby tier | Hobby = 2 cron jobs, daily cadence. We need 2 (daily + every-4-days). Fits. Upgrade to Pro if we add hourly jobs. |
| Claude API costs balloon | Use prompt caching on the long roster context (per `claude-api` skill). Set monthly spend alert at $50. |
| Codex makes architecture mistakes | Each Codex delegation is scoped to one file/module + has a verification step. Review diff before merge. |
| Single-user auth = no protection if URL leaks | Magic link via Supabase = email-bound. Even if URL is shared, attacker needs your email. |

## Open questions for user review

1. **Color coding** — confirmed: blue = in lineup, green = bench/healthy, red = injured. Anything else? (e.g. yellow for "questionable" / day-to-day)
2. **Repo location** — new repo at `/Users/zach/Projects/fantrax-dashboard`, OR add `/web` subdir to existing `/Users/zach/Projects/fantrax-daily-audit`? Recommendation: **new repo** (cleaner deploy, separate package.json).
3. **Domain** — `*.vercel.app` for v1, or buy a custom domain (e.g. `fantrax.zachoelsner.com`)?
4. **Existing Python pipeline** — keep running in parallel until dashboard reaches Phase 5 parity, then deprecate? Or shut down launchd as soon as Phase 1 ships?

## Success criteria for v1 (Phases 0-3)

- [ ] Magic link login works
- [ ] Daily Fantrax pull runs autonomously on Vercel cron, no local Mac involved
- [ ] `/roster` page renders all your players, correctly color-coded by lineup status + injury status
- [ ] Position rank shown for each position (your rank in league)
- [ ] `/league` page renders all 12 team rosters with same coloring
- [ ] Trade Grader form: paste an offer, get a Claude analysis in < 30 seconds, saved to history
- [ ] Cookie staleness banner triggers when last successful pull > 36 hours
