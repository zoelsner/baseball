# Sandlot/Zo Safe Roster-Action Confirmation Flow

## Problem

Zach wants Zo Computer to act as a faster, event-triggered session for roster
decisions, but the system must make it hard to accidentally drop or move a top
player. Sandlot should prepare structured recommendations and safety gates; Zo
should message Zach with the exact proposed move and only submit confirmed,
allowed actions.

## Intended Outcome

Zach can get a fast Zo message like: "Sandlot proposes moving Bench Bat into OF
and TJ Friedl to BN for +2.4 projected points. Confirm?" The action is
impossible to execute unless the named move is eligible, slot data is trusted,
the executor is available, and Zach confirms the exact action.

## Proposed Slice

Build the safety architecture for Zo-triggered roster actions around the
Attention Queue:

1. Sandlot produces structured action proposals from `/api/attention`.
2. Every proposal includes action type, player ids/names, move-out/move-in
   context, data provenance, confidence, warnings, and a human confirmation
   string.
3. Sandlot marks each proposal as `blocked`, `proposal_only`, or `executable`
   from backend safety logic.
4. Zo can message Zach when events change, but can only submit actions that are
   `executable` and match the confirmation payload.
5. Top-player/drop protection is enforced server-side, not by UI copy.

First implementation should follow the reviewed safety sequence: fix/gate real
lineup-slot reliability first, then ship a lineup-only replacement swap card.
Waiver/add-drop stays deferred until #67 and add/drop guards are complete.

## Non-Goals

- No autonomous Fantrax writes.
- No trade execution.
- No waiver/add-drop recommendations until real lineup slots and add/drop
  protections are reliable.
- No UI-only safety promises; protection must live in backend checks.
- No bypass around Zach's explicit per-action confirmation.

## Acceptance Criteria

- [ ] Backend proposals include a machine-readable `action_state` and never emit
      `executable` when slot reliability is unknown, snapshot freshness is old,
      or executor support is absent.
- [ ] Server-side protection blocks drops/move-outs for protected players:
      top-value anchors, Min/prospect slots, IL/IR stashes, and any player above
      a configured value/roster-rank threshold.
- [ ] Zo confirmation copy includes exact player names, action type,
      destination slot, estimated impact, and warnings.
- [ ] Submitted actions must match the latest proposal payload by id/hash; stale
      or edited confirmations fail closed.
- [ ] Attention Queue UI shows the same state Zo sees: `blocked`,
      `proposal_only`, or confirmable.
- [ ] Tests cover accidental top-player drop, stale proposal replay, untrusted
      slot data, old snapshot, mismatched player ids, and allowed lineup-only
      proposal.

## Product / Design Context

- Product context checked: `PRODUCT.md`
- Design context checked: `DESIGN.md`
- Architecture context checked: `docs/ARCHITECTURE.md`
- Safety context checked: `STATUS.md`
- Review note: Claude Opus/xhigh flagged #67 as a recommendation-content
  blocker, not just an execution blocker. Do not emit confident swap proposals
  from untrusted slot data.

## Test / Evidence Plan

- Unit tests for action-state derivation and protected-player blockers.
- API contract tests for `/api/attention` proposal payloads.
- Playwright test showing the Attention Queue visual state and CTA copy.
- Dry-run Zo-style confirmation test: read proposal, confirm exact payload, and
  verify only safe action would be submitted.
- Final `claude -p --model opus --effort xhigh` review on the implementation
  diff.

## Risks / Rollback

Risk: Zo or the UI gets ahead of slot/action safety and makes a
confident-looking recommendation from unreliable data.

Mitigation: default to `blocked` or `proposal_only`, keep waiver/add-drop off
until #67 is resolved, require proposal hashes for confirmation, and enforce
protected-player rules on the server.

Rollback: disable executable action states and keep proposals as read-only or
deep-research prompts while retaining the visual comparison UI.
