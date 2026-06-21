# Sandlot Quality Loop

This loop turns the active app into a measurable set of user stories, then
tests, fixes, and retests those stories without losing track of scope.

## Canonical Tracker

Use one tracker for the whole loop:

`docs/quality/user-story-inventory.csv`

The tracker is intentionally CSV instead of `.xlsx` so it is diffable,
mergeable, and reviewable in pull requests.

## Status Values

- `undocumented`: discovered but not specified yet
- `specified`: user story and expected behavior are written from code
- `testing`: actively being tested
- `passed`: tested and no defect found
- `failing`: tested and defect documented
- `fixed`: code changed for a documented defect
- `retested`: fixed behavior was tested again
- `product-question`: code does not reveal enough intent to call it a bug

## Defect Types

- `logistical`: broken route, broken action, stale data, wrong data, failed state,
  missing loading/error/empty state, or behavior that blocks completion
- `ux`: confusing copy, unclear affordance, poor layout, overlap, inaccessible hit
  target, weak hierarchy, or hard-to-understand state
- `product-question`: ambiguous intent that needs Zach/product judgment before
  becoming a fix

## Loop

### Phase 1: Inventory And Specification

Go over every active user-facing feature reachable from the app shell, hidden
settings, and public API surfaces used by the app or agent integrations.

For each feature, record:

- feature area
- user story
- expected behavior based on current code
- source files/functions/routes
- best available test method
- current status

Do not invent new requirements in this phase. Expected behavior comes from the
code, docs, and existing tests.

### Phase 2: Test Every Story

Test each row against the real app where possible. Use:

- local browser automation with mocked APIs for branch-bundle UI behavior
- production/Railway E2E only for deploy-backed data behavior
- direct API calls for route contracts
- unit tests for deterministic logic
- manual notes only when automation would require credentials or watched writes

Update each row to `passed`, `failing`, or `product-question`. Every failing row
must include evidence and a concise defect note in the tracker.

### Phase 3: Fix Confirmed Errors

Fix every confirmed `logistical` and high-confidence `ux` error. Keep fixes
scoped to tracker rows. If a fix changes expected behavior, update the tracker
before coding beyond that row.

Do not fix `product-question` rows until the intended behavior is clarified.

### Phase 4: Retest

Retest every fixed row and the full critical path:

Today -> Adds -> Continue in Skipper -> Skipper draft -> player sheet.

Rows are done only when final status and evidence are recorded.

## Done Definition

The loop is complete when:

- every active user-facing feature has a tracker row
- every tracker row has expected behavior and source references
- every row has test evidence or a documented testing blocker
- no blocking/high-severity logistical defects remain
- fixed rows have been retested
- remaining `product-question` rows are explicitly called out for decision
