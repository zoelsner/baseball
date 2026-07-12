# Sandlot execution dry-run

This is the first control-plane slice between an exact Sandlot recommendation
and a future supervised Fantrax action. It proves request identity, owner and
runner separation, single-use claiming, and live preflight. It cannot mutate
Fantrax.

## Safety boundary

- Supported mode: `dry_run` only.
- Supported proposal: one simple two-player lineup swap only.
- Unsupported: multi-step lineup chains, add/drop, waiver claims, trades, and
  every Fantrax write.
- The web UI remains review-only and receives no owner or runner credential.
- The local runner may navigate a headful browser and use authenticated read
  APIs. It has no click, type, submit, or mutation path.
- A request expires after at most 120 seconds. A claim lease expires after at
  most 90 seconds and is never requeued.
- Full roster membership must remain unchanged. Any missing player—including
  Aaron Judge—fails preflight before a future write could be considered.

## Configuration and kill switches

The control plane is unavailable unless all three variables are configured:

```text
SANDLOT_EXECUTION_DRY_RUN_ENABLED=true
SANDLOT_OWNER_ACTION_TOKEN_SHA256=<sha256 of owner plaintext secret>
SANDLOT_RUNNER_TOKEN_SHA256=<sha256 of different runner plaintext secret>
```

Keep the plaintext owner secret only with the trusted confirming client. Keep
the plaintext runner secret only on the local Mac. Railway receives digests,
not plaintext. Removing the feature flag or either digest fails closed. The
runner reads its plaintext from `SANDLOT_RUNNER_TOKEN`; it never sends Fantrax
cookies to Sandlot.

Generate a digest locally without printing the plaintext into shell history by
using an approved secret-management workflow. Do not put either plaintext
secret in Git, the browser bundle, request bodies, logs, or preflight evidence.

## API and state machine

Owner credential:

- `POST /api/execution-requests` — server re-derives the latest proposal,
  exact-matches the confirmation, and creates or idempotently returns the same
  immutable request.
- `GET /api/execution-requests/{request_id}` — returns status and sanitized
  evidence, never the runner lease or immutable contract.

Runner credential:

- `POST /api/execution-requests/claim` — atomically claims one pending request
  with `FOR UPDATE SKIP LOCKED`; returns the one-time lease plaintext once.
- `POST /api/execution-requests/{request_id}/preflight` — compare-and-swap
  terminal report bound to the runner credential and lease digest.

```text
pending -> claimed -> preflight_passed
                   -> preflight_failed
pending/claimed    -> expired
pending            -> cancelled (reserved; no public route yet)
```

There is no execution state and no route that calls Fantrax after preflight.
Every response exposes `writes_enabled: false`.

## Local visible runner

With a current local Fantrax login and the runner plaintext in the environment:

```bash
python scripts/sandlot_execution_runner.py \
  --base-url https://web-production-90664.up.railway.app
```

The default processes at most one request. `--loop` polls for later requests,
but it does not retry a claimed request. For each claim the runner:

1. opens the exact Fantrax roster visibly and captures read-only DOM evidence;
2. independently reads the live Fantrax roster API;
3. compares target period, complete roster set, both from-slots, both legal
   destinations, and exact deadline;
4. records zero clicks and zero writes; and
5. reports one terminal pass/fail result.

## Deliberately deferred

- Browser UI confirmation and owner session authentication.
- A persistent local runner service/LaunchAgent.
- Exact Fantrax mutation mechanics.
- Local final approval after live preflight.
- Post-click uncertain-state handling and post-write verification.

Those belong to later slices. A passing dry-run is evidence that an exact
proposal still matches live Fantrax—not permission to execute it.
