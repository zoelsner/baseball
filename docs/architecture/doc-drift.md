# Documentation Drift Register

**Audited commit:** `bd54135` (`origin/main`, 2026-07-14) · **Date:** 2026-07-24

---

## Why this is a defect register and not a tidiness list

`CLAUDE.md` and `AGENTS.md` are injected into every AI agent's context as ground
truth. `docs/ARCHITECTURE.md` is a mandatory read gated by `AGENTS.md:14,31`.
These files are not descriptions of the system — they are **inputs to it**.

A wrong line in `CLAUDE.md` is executed, not merely read. Every future agent
inherits it and builds on it.

This is demonstrated, not theorized. The audit that produced this document was
itself corrupted by it:

- The session loaded a `CLAUDE.md` describing a frontend architecture retired in May.
- Four agents were dispatched against a stale checkout and had to be killed.
- One agent was briefed with frontend constraints — "no bundler," "no `import`/`export`," "refs via `window.*`" — that had been false for two months, and had to be corrected mid-run.

**Stale documentation in this repository has a propagation mechanism.** Treat
these entries with the same seriousness as code defects.

---

## Summary

| Document | Lines | Verdict | Divergences |
|---|---|---|---|
| `docs/ARCHITECTURE.md` | 303 | Contains a section that was **never true** | 5 |
| `STATUS.md` | 404 | Unreliable as current state | 7 |
| `docs/SANDLOT-HANDOFF.md` | 237 | Wrong in nearly every number; one instruction is destructive | 8 |
| `README.md` | 183 | Actively misleading to a newcomer | 4 |
| `CLAUDE.md` | 70 | Accurate but materially incomplete | 4 |
| `AGENTS.md` | 97 | Accurate; inherits risk through its pointer | 1 |
| `docs/recommendation-receipts.md` | 218 | Safety claims hold; 2 stale versions | 2 |
| `PRODUCT.md` / `DESIGN.md` | 85 / 241 | Accurate | 0 / 1 |
| `docs/sandlot-execution-dry-run.md`, `sandlot-automation.md`, `sandlot-matchup-projection-model.md`, `win-this-week.md` | 722 | **Fully verified** | 0 |
| `docs/quality/*` + 5 orphan plan docs | ~2,300 | Historical, unlabeled as such | 6 |

**Verification key:** ✅ orchestrator-verified · 🔶 agent-reported, not re-checked.

---

## Critical

### D1 — `docs/ARCHITECTURE.md` describes a frontend that never existed ✅

**`docs/ARCHITECTURE.md:20,254-265`**

Claims `index.html` loads `atoms.jsx`, `data.jsx`, `data2.jsx`, and
`v2-pages.jsx` through in-browser Babel, with "no module system" and
`Object.assign(window, …)` for inter-file references.

Verified reality:

| Claim | Actual |
|---|---|
| Multiple JSX script tags via CDN Babel | `index.html:50` loads exactly one file: `<script src="app.js?v=frontend-build" defer>` |
| No module system | `main.jsx` uses real ES imports; `atoms.jsx`, `main.jsx`, `v2-pages.jsx` all use `import`/`export` |
| `Object.assign(window, …)` | **Zero occurrences** in any `.jsx` file |
| `data.jsx` / `data2.jsx` | Deleted 2026-05-26 (`e2ac912`) |
| — | `package.json` builds with esbuild; React 18.3.1 and react-dom pinned as real dependencies |

**The file was created 2026-06-10 (`01227c6`), fifteen days after the migration
it contradicts. It was wrong at birth.**

It is also the lone dissenter — `CLAUDE.md:33-42` and `AGENTS.md:59-63` describe
the current architecture correctly. An agent reading all three receives a
contradiction and has no principled way to resolve it, while `AGENTS.md:14,31`
makes reading the wrong one mandatory.

**Fix:** rewrite the section against the esbuild reality. Highest priority in
this document.

---

### D2 — `docs/SANDLOT-HANDOFF.md` instructs deletion of the governing docs ✅

**`docs/SANDLOT-HANDOFF.md:213-217`**

> Untracked scaffold files in the working tree (`AGENTS.md`, `PRODUCT.md`,
> `DESIGN.md`, `docs/ARCHITECTURE.md`, `.github/` templates) duplicate open
> PR **#60** — remove the local copies when #60 merges.

PR #60 merged as `01227c6`. `git ls-files` confirms all four files are **tracked**.
An agent following this instruction deletes `AGENTS.md`, `PRODUCT.md`,
`DESIGN.md`, and `docs/ARCHITECTURE.md` — the four documents that govern its own
behavior.

Same file, additional errors 🔶: "6 tabs" vs 5 (`v2-pages.jsx:674-680`); "12
routes" vs 28; `sandlot_api.py` "510 lines" vs 1,759; "Kimi → Tencent" vs
DeepSeek → Kimi (`sandlot_skipper.py:33-34`).

**Fix:** delete the instruction today. Then decide whether this file has any
remaining purpose — nearly every number in it is wrong, and a handoff document
that is wrong throughout is worse than no handoff document.

---

### D3 — `STATUS.md` asserts an endpoint that does not exist ✅

**`STATUS.md:150,228`**

> `GET /api/attention` is live … returns the ordered queue with status-safe
> `POST /api/actions` payloads where allowed.

> **Not yet done:** Railway tokens (`SANDLOT_ACTIONS_TOKEN`, `SANDLOT_REFRESH_TOKEN`)
> unset — the executor endpoint is fail-closed (503) until then.

There is no `POST /api/actions` among the 28 routes in `sandlot_api.py`. The
string appears only in three docstrings (`sandlot_api.py:169`,
`sandlot_attention.py:12,373`) and one Playwright spec
(`attention-api.spec.ts:41`). `SANDLOT_ACTIONS_TOKEN` appears in no `.py` file.
It belongs to unmerged draft PR #63.

