# Fantrax Daily Audit + Weekly League Intel

Two scripts working together:

- **`audit.py`** — runs daily. Quick lineup hygiene check. Pulls your roster, standings, transactions, FA pool, asks Claude for a short report, emails it.
- **`league_intel.py`** — runs weekly (Sundays). Deep research-driven recommendations. Pulls every team in the league, hydrates with current MLB stats from Fangraphs/Statcast, runs grounded web research on the highest-impact moves (sources cited, last 30 days only), emails a TL;DR-then-evidence report.
- **`sandlot_api.py` / `sandlot_cron.py`** — Sandlot V1 web app. Railway-ready FastAPI service with Postgres snapshots, manual refresh, daily cron scrape, player detail sheets, MLB headshots/game logs/media, background profile warming, and cached Skipper takes. See [`docs/sandlot-railway-v1.md`](docs/sandlot-railway-v1.md).

**Both are recommend-only.** The Fantrax library is read-only — these scripts will never set a lineup, drop a player, or accept a trade for you. They tell you what to do; you decide and execute manually in Fantrax.

## What you'll need

- macOS
- Python 3.11 or newer (`python3 --version` to check; install from [python.org](https://www.python.org/downloads/) if missing)
- Node.js (for the Claude Code CLI; install from [nodejs.org](https://nodejs.org/))
- A Claude Pro or Max subscription (the script uses the `claude` CLI, not the API)
- A Gmail account with an App Password
- Google Chrome installed (Selenium uses it for the one-time login)

## Setup, in plain language

### 1. Install the Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
claude   # follow the prompts to log in
```

You only need to do this once. After that, `claude --print` works from any terminal.

### 2. Create a Gmail App Password

Regular Gmail passwords don't work for SMTP anymore. You need an "App Password":

1. Go to <https://myaccount.google.com/apppasswords>
2. (You may need to enable 2-Step Verification first)
3. Create a new app password called "Fantrax Audit"
4. Copy the 16-character password — you'll paste it into `.env` in the next step

### 3. Configure `.env`

```bash
cd /Users/zach/Projects/fantrax-daily-audit
./setup.sh         # this creates .env from .env.example on first run
```

Open `.env` and fill in:

- `FANTRAX_USER` — your Fantrax email
- `FANTRAX_PASS` — your Fantrax password
- `FANTRAX_LEAGUE_ID` — already filled in (`lydahdo6mhcvnob7`)
- `FANTRAX_TEAM_ID` — already filled in (`tuumpjsjmhcvnobp`)
- `EMAIL_FROM` — your Gmail address
- `EMAIL_TO` — where the report should go (usually the same Gmail)
- `GMAIL_APP_PASSWORD` — the 16-char App Password from step 2
- `OPENROUTER_API_KEY` — optional for Sandlot Skipper chat and cached player-card takes

### 4. Install Python dependencies

```bash
./setup.sh
```

This creates `.venv/` and installs everything from `requirements.txt`.

### 5. Run the first audit manually

```bash
source .venv/bin/activate
python audit.py
```

**The first run will pop open a Chrome window** so Selenium can log in to Fantrax. If Fantrax asks for MFA or shows a captcha, complete it in that window. After login, the cookies get saved to `.cookies/fantrax.json` and future runs are headless.

If everything works you'll get an email titled `<your team name> — daily audit YYYY-MM-DD`. Check `.data/snapshot-YYYY-MM-DD.json` to see the raw data, and `.data/logs/YYYY-MM-DD.log` if anything looks off.

### 6. Schedule daily audit at 7am AND weekly intel Sundays at 7am

```bash
# Daily audit
cp com.zach.fantrax.audit.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.zach.fantrax.audit.plist

# Weekly intel (deeper, more expensive run)
cp com.zach.fantrax.intel.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.zach.fantrax.intel.plist
```

To check loaded: `launchctl list | grep fantrax`.
To stop one: `launchctl unload ~/Library/LaunchAgents/com.zach.fantrax.audit.plist` (or `.intel.plist`).
To run on demand: `launchctl start com.zach.fantrax.audit` (or `.intel`).

### 7. (Optional) Run the weekly intel manually first

The weekly intel takes longer (10-20 minutes) because it runs grounded web research on each candidate decision. Run it once manually before scheduling so you can see what the report looks like:

```bash
source .venv/bin/activate && python league_intel.py
```

### Does my computer need to be on?

**Yes — the laptop must be awake at the scheduled time.** macOS launchd will not wake your laptop to run a scheduled job. If you close the lid Saturday night and don't open it until Monday, the Sunday intel run is skipped that week.

If you want runs that don't depend on your laptop:
- Use **GitHub Actions** with a cron schedule (free, runs in cloud, but requires storing creds as GitHub secrets and figuring out `claude --print` auth in CI)
- Or move to a **VPS or always-on machine**

Both are migrations off launchd; not blockers for getting started.

## What the daily audit report contains

1. **Changes since last run** — players added/dropped, slot changes, FP/G swings
2. **Analysis** — Claude's plain-English recommendations: this week's moves, drop candidates, players on the come-up, standings reality
3. **Roster snapshot** — your current roster as a table
4. **Pending trades** — count of pending trades involving your team
5. **Free agent pool status** — whether the FA endpoint worked today

## What the weekly intel report contains

1. **TL;DR** — 4-7 one-line recommendations with confidence tags (High / Medium / Low)
2. **Detailed Findings** — for each TL;DR line: plain-English reasoning, cited evidence with URLs, what could change the view, why this confidence level
3. **Decisions I Couldn't Verify** — moves the engine considered but couldn't ground in current sources
4. **How Your League Works** — scoring rules summary
5. **Sources** — every URL referenced, grouped by player

The intel report is the antidote to "Claude in Chrome going off training data." Every claim must reference a fetched source from the last 30 days. If the research can't verify something, it explicitly says so rather than making it up.

## Files written each run

Daily audit:
- `.data/snapshot-YYYY-MM-DD.json` — full raw snapshot (kept even if email fails)
- `.data/report-YYYY-MM-DD.md` — the markdown report
- `.data/logs/YYYY-MM-DD.log` — log output
- `.data/age_cache.db` — SQLite cache of player ages

Weekly intel:
- `.data/intel-snapshot-YYYY-MM-DD.json` — pre-research + post-research snapshot, written incrementally so research isn't lost on synthesis failure
- `.data/intel-report-YYYY-MM-DD.md` — the TL;DR-and-evidence report
- `.data/logs/intel-YYYY-MM-DD.log` — log output
- `.data/pybaseball.db` — SQLite cache of MLB stats and player lookups (1-day TTL for stats, 30-day for player IDs)

## When something breaks

- **Failure email arrived**: open it; the traceback tells you which step failed. Check `.data/logs/` for the full log.
- **Cookies expired**: delete `.cookies/fantrax.json` and re-run. Selenium will log in fresh.
- **MFA / captcha appeared**: set `FANTRAX_HEADFUL=1` in `.env`, re-run, complete the challenge in the browser window.
- **`claude: command not found`** when launchd runs it: `launchd` doesn't load your shell profile. Either add `CLAUDE_CMD=/path/to/claude` in `.env` (find with `which claude`), or symlink claude into `/usr/local/bin/`.
- **Free-agent pool says unavailable**: the `fxpa/req` method names Fantrax uses for FA pool may have changed. The script still produces the rest of the report; the FA section just notes it's missing.
- **MLB roster shape errors**: the underlying library was tested on NHL. If you see "Failed to parse roster row" warnings in the log, open the snapshot JSON, find the `raw` field on a row, and add the right attribute name to `_safe_attr` calls in `fantrax_data.py`.

## Files in this project

```
fantrax-daily-audit/
├── README.md                     # this file
├── requirements.txt              # python deps
├── .env.example                  # template; copy to .env
├── .gitignore
├── setup.sh                      # creates venv, installs deps
│
│ # Daily audit (lineup hygiene)
├── audit.py                      # daily entry point
├── auth.py                       # selenium login + cookie persistence (shared)
├── fantrax_data.py               # roster, standings, FA, all-teams, league rules
├── claude_analyzer.py            # subprocess call to claude --print (daily prompt)
├── notify.py                     # composes & sends the email (shared)
├── com.zach.fantrax.audit.plist  # daily 7am
│
│ # Weekly intel (deep research)
├── league_intel.py               # weekly entry point
├── pybaseball_layer.py           # Fangraphs/Statcast stats + player lookup cache
├── research_layer.py             # grounded web research via claude --print + WebSearch
├── decision_engine.py            # narrows candidates + synthesizes final report
└── com.zach.fantrax.intel.plist  # Sunday 7am
```

## What this script will NOT do

- Auto-execute roster moves, drops, claims, or trades (the API is read-only and even if it weren't, no)
- Store credentials anywhere except `.env` (which is in `.gitignore`)
- Email anyone except `EMAIL_TO`
