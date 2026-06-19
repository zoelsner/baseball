# Sandlot Actions API

`POST /api/actions` is a machine-to-machine executor for Zo Computer. It is not
a Sandlot user-facing feature and must not be exposed in the UI or Skipper.

## Authentication

Set `SANDLOT_ACTIONS_TOKEN` on Railway. The token must be different from
`SANDLOT_REFRESH_TOKEN`.

Send the token either as:

```http
Authorization: Bearer <token>
```

or:

```http
x-actions-token: <token>
```

Missing or wrong tokens return `401`. If the server token is not configured,
the endpoint fails closed with `503`.

## Request

```json
{
  "action": "move_to_il",
  "player_id": "fantrax-player-id",
  "to_slot": "IL",
  "confirm_player_name": "Exact Player Name",
  "move_out_player_id": "fantrax-player-id"
}
```

Fields:

- `action`: required. One of `move_to_il`, `add_free_agent`, `drop_player`,
  `change_slot`.
- `player_id`: required Fantrax player ID.
- `to_slot`: required only for `change_slot`.
- `confirm_player_name`: required for `drop_player` and must exactly match the
  latest roster snapshot player name.
- `move_out_player_id`: required for `add_free_agent` when active plus reserve
  roster slots are full.

## Response

All action-specific outcomes use the same response body:

```json
{
  "ok": true,
  "action": "move_to_il",
  "player_name": "Example Player",
  "detail": {
    "from_slot": "BN",
    "to_slot": "IL"
  },
  "error": null,
  "duration_ms": 1234
}
```

Status codes:

- `200`: action succeeded.
- `400`: request or deterministic safety check failed.
- `401`: missing or invalid actions token.
- `409`: refresh/action lock is held, or no successful snapshot exists.
- `502`: Fantrax session is stale or Selenium/action execution failed.
- `503`: server configuration or database is unavailable before execution.

## Safety Constraints

The endpoint implements these constraints in code:

- No automatic retry wraps Selenium write actions.
- Drops require exact `confirm_player_name` matching the latest roster snapshot.
- `move_to_il` refuses players without an injury/status flag in the latest
  successful snapshot.
- `add_free_agent` checks active plus reserve capacity and requires
  `move_out_player_id` when the roster is full.
- Every action validates cached Fantrax cookies before Selenium writes. Stale
  sessions return `Session expired — run a refresh to re-authenticate.`
- Actions and refresh use the same Postgres advisory lock ID: `2026060802`.
- Every post-token action attempt records to `action_logs` with action type,
  player ID, success/failure, error detail, duration, snapshot ID, and Selenium
  state.

## Fantrax Navigation

The Selenium executor uses cached Fantrax cookies from Postgres, environment
cookies, or the local cookie file. It never starts an interactive login flow.

Optional URL overrides:

- `SANDLOT_FANTRAX_ROSTER_URL`
- `SANDLOT_FANTRAX_FREE_AGENTS_URL`
- `SANDLOT_ACTIONS_HEADFUL=1` for visible local debugging
