# Sandlot security audit — `origin/main` @ `bd54135`

Read-only audit of the owner's own project, at the owner's request. No files in the
audit tree were modified. No network requests were made to Fantrax or OpenRouter.
Audit tree:
`<audit worktree>`

**Headline:** the write boundary holds, and holds structurally, not just by flag.
The real exposure is that every money-spending and DB-mutating product route is
reachable by anyone who knows the Railway hostname, with no rate limit and no
size cap on cumulative state.

---

## 1. Route exposure table

All paths served by `sandlot_api.py`. "Auth" = server-side credential check.
"Cost" = does an unauthenticated caller cause the owner to spend money (OpenRouter
tokens, Selenium/Fantrax scrape, third-party API quota).

| Method | Path | Line | Auth | Cost | Mutates | Destructive |
|---|---|---|---|---|---|---|
| GET | `/api/health` | `sandlot_api.py:124` | none | none | `init_schema()` (idempotent DDL) | no |
| GET | `/api/snapshot/latest` | `:151` | none | none | no | no |
| GET | `/api/attention` | `:163` | none | none | no | no |
| GET | `/api/hot-swaps/latest` | `:200` | none | none | no | no |
| GET | `/api/win-this-week/latest` | `:217` | none | none | no | no |
| GET | `/api/recommendation-receipts/latest` | `:229` | none | none | no | no |
| GET | `/api/recommendation-outcomes/recent` | `:240` | none | none | no | no |
| GET | `/api/recommendation-learning` | `:268` | none | none | no | no |
| GET | `/api/matchup-probability-readiness` | `:285` | none | none | no | no |
| POST | `/api/recommendation-receipts/{id}/decision` | `:364` | **owner SHA-256 bearer** (`_require_hashed_role`) | none | terminal ledger decision | no (immutable, one-shot) |
| GET | `/api/action-proposals/{proposal_id}` | `:396` | none | none | no | no |
| POST | `/api/execution-requests` | `:421` | **owner bearer + `SANDLOT_EXECUTION_DRY_RUN_ENABLED` + distinct digests** | none | inserts dry-run request | no |
| GET | `/api/execution-requests/{id}` | `:454` | **owner bearer + flag** | none | expiry normalization only | no |
| POST | `/api/execution-requests/claim` | `:466` | **runner bearer + flag** | none | claims one request | no |
| POST | `/api/execution-requests/{id}/preflight` | `:487` | **runner bearer + flag + live lease CAS** | none | terminal preflight row | no |
| POST | `/api/refresh` | `:536` | **`SANDLOT_REFRESH_TOKEN` if set — fails open if unset** | **HIGH** — full Fantrax scrape + MLB API + snapshot write | yes, everything | prunes old snapshots (`SANDLOT_KEEP_SNAPSHOTS`) |
| GET | `/api/waiver-swaps/latest` | `:598` | none | none (cached-AI overlay only) | no | no |
| POST | `/api/trades/grade` | `:617` | **none** | **HIGH** — 2 OpenRouter completions per uncached package | `ai_briefs` + `recommendation_receipts` row | no (scope-keyed, cannot supersede owner receipts) |
| GET | `/api/trades/incoming` | `:663` | none | none | no | no |
| GET | `/api/skipper/options` | `:787` | none | none | no | no |
| GET | `/api/skipper/messages` | `:810` | **none** | none | no | no — but discloses the owner's full chat history |
| GET | `/api/player/{fantrax_id}` | `:834` | none | **MEDIUM** — background `refresh_cached_profile` on cache miss (MLB API + one OpenRouter take per player/snapshot) | profile/take caches | no |
| POST | `/api/player/{fantrax_id}/refresh` | `:851` | **none** | **MEDIUM** — synchronous MLB Stats API fan-out every call | profile/media/mlb-id caches | no |
| GET | `/api/team/{team_id}/roster` | `:856` | none | none | no | no |
| DELETE | `/api/skipper/messages` | `:897` | **none** | none | deletes rows | **YES — wipes chat history** |
| POST | `/api/skipper/messages` | `:907` | **none** | **HIGH** — unbounded-output OpenRouter stream, allowlisted premium models, reasoning=high, web search | appends 2 chat rows + projection log | no, but permanently inflates every future prompt |
| GET | `/` | `:1750` | none | none | no | no |
| GET | `/*` (static mount) | `:1759` | none | none | no | no |

