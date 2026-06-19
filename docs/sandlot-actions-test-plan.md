# Sandlot Actions Manual Test Plan

Do not run these from CI. Each successful case can touch the real Fantrax team,
so execute only with an intentional low-risk roster plan.

## Checklist

1. Token rejection
   - Call `POST /api/actions` without `Authorization` or `x-actions-token`.
   - Expected: `401`, no Selenium launch, no Fantrax write.

2. Session freshness failure
   - Expire or remove cached Fantrax cookies, then call a valid action.
   - Expected: `502` with `Session expired — run a refresh to re-authenticate.`

3. Move an injured player to IL
   - Pick a player with `IL`, `IR`, `DTD`, or `OUT` in the latest snapshot.
   - Call `move_to_il`.
   - Expected: `200`, player name returned, `detail.from_slot` and
     `detail.to_slot` present, action logged.

4. Refuse a healthy IL move
   - Pick a healthy rostered player.
   - Call `move_to_il`.
   - Expected: `400`, clear IL eligibility error, no Selenium write.

5. Add a free agent with roster space
   - Confirm active plus reserve count is below max.
   - Call `add_free_agent` for a low-risk available player.
   - Expected: `200`, player name returned, action logged.

6. Add with full roster using move-out player
   - Confirm active plus reserve count is at max.
   - Call `add_free_agent` with `move_out_player_id` for a low-risk move-out.
   - Expected: Fantrax add/drop confirmation handled in one flow, `200`, both
     player IDs visible in the log/debug detail.

7. Drop and slot-change guardrails
   - First call `drop_player` with an incorrect `confirm_player_name`.
   - Expected: `400`, no Selenium write.
   - Then call `change_slot` for a low-risk bench/active slot move.
   - Expected: `200`, `from_slot` and `to_slot` returned, action logged.