This matters twice over: `CLAUDE.md:5` directs every agent to read `STATUS.md`
first, and the second line describes a *safety property* — "fail-closed (503)" —
for an endpoint that does not exist. An agent could reasonably conclude a
protection is in place when there is nothing there at all.

**Fix:** move both bullets to an explicitly-labeled "planned / not shipped"
section.

---

## Moderate

### D4 — `CLAUDE.md` omits the execution kill-switches ✅

**`CLAUDE.md:22-29`** lists 5 of roughly 25 `SANDLOT_*` variables. Omitted:

- `SANDLOT_EXECUTION_DRY_RUN_ENABLED`
- `SANDLOT_EXECUTION_OWNER_ACTION_TOKEN_SHA256`
- `SANDLOT_EXECUTION_RUNNER_TOKEN_SHA256`

These are the three variables controlling the execution control plane — the most
safety-relevant configuration in the system, absent from the file agents read
first. `.env.example:29` additionally inverts the model order 🔶.

**Fix:** document all three with their fail-closed semantics. If the list is too
long for `CLAUDE.md`, put it in one referenced file and keep the safety-relevant
three inline.

---

### D5 — Evidence version drift: docs say `v2`, code says `v3` ✅

| Location | Value |
|---|---|
| `fantrax_data.py:184` | `fantrax_period_lineup_v3` |
| `sandlot_receipts.py:26` | `fantrax_period_lineup_v3` |
| `sandlot_db.py:895` | `fantrax_period_lineup_v3` |
| `STATUS.md:383` | `fantrax_period_lineup_v2` |
| `docs/ARCHITECTURE.md:65` | `fantrax_period_lineup_v2` |
| `docs/recommendation-receipts.md:167` | `fantrax_period_lineup_v2` |

No document mentions v3. For a versioned evidence contract this is more than
cosmetic — the version is a join key, and [R6](risk-register.md#r6) concerns a
join that silently discards rows.

---

### D6 — `README.md` documents the wrong product ✅ / 🔶

The README leads with `audit.py` and `league_intel.py`, the dormant local CLI.
The live product — a deployed web app — appears in one line. Setup instructions
describe a macOS launchd cron, Gmail app passwords, and the `claude` CLI, none
of which the web app uses.

Most importantly: the "recommend-only" **conclusion** at `:9`/`:181` is still
true, but its stated **mechanism** — "the Fantrax library is read-only" — is now
false. The guarantee today comes from contract checks
(`sandlot_execution.py:124,157`) and feature flags.

That distinction matters: a reader who believes the library enforces read-only
will not realize that swapping the library removes the guarantee. The right
answer is documented for the wrong reason, which is the most fragile kind of
correct.

**Fix:** lead with the web app; move the CLI to a clearly-marked legacy section;
correct the mechanism sentence.

---

### D7 — `docs/quality/*` is unlabeled history 🔶

~2,300 lines across quality-loop records, second-opinion results, and five
orphan plan documents. A newcomer — human or agent — cannot distinguish a
still-binding decision from a completed record.

**Fix:** a one-line status header on each (`Historical — superseded by X` /
`Binding`), or move completed records into `docs/quality/archive/`.

---

### D8 — Unverifiable and drifted numerics in `STATUS.md` 🔶

| Claim | Finding |
|---|---|
| "11 trade and 4 Skipper journeys" | ✅ exact |
| "2 disposable-Postgres tests skipped" | ✅ exact |
| "573 Python tests pass" | ⚠️ 588 `def test_` at HEAD |
| "20 relevant browser journeys" | ❌ unverifiable (68 across 12 specs) |

Also undocumented anywhere: `ci.yml:13-28` provisions Postgres, so the two
"skipped" tests are **not** skipped in CI.

Hand-maintained counts drift by construction. Either generate them or drop them.

---

## Open question for the maintainer

`STATUS.md:400` places "no add/drop recommendations until #67 lands" in a block
marked non-negotiable, while `:303-307` reports #67 cleared. Only you can say
whether that constraint is retired. Until resolved, an agent reading the file
top-to-bottom will apply a rule you may have lifted.

---

## What the documentation gets right

Worth recording, because the picture above is unbalanced on its own.

**Every claimed safety property is genuinely enforced in code** — verified
individually:

- Sanitized receipt exposure is an allowlist dict (`sandlot_api.py:1493-1560`).
- The owner bridge is loopback-only, enforced server-side three ways plus a nonce (`sandlot_owner_bridge.py:25,188-193,229-246,663`).
- No owner bearer reaches the browser bundle; all owner calls target `V2_OWNER_BRIDGE_URL`.
- `fantrax_changed` and `writes_enabled` are **hard-set**, not merely asserted (`sandlot_api.py:391-392`), and rejected at `sandlot_owner_bridge.py:431,456`.

`docs/sandlot-execution-dry-run.md` checks out to the constant — 120s/90s/5-min
all match code.

**The pattern: where these documents make a safety promise, the code keeps it.**
The drift is concentrated in structural and status claims. That is the better
failure mode of the two, and it means the remediation here is bounded.

---

## Suggested order

1. **D2** — a live instruction to delete four governing files. Today.
2. **D1** — the mandatory-read file is wrong about the frontend.
3. **D3** — a claimed safety property on a nonexistent endpoint.
4. **D4** — the three execution kill-switches.
5. **D5–D8** — as convenient.

Then consider the structural fix: these documents drift because nothing checks
them. A CI step asserting that referenced routes, env vars, and version strings
exist in code would have caught D1, D3, D4, and D5 at PR time.
