# Sandlot Risk Register

**Audited commit:** `bd54135` (`origin/main`, 2026-07-14)
**Audit date:** 2026-07-24
**Method:** four parallel read-only agents, findings then re-verified against source by the orchestrator.

> **Scope note.** This register describes `origin/main`. It was produced while the
> primary working checkout sat on `codex/fix-5-player-index`, 114 commits behind.
> If you are reading this from a branch that does not contain `sandlot_matchup.py`,
> you are on the wrong tree. See [R22](#r22).

---

## How to use this

Each entry is a self-contained work item. `Brief` is written so it can be handed
to an agent or a person without re-reading this document.

**Verification key**

| Mark | Meaning |
|---|---|
| ✅ **Verified** | Orchestrator independently read the cited code and confirmed the claim. |
| 🔶 **Reported** | Surfaced by an audit agent with citations; not independently re-checked. Confirm before acting. |

Treat 🔶 as a strong lead, not a fact. Roughly one in six agent findings had
line numbers off by a few, and a small number of framing claims did not survive
scrutiny — the ones that did are marked ✅.

**Severity** reflects consequence *to this owner*: a single-user hobby app with
no third-party PII, holding real Fantrax credentials and a funded OpenRouter key,
deployed at a public Railway hostname.

---

## Summary

| ID | Severity | Area | Finding | Status |
|---|---|---|---|---|
| [R1](#r1) | **High** | Cost/abuse | `/api/refresh` token guard fails open when unset | ✅ |
| [R2](#r2) | **High** | Cost/abuse | Skipper stream uncapped + unbounded history replay | ✅ |
| [R3](#r3) | **High** | Cost/abuse | Player sheet polls forever, each cycle queues an LLM call | ✅ |
| [R4](#r4) | **High** | Correctness | Two projection models that never reconcile | ✅ |
| [R5](#r5) | **High** | Correctness | Win probability is systematically overconfident | ✅ |
| [R6](#r6) | **High** | Roadmap | Learning loop joins on exact dates; labels vanish silently | ✅ |
| [R7](#r7) | **High** | Process | E2E suite goes green precisely when production breaks | ✅ |
| [R8](#r8) | Med-High | Cost | `/api/trades/grade` unauthenticated, 2 completions/call | 🔶 |
| [R9](#r9) | Med-High | Correctness | No sample-size shrinkage anywhere in waiver ranking | 🔶 |
| [R10](#r10) | Med-High | Roadmap | Probability release gate is structurally unreachable | 🔶 |
| [R11](#r11) | Medium | Trust | Three freshness implementations, two threshold sets | ✅ |
| [R12](#r12) | Medium | Destructive | `DELETE /api/skipper/messages` unauthenticated | ✅ |
| [R13](#r13) | Medium | Trust | Fabricated player quotes reachable in the UI | 🔶 |
| [R14](#r14) | Medium | Supply chain | Every Python dep unbounded `>=`, no lock | ✅ |
| [R15](#r15) | Medium | Robustness | No React error boundary anywhere | 🔶 |
| [R16](#r16) | Medium | Roadmap | Retention ≈ 15 days of history | ✅ |
| [R17](#r17) | Medium | Process | `app.js` is a committed build artifact | 🔶 |
| [R18](#r18) | Low-Med | Credentials | Cookie files written world-readable | ✅ |
| [R19](#r19) | Low-Med | XSS | Model-supplied citation URL unvalidated | ✅ |
| [R20](#r20) | Low-Med | Robustness | `run_refresh()` unguarded against DB failure | ✅ |
| [R21](#r21) | Low | Trust | `v2NormalizeSnapshot(undefined)` reports "fresh" | 🔶 |
| [R22](#r22) | Medium | Process | Branch sprawl and stale primary checkout | ✅ |

**What is NOT broken** — see [Checked and sound](#checked-and-sound). The write
boundary holds. Read that section before assuming anything about roster safety.

---

## Cost and abuse

These share a root cause: the app is at a public hostname, most routes are
unauthenticated by design, and several of them spend money.

### R1 — `/api/refresh` token guard fails open {#r1}

**Severity:** High · ✅ Verified · `sandlot_api.py:1719-1727`

```python
def _require_refresh_token(request: Request) -> None:
    expected = os.environ.get("SANDLOT_REFRESH_TOKEN")
    if not expected:
        return                      # ← no token configured = no guard
```

`STATUS.md:228` states this variable is unset on Railway. If that is still true,
`POST /api/refresh` is fully open to anyone who knows the hostname.

**Failure path.** A stranger loops `POST /api/refresh`. Each call drives a real
Selenium scrape against Fantrax using your authenticated session, from Railway's
IP. Consequence is not data loss — it is your Fantrax account getting
rate-limited or flagged for automation.

Separately, `provided != expected` at `:1727` is not constant-time. Rank this far
below the fail-open issue; a timing attack across the internet against a hobby
app is near-theoretical.

**Brief.** Verify the live value first — `railway variables`. If unset, set it,
then decide whether absent-token should mean *open* or *closed*. For a
public deployment, closed is correct: invert to deny when unset, with an
explicit opt-out env var if local dev needs it. Swap in `hmac.compare_digest`
while you are in the function.

---

### R2 — Skipper chat is an uncapped, self-amplifying LLM proxy {#r2}

**Severity:** High · ✅ Verified · `sandlot_skipper.py:1084-1120`, `sandlot_db.py:1825`

Two defects that compound:

1. `SkipperClient.stream()` builds its request kwargs with `model`, `messages`,
   `stream`, `temperature` — and **no `max_tokens`**. The only cap in the file
   (`max_tokens: int = 220`, `:1185`) belongs to a different method. Premium
   models, `reasoning=high`, and web search are all caller-selectable.
2. `list_chat_messages` is an unbounded `SELECT … ORDER BY created_at ASC` with
   no `LIMIT`, and the full result is replayed into every subsequent prompt.

**Failure path.** The endpoint is unauthenticated. An attacker — or a loop bug —
posts messages; they are persisted *before* the model call. Every future
conversation then carries that history, so per-request cost rises permanently
and never recovers. This is the one finding where the damage does not stop when
the attack stops.

**Brief.** Three independent fixes, each small: add `max_tokens` to `stream()`;
add a `LIMIT` to `list_chat_messages` and window what `build_messages` replays;
put the route behind the shared secret the frontend already holds. Do the window
first — it is the one that bounds the permanent damage.

---

### R3 — Player sheet polls forever, each cycle queuing an LLM call {#r3}

**Severity:** High · ✅ Verified · `web/sandlot/v2-pages.jsx:4365-4396`

```js
    let attempts = 0;                          // ← scoped INSIDE the effect
    // …
        if (done || attempts >= 12) return;    // ← cap that can never trigger
    // …
  }, [id, data]);                              // ← `data` is a dependency
```

`setData(next)` inside `tick` mutates `data`, which is in the dependency array,
which re-runs the effect, which re-declares `attempts = 0` and starts a fresh
2.5s timer. The 12-attempt cap is dead code.

**Failure path.** Open a player whose Skipper take will not generate — a model
error, a missing key, a persistently `pending` cache row. The client polls
`/api/player/{id}` roughly every 2.5s indefinitely. Per the audit, each GET
queues a background OpenRouter take (`sandlot_api.py:840-847`). No attacker
needed; a single open tab does it.

**Brief.** Hoist the counter into a `useRef` so it survives re-renders, or drop
`data` from the deps and read it via ref. Then add a server-side guard so
repeated GETs for the same player cannot each enqueue generation. Add a
regression test — this is the second instance of this exact bug class in the
file (see R-note under [R15](#r15)).

---

### R8 — `/api/trades/grade` unauthenticated {#r8}

**Severity:** Med-High · 🔶 Reported · `sandlot_api.py:617`

Two model completions per novel give/get package, plus a
`recommendation_receipts` row per call. Ledger *integrity* was reported sound —
`scope_key` embeds the package (`sandlot_receipts.py:219`) and these cannot
supersede owner receipts — so this is a cost and volume issue, not a
correctness one.

**Brief.** Confirm the two-completion count, then apply the same shared-secret
gate as R1/R2/R12. Consider a cheap dedup on `scope_key` so a replayed package
returns the cached grade instead of re-billing.

---

### R12 — `DELETE /api/skipper/messages` is unauthenticated {#r12}

**Severity:** Medium · ✅ Verified · `sandlot_api.py:897-904`

No auth dependency; calls `clear_chat_messages`. One `curl -X DELETE` erases
your chat history. Low blast radius (chat history is not load-bearing for any
decision), but it is a stranger-reachable destructive action.

**Brief.** Gate it. Bundle with R1/R2/R8 as one change.

---

## Correctness and trust

### R4 — Two projection models that never reconcile {#r4}

**Severity:** High · ✅ Verified

Sandlot projects the same quantity — expected points from a hitter over a period —
in two places, with structurally different math.

The Monday optimizer does it correctly (`sandlot_lineup.py:91-93`):

```python
    share = min(1.0, games_recent / team_games_recent) if team_games_recent else 0.0
    return team_games_next * share
```

The matchup projection does not (`sandlot_matchup.py:2288-2290`):

```python
        games = _projection_opportunities(row, period_end, period_start)
        fppg = _row_fppg(row)
        delta = fppg * games
```

`_row_fppg` (`:2464-2475`) reads Fantrax's FP/G, which is **points per game
played**. `_projection_opportunities` (`:2482-2495`) returns, for non-pitchers,
the player's **team's** remaining scheduled games. There is no playing-time
share term.

**Failure path.** For an everyday starter the two denominators coincide and
nothing is visibly wrong. For a platoon bat, an injury-returnee, or a bench
piece, team games exceed games played and the projection inflates. The error is
directionally biased — it always over-projects part-time players, which is
exactly the direction that manufactures a false "this player will out-earn your
starter" swap card.

**Brief.** Port the `share` computation from `sandlot_lineup.py:91-93` into
`sandlot_matchup.py`'s hitter path. The code already exists and is tested. Then
decide the larger question: these two models should not be independent
implementations at all. Extracting one shared projection function is the real
fix; the port is the stopgap.

---

### R5 — Win probability is systematically overconfident {#r5}

**Severity:** High · ✅ Verified · `sandlot_matchup.py:2284-2296`

```python
        mean_delta += delta
        variance += max(1.0, abs(fppg)) * games
```

Variance is set equal to the mean. That is a Poisson assumption, and weekly
fantasy point totals are far more dispersed than Poisson — they are sums of
heavy-tailed per-game outcomes. The audit measured σ(margin) ≈ 35 where the
realistic figure is nearer 90.

**Failure path.** Every win probability the UI shows is pushed toward the
extremes. A genuine 65% reads as 85%. This is worse than showing no probability
at all, because it invites confident action on a number that has never been
validated against a single realized outcome — see [R6](#r6).

**Brief.** Two options. Cheap: estimate per-player weekly variance empirically
from the game logs already being fetched, and sum those. Cheaper still, and
arguably more honest today: stop displaying a probability until [R6](#r6) is
fixed and you can calibrate against real outcomes. Note the comment above the
variance line shows this was already adjusted once to avoid a degenerate case —
so the line has been looked at, but the distributional assumption behind it has
not been revisited.

---

### R9 — No sample-size shrinkage in waiver ranking {#r9}

**Severity:** Med-High · 🔶 Reported · `sandlot_waivers.py`

The audit reports no games-played field exists in the waiver path at all, so a
3-game callup at 18 FP/G outranks an established contributor.

**Brief.** Confirm the absence first. Then apply Marcel-style shrinkage toward a
positional mean, weighted by games played. This is ~15 lines and is the single
highest-value change to waiver output. Rank on expected points over the
remaining period rather than on rate.

---

### R11 — Three freshness implementations, two threshold sets {#r11}

**Severity:** Medium · ✅ Verified

| Location | Fresh | Stale/Old |
|---|---|---|
| `sandlot_api.py:42-43` | ≤ 18h | > 36h is "old" |
| `player_service.py:542-546` | ≤ 30 min | ≤ 24h stale, then old |
| `sandlot_waivers.py:894-899` | ≤ 30 min | ≤ 24h stale, then old |

Same `taken_at`, same UI, three answers. With a twice-daily cron, a snapshot is
older than 30 minutes almost all day — so the Today bar reads green "Healthy"
while the player sheet reads amber "stale" for the identical snapshot.

**Brief.** One `freshness()` in `sandlot_config.py`, one threshold pair derived
from the actual cron cadence, three call sites updated. Decide the real
semantics first: with a 2×/day cron, a 30-minute freshness window is
meaningless — it will essentially never be satisfied.

---

### R13 — Fabricated player quotes are reachable {#r13}

**Severity:** Medium · 🔶 Reported · `v2-pages.jsx:4531,4761-4763`

`V2ProfileClips` falls back to `V2_PROFILE_PLACEHOLDER_CLIPS` when `media` is
absent — reportedly including an invented Dave Roberts quote. The repo's own
`player-sheet.spec.ts:41-53` mock omits `media`, which means the test suite
renders them.

**Failure path.** Any player whose media fetch fails displays plausible-looking
fabricated quotes attributed to real people, with no visual distinction from
real content. This is the highest *trust* severity in the register even though
its technical severity is moderate — a tool that invents attributed quotes is
one you stop believing entirely.

**Brief.** Verify, then delete the placeholder constant and render an explicit
empty state. Placeholder content that is indistinguishable from real content
should not exist in a build that ships.

---

### R21 — `v2NormalizeSnapshot(undefined)` defaults to "fresh" {#r21}

**Severity:** Low · 🔶 Reported · `v2-pages.jsx`, `sandlot_api.py:593`

A 200 response carrying `snapshot: null` reportedly renders a green dot, zero
players, and the string `"Snapshot fresh old."` Failure presented as success.

**Brief.** Default unknown state to `unknown`, not `fresh`, and render a real
empty state. The garbled string suggests two independent label paths are
concatenating — worth a look while you are there.

---

## Roadmap blockers

These three are why "add ML" and "make it autonomous" are not currently
actionable. They are the most important entries in this document.

### R6 — The learning loop cannot close: labels vanish silently {#r6}

**Severity:** High · ✅ Verified · `sandlot_db.py:920-937`, `sandlot_receipts.py:350-362`

```sql
            JOIN lineup_period_evidence l
              ON l.league_id = r.league_id
             AND l.team_id = r.team_id
             AND l.period_start = r.period_start
             AND l.period_end   = r.period_end
```

Receipts are written with calendar Monday–Sunday bounds. `lineup_period_evidence`
records Fantrax's actual scoring-period bounds. When Fantrax runs an extended
period — such as the July 13–26 matchup described in your own `STATUS.md` — the
dates differ and the row **does not join**.

**Failure path.** The receipt does not surface as pending, or errored, or
awaiting evidence. It is simply absent from
`receipts_missing_outcome_evaluation` and from every downstream count. The
system reports "zero scored counterfactual weeks" and reads as *not enough time
has passed*, when the actual cause is a join that silently discards rows. You
could wait a full season and the number would stay at zero.

This is the finding I would fix first in the entire register. Everything in the
ML and autonomy roadmap is downstream of it.

**Brief.** Bind receipts to Fantrax period *identity* rather than to calendar
dates — carry the period key from the evidence record into the receipt at
creation and join on that. Backfill is likely impossible given [R16](#r16), so
land it before more periods elapse. Add a test with a multi-week period fixture;
the current fixtures are all clean 7-day weeks, which is why this survived 588
tests.

---

### R10 — Probability release gate is structurally unreachable {#r10}

**Severity:** Med-High · 🔶 Reported

The audit reports executing the gate code and finding the release cohort always
empty: relievers guarantee `opportunity_completeness != "complete"`, so a
condition the system waits on can never be satisfied.

**Failure path.** Same shape as R6 — a gate that appears to be accumulating
evidence is in fact waiting on an impossibility, and reports its state in a way
indistinguishable from "not yet."

**Brief.** Re-verify by executing the predicate against a production snapshot.
If confirmed, either model reliever cadence (the audit notes the needed code
already exists at `sandlot_lineup.py:88`) or redefine completeness to exclude
relievers from the requirement. Then add an assertion that fails loudly when a
gate's cohort is empty for N consecutive periods — the general lesson from R6
and R10 is that **silent-empty is the dominant failure mode in this system**.

---

### R16 — Retention is ~15 days {#r16}

**Severity:** Medium · ✅ Verified · `sandlot_db.py:2204`, `sandlot_refresh.py:117`

`prune_successful_snapshots(keep=30)`, driven by `SANDLOT_KEEP_SNAPSHOTS`
defaulting to `"30"`. At the cron's twice-daily cadence that is roughly 15 days
of history.

Harmless in isolation; serious in combination with R6, because it caps how far
back any backfill could ever reach.

**Brief.** Raise materially before fixing R6, or the fix has nothing to work
with. Snapshots are JSONB and small; the storage argument for 30 is weak
relative to the analytical cost.

---

## Process and supply chain

### R7 — The E2E suite goes green when production breaks {#r7}

**Severity:** High · ✅ Verified · `tests/playwright/specs/`

```
roster.spec.ts:14         test.skip(rows.length === 0, 'Snapshot has no roster rows.');
attention-api.spec.ts:15  test.skip(res.status() === 503, 'Target deploy has no successful snapshot (or no DB).');
league.spec.ts:13         test.skip(teams.length === 0, 'Snapshot has no standings; nothing to assert.');
```

Nine data-conditional skips across five specs.

**Failure path.** `playwright.yml` runs daily against Railway. Its purpose is to
catch a broken scrape. But an empty roster, an all-503 API, or missing standings
— the precise symptoms of a broken scrape — each cause a **skip, not a
failure**. The suite is structurally incapable of detecting the thing it was
built to detect, and reports green while doing so.

**Brief.** Split the suite. A scheduled production run should assert
`rows.length > 0` and fail on 503. A PR-time run against a fixture can keep the
conditional guards. Do not simply delete the skips — they exist because the same
specs run in both contexts; the fix is separating the contexts.

Related: the one true contract test (`attention-api.spec.ts`) covers
`/api/attention` and `/api/hot-swaps/latest`, and the audit reports neither
appears in any `fetch()` in the JSX. `/api/snapshot/latest` — what the UI
actually renders — is unguarded.

---

### R14 — No dependency pinning on the Python side {#r14}

**Severity:** Medium · ✅ Verified · `requirements.txt`

All 13 dependencies are unbounded `>=`, with no lock file. The JS side is
correctly locked (`package-lock.json`, React pinned exactly).

**Failure path.** A malicious or merely broken release of any transitive
dependency runs in a process holding `DATABASE_URL`, Fantrax session cookies,
and `OPENROUTER_API_KEY`. This is the most realistic path to actual credential
loss in the whole register — not because it is likely, but because every other
path was closed off competently.

**Brief.** `pip freeze` into a lock, or adopt `uv`/`pip-tools`. Note
`.python-version` already exists, so the runtime is pinned and only the packages
are not.

---

### R17 — `app.js` is a committed build artifact {#r17}

**Severity:** Medium · 🔶 Reported

`web/sandlot/index.html:50` loads a single esbuild bundle; `Procfile` has no
build step, so the committed bytes are what production serves.

The audit found the bundle currently in sync (all 37 post-migration commits
touching `web/sandlot/` also touched `app.js`) and a freshness check at
`ci.yml:69-71`. Two residual concerns: whether that check is a *required* status
check (branch protection is not visible in-repo), and that `local-frontend-e2e`
rebuilds before serving, so it would pass over a stale artifact.

**Failure path.** Edit `v2-pages.jsx`, skip the rebuild, merge. Production
serves old code while source review looks correct. Cache-busting works properly,
which makes it *worse* — the symptom presents as "my fix didn't work," never as
"stale cache."

**Brief.** Confirm `frontend-build` is a required check in branch protection. If
not, make it one. Consider building at deploy time instead and removing `app.js`
from version control — it also removes a permanent source of diff noise.

---

### R18 — Cookie files written world-readable {#r18}

**Severity:** Low-Med · ✅ Verified

`auth.py:192` and `import_chrome_cookies.py:62` both use a bare
`COOKIE_PATH.write_text(...)`, inheriting the default umask (typically `0644`).

The correct pattern already exists in this repo, at
`import_fantrax_cookies_manual.py:110-121` — `0700` on the directory, atomic
write to a temp file, `0600` on both temp and final.

**Brief.** Copy that function's approach into the other two call sites. Fifteen
minutes, and the reference implementation is already written and reviewed.

---

### R19 — Model-supplied citation URLs are unvalidated {#r19}

**Severity:** Low-Med · ✅ Verified · `sandlot_skipper.py:1258` → `v2-pages.jsx:5692`

`_extract_url_citations` accepts any `url` from the model's annotation with only
a falsy check. It renders straight into `href={source.url}`. A grep for scheme
validation anywhere in that path returns nothing.

Requires a malicious search result *and* a user click, so severity stays low —
but if clicked, a `javascript:` URL executes with access to
`localStorage.sandlot_refresh_token`.

**Brief.** Allowlist `http`/`https` at extraction time in `sandlot_skipper.py`.
One line, server-side, covers every render site.

---

### R20 — `run_refresh()` is unguarded against DB failure {#r20}

**Severity:** Low-Med · ✅ Verified

`sandlot_refresh.py:49-50` calls `load_dotenv()` and `init_schema()` outside any
try block. `sandlot_api.py:541` and `sandlot_cron.py:21` both call
`run_refresh()` bare, and `sandlot_api.py:542` then calls
`latest_successful_snapshot()` unguarded.

**Failure path.** Postgres is briefly unavailable → unhandled exception → a bare
500 from the API and a traceback-exit from cron, instead of the structured
`RefreshResult` the module returns everywhere else. No traceback reaches the
client (FastAPI does not expose them by default), so this is a robustness and
observability defect rather than a leak.

**Brief.** Move the three calls inside the existing try, return a failed
`RefreshResult`. Note: a near-identical fix exists uncommitted on
`codex/fix-5-player-index` — worth reading before rewriting it.

---

### R22 — Branch sprawl and stale primary checkout {#r22}

**Severity:** Medium · ✅ Verified · process, not code

- 80+ branches, most `codex/*` or `agent/*`.
- Many were checked out into `/private/tmp/...` worktrees, which do not survive a reboot.
- The primary working directory sits on a branch 114 commits and ~41k lines behind `origin/main`.
- The `CLAUDE.md` in that checkout describes a frontend architecture retired two months ago.

**Failure path.** This is not hypothetical. It cost the first phase of this
audit: four agents were dispatched against the stale tree and had to be killed,
and one subsequent agent was briefed with frontend constraints that had been
false since May. Any agent session started from this checkout inherits the same
errors.

**Partially remediated 2026-07-24.** The `/private/tmp` worktrees no longer
exist — the directories were already gone and `git worktree prune` cleared the
stale metadata, so nothing remains to salvage from them. Any work that lived
only in those directories and was never pushed is already lost; every branch
listed below still has an `origin/` counterpart, so the committed work survives.

**Brief — remaining.** Move the primary checkout to `main`. Delete branches
already merged into `origin/main` (`git branch --merged origin/main`), local
first, then their remote counterparts once you are satisfied. Then reconcile
`CLAUDE.md` — see the companion [doc-drift.md](doc-drift.md), where this has its
own register.

---

## Checked and sound

Verified as genuinely well-built. Do not spend Fable's time here.

**The write boundary holds — structurally, not just by flag.** Every FXPA method
name in the repo is a hardcoded read (`fantrax_data.py:112,177,252,440,602,727,2106,2347`);
no `setLineup`-class string exists anywhere. `writes_enabled: True` appears only
in negative tests. `prepare_dry_run_request` (`sandlot_execution.py:141`)
re-derives the proposal server-side and whole-object-compares the confirmation,
so there is no signed payload to replay or field-swap. Credentials use
`hmac.compare_digest` after strict 64-hex validation (`:81`); owner and runner
roles are enforced distinct (`:101`); the lease is a SQL compare-and-swap
(`sandlot_db.py:1765`), so there is no TTL race. Flipping
`SANDLOT_EXECUTION_DRY_RUN_ENABLED` achieves nothing without both plaintexts,
and even then the state machine terminates at a preflight evidence row. **An
attacker who fully owned the Railway environment would have to add code to touch
Fantrax.** That is a strong result and it was clearly deliberate.

Also verified sound:

- **All four documented safety properties are actually enforced** — sanitized-receipt allowlist (`sandlot_api.py:1493-1560`), loopback-only owner bridge enforced server-side three ways plus a nonce (`sandlot_owner_bridge.py:25,188-193,229-246,663`), no owner bearer in the browser bundle, and `fantrax_changed`/`writes_enabled` hard-set rather than merely asserted (`sandlot_api.py:391-392`).
- **No secret has ever been committed** across all 156 commits; every secret-shaped hit in history is a placeholder.
- **All SQL is parameterized** — zero string interpolation anywhere in `sandlot_db.py` (2230 lines).
- **No SSRF** — every outbound URL derives from a constant.
- **No `dangerouslySetInnerHTML`** anywhere in the frontend.
- **Prompt injection is display-only** — no tool-call handling; model output reaches no DB key, URL, or ledger field.
- **The API↔UI contract is healthy** — 17 surfaces and 129 keys traced, no naming drift, no unguarded deep access, no `NaN` paths.
- **Test quality is real, not tautological** — `test_sandlot_receipts.py` drives the app through `TestClient` asserting negative invariants, hash stability under reordering, fail-closed behavior on non-finite values, and two-worker Postgres concurrency.
- **CI workflows are clean** — no `pull_request_target`, no secrets in logs.

---

## Suggested order

1. **R1** — one env var, verifiable today, largest single exposure.
2. **R6** — the roadmap is blocked behind it and every elapsed period is unrecoverable.
3. **R2 + R3** — bound the spend; R3 needs no attacker.
4. **R7** — until fixed, no green run means anything.
5. **R4 + R5 + R9** — projection quality; R4 is a code port that already exists.
6. Everything else.