Notes on the auth column:
- No CORS middleware exists anywhere in the app. This is a *good* accident: a
  victim's browser cannot be used to drive these routes cross-origin, because
  `DELETE` and `application/json` `POST` both require a CORS preflight that gets
  no `Access-Control-Allow-Origin` back. Everything below is therefore a
  *direct* attack against the public hostname, not CSRF.
- `_require_hashed_role` (`sandlot_api.py:1738` → `sandlot_execution.py:63`) fails
  **closed** when the digest env var is absent or malformed (503). Verified.
- `_require_execution_role` (`sandlot_api.py:1730`) additionally requires the
  feature flag and two *distinct* valid digests. Verified, covered by
  `tests/test_sandlot_execution.py:797,802,811`.

---

## 2. Verdict on the write boundary

**It holds. VERIFIED, with high confidence.** This is the strongest part of the
codebase and I could not construct a bypass.

Evidence, in order of how much it matters:

1. **The capability does not exist in the process.** Every Fantrax API method
   name in the entire repo is a hardcoded read: `getTeamRosterInfo`,
   `getPlayerStats`, `getLiveScoringStats`, `getPendingTransactions`
   (`fantrax_data.py:112,177,252,440,602,727,2106,2347`). There is no
   `setLineup`/`executeClaim`/`proposeTrade` string anywhere. `_direct_fxpa_request`
   (`fantrax_data.py:75`) takes `method` as a parameter, but all four call sites
   pass string literals — no data-derived method name reaches it.
2. **`writes_enabled` is never assigned `True` in production code.** Grepping the
   whole tree, the only `writes_enabled: True` and `executable: True` literals are
   inside `tests/test_sandlot_readonly_monitor.py` (negative tests asserting the
   monitor screams). `sandlot_matchup.py:1249,1569` hardcode `executable: False`;
   `sandlot_execution.public_request` (`sandlot_execution.py:397`) hardcodes
   `"writes_enabled": False` on every response regardless of row contents.
3. **Flipping `SANDLOT_EXECUTION_DRY_RUN_ENABLED=true` gets an attacker nothing**
   without *also* holding the owner plaintext and the runner plaintext. And even
   with all three, the terminal state machine has no `executing` state and no
   route that calls Fantrax after preflight — the only reachable outcome is a
   `preflight_passed`/`preflight_failed` evidence row.
4. **No HMAC-signed-payload replay surface exists**, because there is no signed
   payload. The design is stronger: `prepare_dry_run_request`
   (`sandlot_execution.py:104`) re-derives the proposal server-side from the
   latest snapshot and requires `submitted["confirmation"] == expected` by whole-
   object equality (`:141`), plus internal consistency between
   `contract.slot_moves`, `review.slot_moves` and `expected.slot_moves` (`:147`).
   Field-swapping is impossible; there is nothing to swap that isn't re-derived.
5. **Credential comparison is constant-time and correctly ordered.**
   `require_hashed_bearer` (`sandlot_execution.py:63`) validates the configured
   digest is exactly 64 lowercase hex *before* comparing, then uses
   `hmac.compare_digest(token_digest(provided), expected)` (`:81`).
   `distinct_role_credentials_configured` (`:94`) refuses identical owner/runner
   digests. `finish_execution_preflight` compares the lease by digest inside the
   SQL `UPDATE ... WHERE lease_token_hash = %s AND lease_expires_at > now() AND
   expires_at > now() RETURNING *` (`sandlot_db.py:1765`) — a compare-and-swap, so
   there is no TTL/lease TOCTOU race.
6. **Replay of a signed request is not a thing, and re-creation is idempotent.**
   `request_expires_at = min(now+120s, deadline, eligibility_deadline)`
   (`sandlot_execution.py:187`) — a near-stale snapshot cannot buy another 120
   seconds, which is exactly the subtle bug I went looking for and it is already
   closed and tested (`tests/test_sandlot_execution.py:211,256`).

The one thing the boundary does *not* cover, and correctly does not claim to, is
the terminal ledger write at
`POST /api/recommendation-receipts/{id}/decision`. That route is owner-bearer
gated and fails closed. It is not a Fantrax mutation.

**Bottom line on #2: an attacker who fully owns the Railway environment still
cannot make this codebase change anything in Fantrax. They would have to add
code.**

---

