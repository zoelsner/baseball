# Sandlot automation loop

Sandlot already refreshes Fantrax read-only through Railway at `13:00` and
`21:00` UTC. This loop does not add another scraper or another scheduler.
Instead, GitHub checks the resulting production contracts 45 minutes after
each Railway refresh during baseball season.

## Flow

1. `scripts/sandlot_readonly_monitor.py` performs GET requests against health,
   snapshot, attention, hot-swap, and waiver endpoints.
2. The monitor records only contract states, counts, and invariant failures.
   Player and roster payloads are not copied into the report.
3. A passing monitor ends the workflow and closes any prior monitor issue.
4. On failure, GitHub creates or updates one labeled issue containing the
   sanitized evidence and keeps the workflow red.
5. Zach can bring that issue to an interactive Codex session when he chooses.
   Diagnosis, code changes, tests, and PR publication are never started by the
   scheduled workflow.

The scheduled path uses no OpenAI API, model, or Codex credits. It is ordinary
GitHub Actions plus deterministic Python.

## Executor coverage

The weekly/manual `executor-contract` job checks out draft PR #63, applies a
static safety-contract gate from the default branch, and then runs the PR's
mocked guard tests. The gate requires every write request to be bound to the
exact snapshot, proposal, input hash, and player confirmation; it also requires
live slot-legality preflight, post-write verification, and regression tests for
stale or protected actions. The job receives no Fantrax credentials, database
URL, or actions token. It never launches a live roster write and never calls
`POST /api/actions`.

Draft PR #63 intentionally fails this gate until those safeguards are actually
implemented. Passing its existing mock suite alone is not treated as proof of
write safety, and neither result proves Fantrax's current DOM click flow. Any
real executor test remains local, headful, and tied to Zach approving the exact
named action while watching.

## One-time GitHub setup

1. Ensure repository Actions policy lets this workflow request `issues: write`
   so it can maintain the single monitor issue.
2. Optional: set repository variable `SANDLOT_URL` to override the production
   URL.
3. Merge this workflow to the default branch; scheduled workflows only run
   from the default branch.

Manual runs can independently enable the production monitor and executor guard
suite through `workflow_dispatch` inputs.
