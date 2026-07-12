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
  most 90 seconds and is never requeued. Owner status and idempotent-create
  reads normalize overdue pending/claimed rows to `expired` even when no runner
  is polling.
- Request creation also enforces the proposal's live-preflight snapshot age;
  the current contract requires a server snapshot no more than five minutes
  old. Old eligibility data cannot seed a browser-assisted preflight.
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
  terminal report bound to the runner credential and lease digest. A passing
  report must contain the complete contract-derived check-key set, an exact
  full-roster SHA-256 digest, exact observed participant slots and eligible
  destinations, and an observation timestamp inside the live claim window. A
  fixed `live_read` failure may terminal-fail without pretending roster facts
  were observed.

```text
pending -> claimed -> preflight_passed
                   -> preflight_failed
pending/claimed    -> expired
pending            -> cancelled (reserved; no public route yet)
```

There is no execution state and no route that calls Fantrax after preflight.
Every response exposes `writes_enabled: false`.

## Local visible runner

### One-click proposal confirmation

The exact-action review sheet can request the same dry-run without receiving or
persisting the owner bearer. Start the loopback-only owner bridge on the Mac
that holds the plaintext owner token:

```bash
SANDLOT_OWNER_ACTION_TOKEN='<local plaintext owner secret>' \
python sandlot_owner_bridge.py
```

The browser probes `http://127.0.0.1:8765`, then one explicit **Confirm exact
action · run safety check** click sends the immutable proposal identity and
confirmation to that local process. The bridge adds the owner bearer only on
its server-to-server request to Sandlot and proxies sanitized status back to
the review sheet. The token never enters page state, browser storage, request
JSON, logs, or the committed bundle.

Chromium treats public HTTPS → loopback as Local Network Access. Sandlot marks
each bridge fetch with `targetAddressSpace: "local"` so Chromium can show its
one-time local-network permission prompt instead of silently blocking the
health check. Denying that permission leaves the bridge offline and the
confirmation button disabled.

The bridge binds only to loopback, requires the exact configured production
origin, validates the loopback Host header, requires a per-process nonce on
state-changing/status requests, answers Private Network Access preflight, caps
JSON bodies, rejects upstream redirects, and accepts only an uncredentialed
HTTPS Sandlot origin. `--allowed-origin http://127.0.0.1:<port>` is available
for local frontend development. The bridge and visible runner are separate
processes: the bridge confirms/creates the request; the runner claims it and
performs the zero-click visible preflight.

If the bridge, control plane, or runner is offline, the review sheet stays
disabled or reports a terminal safe failure. A passing state means only that
the exact proposal still matched live evidence. It does not authorize or
perform a Fantrax mutation.

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

### Cookie-free visible browser evidence

The runner can alternatively consume a fresh, non-secret JSON artifact from an
already signed-in visible browser:

```bash
python scripts/sandlot_execution_runner.py \
  --base-url https://web-production-90664.up.railway.app \
  --browser-evidence-json /path/to/fresh-visible-roster.json
```

This mode is a two-process, post-claim handshake. Start the runner first. After
it claims a request it atomically writes a non-secret sidecar named
`/path/to/fresh-visible-roster.json.request.json`, then waits (60 seconds by
default, configurable with `--evidence-wait-seconds`). A separate visible
browser exporter must watch that sidecar, capture the roster *after* the claim,
and atomically replace the requested artifact (write a temporary file, then
rename it). In `--loop` mode the exporter must respond to every new sidecar;
an artifact from a prior request is deliberately rejected.

The sidecar contains `request_id`, `snapshot_id`, `proposal_id`, `input_hash`,
`league_id`, `team_id`, and `claimed_at`; it never contains the runner bearer
or lease token. The produced artifact must echo those six identity fields
exactly and add this non-secret shape:

```json
{
  "request_id": "xreq_...",
  "snapshot_id": 274,
  "proposal_id": "lineup-swap:...",
  "input_hash": "...",
  "league_id": "...",
  "team_id": "...",
  "captured_at": "2026-07-12T01:00:01+00:00",
  "period_number": 17,
  "rows": [{
    "player_id": "0423c",
    "name": "C. Cortes",
    "team": "ATH",
    "slot": "OF",
    "identity_conflict": [],
    "lineup_control_enabled": true
  }]
}
```

It contains no cookies, storage, authorization headers, lease, or Fantrax API
response. `captured_at` must be inside the live claim window and no more than
30 seconds old. `lineup_control_enabled` is true only when the actual selected
slot control is present and lacks disabled/locked semantics. A missing,
ambiguous, or disabled participant control fails preflight; a position chip is
not accepted as a lineup control.

Fantrax headshot URLs such as `hs0423c_96_1.png` are parsed as player ID
`0423c`; size/version suffixes are not part of the identity. Some prospects
have no headshot and therefore no visible ID. Those rows resolve only when
initial + surname + team has exactly one unmatched server-roster candidate.
Ambiguity, count drift, identity conflict, or any missing player fails the
entire preflight. This preserves full-roster/Aaron Judge protection.

Destination eligibility comes from the same server snapshot embedded in the
immutable request. The request and lease are capped at that snapshot's exact
five-minute eligibility deadline, so a near-stale snapshot cannot gain another
120 seconds by creating a request.
The visible browser independently proves period, complete membership, and
current slots. This bridge remains zero-click and zero-write.

Credential-bearing runner calls require HTTPS. Plain HTTP is accepted only for
`localhost`, `127.0.0.1`, or `::1` development, and redirects are rejected so a
runner bearer cannot be forwarded to a typoed or untrusted destination.

## Deliberately deferred

- Browser UI confirmation and owner session authentication.
- A persistent local runner service/LaunchAgent.
- Exact Fantrax mutation mechanics.
- Local final approval after live preflight.
- Post-click uncertain-state handling and post-write verification.

Those belong to later slices. A passing dry-run is evidence that an exact
proposal still matches live Fantrax—not permission to execute it.