## 3. Findings, by real-world consequence

### F1 — `/api/refresh` fails open when `SANDLOT_REFRESH_TOKEN` is unset, and STATUS.md says it is unset — SUSPECTED (code path VERIFIED, deployment state unverified)

`sandlot_api.py:1718`
```python
def _require_refresh_token(request: Request) -> None:
    expected = os.environ.get("SANDLOT_REFRESH_TOKEN")
    if not expected:
        return                      # <-- fail open
    ...
    if provided != expected:        # <-- not constant-time
        raise HTTPException(status_code=401, ...)
```

`STATUS.md:228` states: *"Railway tokens (`SANDLOT_ACTIONS_TOKEN`,
`SANDLOT_REFRESH_TOKEN`) unset."* That bullet is stale in other respects (it
references `SANDLOT_ACTIONS_TOKEN`, which no longer exists in the codebase), so I
cannot confirm the current Railway value from the repo — this needs a
`railway variables` check.

**Attack path:** stranger finds `https://web-production-90664.up.railway.app`
(it is hardcoded in `.github/workflows/playwright.yml`, `sandlot_owner_bridge.py:27`,
`scripts/sandlot_readonly_monitor.py:23`, and in the repo's docs). They
`curl -X POST .../api/refresh` in a loop. Each call takes the Postgres advisory
lock (`sandlot_refresh.py:REFRESH_LOCK_ID`) so only one runs at a time — that
correctly prevents parallel scrapes — but serially they drive a continuous
Fantrax scrape from the Railway egress IP using the owner's stored session
cookies, plus MLB Stats API fan-out, plus snapshot pruning.

**Impact for this owner:** sustained automated hammering of Fantrax with the
owner's authenticated session is the single most plausible route to a Fantrax
account rate-limit, cookie invalidation, or account flag. Also drives Railway
compute cost and continuously churns the snapshot table. It does not expose the
Fantrax password.

**Severity: HIGH if the var is unset; N/A if set.** Verify first.

**Fix:** two lines. Require the token unconditionally when `DATABASE_URL` is set
(i.e. in production), and switch to `hmac.compare_digest`. The current "allowed
without a header locally" convenience is preserved by keying on `DATABASE_URL`
rather than on the token's own presence.

---

### F2 — `POST /api/skipper/messages` is an unauthenticated, uncapped OpenRouter proxy with permanent cost amplification — VERIFIED

`sandlot_api.py:907`. No auth. `SkipperClient.stream` (`sandlot_skipper.py:1084`)
sets `temperature` but **no `max_tokens`** — contrast with every other call site,
which caps it (`sandlot_trades.py:1311` `max_tokens=260`, `:1399` `160`,
`player_service.py:433` `220`). The caller may select
`deepseek/deepseek-v4-pro` or `z-ai/glm-5.2` from the allowlist
(`sandlot_skipper.py:35`), request `reasoning_effort="high"`, and leave
`web_search=True` (default, `sandlot_api.py:784`), which attaches the
`openrouter:web_search` tool with up to 8 total results
(`sandlot_skipper.py:43-44`) — a per-request billed search cost on top of tokens.

The amplification is the part that matters:
- `sandlot_api.py:936` appends the user message to the DB **before** any model
  call, unauthenticated.
- `sandlot_db.list_chat_messages` (`sandlot_db.py:1825`) has **no `LIMIT`**.
- `sandlot_skipper.build_messages` (`:1028`) replays **every** history row into
  every subsequent prompt.

So N attacker messages of 4000 chars each permanently add ~N×1k tokens to the
input of every future Skipper turn — including the owner's own. Cost grows
quadratically in attacker effort and never decays. Tier-3 detection
(`sandlot_skipper.py:205`) is trivially triggered by including the word "trade"
or "league", which loads every team's roster into context on top.

**Attack path:** stranger POSTs `{"content": "<4000 chars> trade",
"model": "deepseek/deepseek-v4-pro", "reasoning": true,
"reasoning_effort": "high"}` in a loop against the public host.

**Impact:** direct, unbounded charges to the owner's OpenRouter account, plus
permanent degradation of the owner's own Skipper (slower, more expensive, and
the poisoned history is visible in the UI).

**Severity: HIGH** (money).

**Fix (smallest thing that works, no new dependency):**
1. Add a `max_tokens` to `stream()` matching the other call sites (~1200).
2. Window the history: `LIMIT`-and-reverse the last ~40 rows in
   `list_chat_messages`, or slice in `build_messages`. This alone kills the
   amplification.
3. Gate the whole `/api/skipper/*` write surface behind the same shared secret as
   `/api/refresh` — the frontend already reads `sandlot_refresh_token` from
   localStorage (`web/sandlot/v2-pages.jsx:397`), so the plumbing exists.

---

### F3 — `POST /api/trades/grade` is an unauthenticated OpenRouter spender and an unbounded ledger-row writer — VERIFIED

`sandlot_api.py:617`. No auth. Per call:
- `sandlot_trades.grade_offer` → `_load_or_generate_rationale`
  (`sandlot_trades.py:1389`) and the counter brief (`:1300`) each check
  `ai_briefs` keyed by `(snapshot_id, brief_type, subject_key)` + `input_hash`,
  and on miss call `SkipperClient().complete()` — **two** completions per novel
  package.
- `sandlot_db.record_recommendation_receipt` (`sandlot_api.py:649`) then inserts a
  `recommendation_receipts` row whose `scope_key` embeds the exact give/get id
  lists (`sandlot_receipts.py:219`).

**Attack path:** stranger reads `/api/snapshot/latest` (public) to enumerate
player IDs, then walks distinct `give`/`get` combinations (1–5 each side). Every
novel combination is a guaranteed cache miss.

**Impact:** money (2 completions per request, sustainable indefinitely) and
unbounded `recommendation_receipts` + `ai_briefs` growth on a Railway Postgres
plan.

**What is NOT broken, and I checked specifically:** because `scope_key` includes
the package (`sandlot_receipts.py:219`), an attacker's junk grade **cannot**
supersede the owner's real active receipts. The supersede path
(`sandlot_db.py:655-673`) only touches rows sharing the exact scope key, and
`record_recommendation_receipt` refuses to supersede a *decided* receipt
(`:663`). `/api/recommendation-receipts/latest` is `Literal["monday_lineup"]`-
restricted (`sandlot_api.py:230`), so trade spam never surfaces there. Ledger
integrity is intact; only ledger *volume* is attackable.

**Severity: MEDIUM-HIGH** (money + storage; no integrity loss).

**Fix:** same shared-secret gate as F2, or a coarse per-IP token bucket in front
of the three costly POSTs.

---

### F4 — `DELETE /api/skipper/messages` lets any stranger wipe the owner's chat history — VERIFIED

`sandlot_api.py:897` → `sandlot_db.clear_chat_messages`. No auth, no
confirmation, no soft-delete, no backup.

**Attack path:** one `curl -X DELETE https://.../api/skipper/messages`.

**Impact:** permanent loss of the owner's Skipper conversation history. Not a
credential or a Fantrax change, but it is the only genuinely *destructive*
unauthenticated route in the app, and it is a single request.

Paired disclosure: `GET /api/skipper/messages` (`:810`) returns the entire
history to anyone. For a fantasy-baseball chat that's low-stakes, but note that
whatever the owner types into Skipper is world-readable.

**Severity: MEDIUM.**

**Fix:** gate `DELETE` behind the same secret used for `/api/refresh`. One line.

---

### F5 — Model/scraped-content URLs reach `href` with no scheme allowlist — VERIFIED (chain is long; impact is real but conditional)

`web/sandlot/v2-pages.jsx:5692`
```jsx
<a key={`${source.url}-${index}`} href={source.url} target="_blank" rel="noreferrer">
```
`source.url` originates from the model's `url_citation` annotations, extracted
verbatim with no validation at `sandlot_skipper.py:1257-1261`, persisted into
`chat_messages.metadata` (`sandlot_api.py:1033`), and replayed on history load.
React does not sanitize `href`; a `javascript:` URL renders and executes on
click (React only logs a dev warning).

`web/sandlot/v2-pages.jsx:4907` has the same shape for `clip.url` from MLB Stats
API media.

**Attack path:** attacker POSTs a Skipper message with web search enabled that
steers the model — or plants content on a page the search surfaces — into
emitting a `javascript:` citation. It persists in the owner's history. The owner
must then click it. Payload runs on the app origin and can read
`localStorage.sandlot_refresh_token`.

**Impact:** exfiltration of the refresh token, which is exactly the credential
gating the Fantrax scrape. Low probability, non-trivial consequence.

**Severity: LOW-MEDIUM.** This is the only real XSS-shaped gap in the frontend;
there is no `dangerouslySetInnerHTML` or `innerHTML` anywhere.

**Fix:** one helper — reject any URL not matching `^https?:` at
`sandlot_skipper.py:1257` (server side, so it also cleans persisted history going
forward) and defensively at both `href` sites.

---

### F6 — Prompt injection cannot reach a privileged action — VERIFIED, reported as a *negative* finding

I traced this deliberately because the brief asked. LLM output in this app can do
exactly three things: be stored as text in `chat_messages` / `ai_briefs` /
`player_takes`, be displayed, and (via F5) supply an `href`. There is **no
tool-call handling** — `grep` for `tool_call` / `function_call` / `exec(` /
`eval(` in `sandlot_skipper.py` returns nothing; the `openrouter:web_search`
tool is executed provider-side and the server only reads citations back out. No
model output is ever parsed into a DB write key, an outbound URL, a receipt
field, or a ledger mutation. The trade-research path additionally *buffers* model
text and reconstructs the visible answer from deterministic evidence before
emitting anything (`sandlot_api.py:1017-1024`).

**Prompt injection here is display-only, with the single caveat of F5.** No
further work needed.

---

### F7 — `.cookies/fantrax.json` is written world-readable by two of three writers — VERIFIED

`auth.py:189`
```python
def _save_cookies(cookies: list[dict]) -> None:
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_PATH.write_text(json.dumps(cookies, indent=2))   # default umask -> 0644
```
`import_chrome_cookies.py:61-62` has the same defect.
`import_fantrax_cookies_manual.py:113-121` does it correctly (`0o700` dir,
`0o600` file, atomic temp+rename, never prints values) — so the hardened pattern
already exists in-repo and just isn't used by the other two writers.

**Impact:** live Fantrax session cookies readable by any other local user or any
process running as another account on the Mac. Local-only, but these cookies are
equivalent to logged-in access to the real Fantrax account.

**Severity: LOW-MEDIUM.**

**Fix:** copy the three `chmod` lines from `import_fantrax_cookies_manual.py:113-121`
into `auth._save_cookies` and `import_chrome_cookies.py`.

---

### F8 — Exception strings are returned to unauthenticated clients — VERIFIED

Recurring pattern, e.g.:
- `sandlot_api.py:156` `detail=f"Database unavailable: {exc}"`
- `sandlot_api.py:136` `/api/health` returns `"error": str(exc)` with HTTP 200
- `sandlot_api.py:893` `raise HTTPException(status_code=500, detail=str(exc))`
  for *any* failure in the player-profile path

**Impact:** psycopg operational errors typically embed the Postgres host, port,
and username. That is internal-topology disclosure, not credential disclosure —
the password is not in the message. FastAPI is not in debug mode
(`sandlot_api.py:46`), so unhandled exceptions return a generic 500 with no
traceback. Several of the newer handlers already do this right
(`:281`, `:296`, `:622`, `:659` log the exception and return a fixed string) —
the leaky ones are the older routes.

**Severity: LOW.**

**Fix:** mechanical — replace `detail=f"...: {exc}"` with `log.exception(...)` +
a fixed message, matching the pattern already used at `:281`.

---

### F9 — Python dependencies are unpinned with no lockfile — VERIFIED

`requirements.txt` is entirely `>=` constraints (`fantraxapi>=0.2.0`,
`selenium>=4.15`, `webdriver-manager>=4.0`, `pycookiecheat>=0.7`,
`psycopg[binary]>=3.2`, `pybaseball>=2.2`, …). There is no `requirements.lock` /
`pip-compile` output. Every Railway build resolves fresh.

**Impact:** a compromised release of any of these — several are small,
single-maintainer packages — executes with `DATABASE_URL`, the Fantrax session
cookies in Postgres, and `OPENROUTER_API_KEY` in the environment. This is the
most realistic path by which the owner loses actual credentials, and it does not
require anyone to attack the app at all.

Contrast: the JS side is correct — `package-lock.json` is committed, `npm ci` is
used in CI, `react`/`react-dom` are exact-pinned.

**Severity: MEDIUM** (low likelihood, high consequence, and cheap to close).

**Fix:** `pip freeze` the current working set into a lockfile and install from it
in `Procfile`/Railway. Keep `requirements.txt` as the human-readable spec.

---

### F10 — Availability: unbounded concurrent SSE streams can starve the threadpool — VERIFIED

Every route in `sandlot_api.py` is a `def` (sync), so FastAPI runs each in the
AnyIO threadpool (default 40 workers). `skipper_send` returns a
`StreamingResponse` over a **sync** generator, which occupies one threadpool
worker for the full model round-trip, and `_trade_research_events`
(`sandlot_api.py:1067`) spawns an additional thread per request. ~40 concurrent
held connections make the whole app unresponsive.

**Severity: LOW** for this owner — annoyance, self-healing, no data loss. Fixed
for free by rate-limiting per F2/F3.

---

## 4. Checked and clean

Things I examined and found genuinely sound. **Do not spend follow-up effort
here.**

**Write boundary / execution control plane** — `sandlot_execution.py` in full.
Fails closed on flag, on missing digest, on identical owner/runner digests. TTLs
correctly `min()`-capped against the eligibility deadline. Lease terminated by
SQL compare-and-swap, not read-then-write. Evidence is denylisted
(`SENSITIVE_EVIDENCE_KEYS`, recursive, substring match at
`sandlot_execution.py:478`) *and* allowlisted (`ALLOWED_EVIDENCE_KEYS`, unknown
keys rejected at `:263`) *and* size-capped at 32 KB (`:338`). Runner free text is
discarded and replaced with fixed strings (`:250-254`). Covered by 36 tests in
`tests/test_sandlot_execution.py`.

**Owner bridge** — `sandlot_owner_bridge.py`. Loopback binding is enforced by
`argparse` `choices=["127.0.0.1","::1"]` (`:663`), **and** independently by a
server-side `Host`-header loopback check on every route
(`_host_is_loopback`, `:188`), **and** by exact-Origin match (`:186`), **and** by a
per-process nonce compared with `hmac.compare_digest` (`:177`). DNS-rebinding is
blocked by the Host check. Cross-site form POST to the review page is blocked by
the same-origin check at `:249`. Upstream must be an uncredentialed HTTPS origin
(`:46`), redirects are refused (`:167`), bodies capped at 64 KB, responses
re-validated and field-allowlisted before being handed back to the browser
(`:613`, `:642`). The review HTML escapes every interpolation and ships a
`default-src 'none'` CSP (`:221`). Covered by 13 tests including explicit path-
traversal and cross-origin cases.

**SQL** — all 2230 lines of `sandlot_db.py`. Zero f-string/`%`/`.format()`
interpolation of data into SQL. The single `f"""` at `sandlot_db.py:1785` splices
a *constant* fragment (`" AND request_id = %s"`) and still passes the value as a
bound parameter. All `LIMIT` values are clamped server-side
(`:765`, `:783`). No injection surface.

**Secrets in git history** — 156 commits scanned with `git log -S` for
`sk-or-v1`, `sk-ant-`, `postgresql://`, `FANTRAX_PASS=`, `GMAIL_APP_PASSWORD=`,
`JSESSIONID`, `SANDLOT_REFRESH_TOKEN=`, plus a regex sweep for AWS/GitHub/
Anthropic/OpenRouter key shapes across every reachable blob. **Every hit is a
placeholder** (`your-fantrax-password`, `postgresql://user:password@host:5432/railway`,
`replace-with-a-private-token`) in `.env.example` at `861cb4a`, or the CI test
DB URL `postgresql://sandlot:sandlot@127.0.0.1:5432/sandlot_test`. No real
secret was ever committed. `.env`, `.cookies/`, `.data/` are gitignored and no
file matching those names ever appears in `--diff-filter=A`.

**Fantrax write surface** — see §2. All FXPA method names are read-only literals.

**SSRF** — every outbound URL is built from a module constant.
`mlb_stats.BASE_URL` is a literal (`mlb_stats.py:28`); the only interpolated path
segments are `mlb_id` and `game_pk`, both resolved from cached DB rows /
MLB's own index rather than from request input (`player_service.py:299`). The
Fantrax URL is a constant. `sandlot_win_week.py:398` percent-encodes both
segments with `quote(..., safe='')`. No user-controlled host anywhere.

**XSS** — no `dangerouslySetInnerHTML`, no `innerHTML`, no `eval`, no
`new Function` in any `.jsx`. Only F5's `href` scheme gap.

**CSRF** — no CORS middleware means no cross-origin preflight succeeds; the
mutating routes all require `DELETE` or JSON `POST`, both non-simple. Nothing to
fix.

**Model selection** — `sandlot_skipper.model_order` (`:139`) allowlists against
`ALLOWED_CHAT_MODELS`; an unknown `model` string is silently dropped rather than
forwarded to OpenRouter. `normalize_reasoning_effort` (`:150`) clamps to four
values. Good.

**Refresh concurrency** — `run_refresh` takes a Postgres advisory lock
(`sandlot_refresh.py:51`) and returns `skipped` rather than running a second
scrape. Parallel-scrape abuse is not possible even without the token.

**Receipt ledger integrity** — `record_recommendation_receipt`
(`sandlot_db.py:627`) rejects an identity collision with different immutable
evidence (`:650`), refuses to reactivate a superseded receipt (`:652`), refuses to
supersede a decided one (`:663`), and does the supersede+insert inside one
transaction with `FOR UPDATE`. Correct.

**GitHub Actions** — all six workflows use `pull_request:` (never
`pull_request_target:`), so fork PRs run without secrets. `permissions:` is
explicitly `contents: read` on the scheduled jobs; only the two issue-filing jobs
escalate to `issues: write` and they run `github-script` with `github.token`, not
a PAT. `persist-credentials: false` is set where untrusted refs are checked out.
The `executor-contract` job checks out `refs/pull/63/head` and executes it, but
explicitly blanks `DATABASE_URL`/`FANTRAX_USER`/`FANTRAX_PASS` in `env:` and has
no secrets available. `sandlot_readonly_monitor.py` is documented and implemented
to emit counts and invariant names only, not payloads, before it is dumped into
`$GITHUB_STEP_SUMMARY` and an issue body. No secret reaches a log.

**Frontend supply chain** — `index.html` no longer loads Babel from CDN (the
`CLAUDE.md` note about in-browser Babel is stale); it loads a single committed,
esbuild-produced `app.js` whose freshness is enforced in CI by
`git diff --exit-code web/sandlot/app.js`. The only remaining CDN is Google Fonts
CSS (no SRI, but a stylesheet-only, low-value target). `package-lock.json` +
`npm ci`. Clean.

**localStorage** — only `sandlot_refresh_token` and Skipper UI preferences, per
the project's own rule. Nothing sensitive beyond the refresh token itself.

**Static file serving** — `NoCacheStaticFiles` subclasses Starlette `StaticFiles`,
which has its own traversal protection; `sandlot_index` reads two fixed paths.
No traversal.

---

## 5. Recommended order of work

1. **Verify `SANDLOT_REFRESH_TOKEN` is actually set on Railway** (`railway variables`).
   If unset, set it — that is the whole of F1. Then make the guard unconditional
   in production and constant-time (2 lines).
2. **Cap and gate the money routes** — `max_tokens` on `stream()`, a history
   window in `list_chat_messages`, and put `POST /api/skipper/messages`,
   `DELETE /api/skipper/messages`, `POST /api/trades/grade`, and
   `POST /api/player/{id}/refresh` behind the same shared secret the frontend
   already stores. Closes F2, F3, F4, F10 together.
3. **Lock the Python dependencies** (F9). Highest consequence-per-effort of
   anything on this list.
4. **`chmod 0600` in `auth._save_cookies` and `import_chrome_cookies.py`** (F7).
5. **Reject non-`http(s)` citation URLs server-side** (F5).
6. **Stop returning `str(exc)` to clients** in the older handlers (F8).

## 6. Open questions / unknowns

- Current Railway env: is `SANDLOT_REFRESH_TOKEN` set? Is
  `SANDLOT_EXECUTION_DRY_RUN_ENABLED` set? (Expected: unset → 503 on all
  execution routes. Worth confirming.)
- Is the GitHub repo `zoelsner/baseball` public or private? If public, the
  monitor issue bodies and step summaries expose league/roster shape — harmless
  here, but worth knowing.
- Does Railway sit behind any edge rate limiting? I assumed not; if it does, F2/F3
  drop a severity band.
- The FastAPI request body size is unbounded at the app layer (uvicorn does not
  cap it by default). I did not find a practical exploit given the Pydantic field
  caps, but a very large JSON body to any `POST` is parsed before validation.
