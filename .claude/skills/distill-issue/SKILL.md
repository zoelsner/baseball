---
name: distill-issue
description: Drafts a GitHub issue from a brief description, then files it via `gh issue create`. Use whenever a non-trivial slice is about to be worked on but no issue exists yet — before code, so the issue URL becomes the slice's anchor for branch + PR.
---

# distill-issue

Turns a one-line description of a slice into a properly-structured GitHub issue and files it on `zoelsner/baseball`. Returns the issue URL, which the agent then uses as the branch slug source and links from the eventual PR.

## When to use

- The agent is about to start non-trivial work and no issue exists yet.
- The user pastes a chat description of what they want done — distill before editing code.
- The eventual PR will need an `Issue: #<number>` reference in the body.

## When _not_ to use

- One-line typo fixes, formatting tweaks, doc-only edits where a PR title alone says enough.
- The user has already linked or filed an issue.
- Exploratory work that's not yet a coherent slice — capture in `inbox/` or `Board.md` instead.

## How to invoke

The skill is a template, not a script — fill in each field, then run `gh issue create`.

### 1. Compose the body

Use this Markdown shape. Drop sections that don't apply (e.g. `Architecture impact: none`).

```markdown
## Problem

<one short paragraph — what's broken or missing today, framed as user-facing impact when possible>

## Intended outcome

<what is true when this slice is done — concrete, observable>

## Non-goals

- <bulleted thing this slice will not do, even if tempting>
- <...>

## Acceptance criteria

- [ ] <testable item the reviewer will check off>
- [ ] <...>

## Architecture impact

<which files / boundaries / schemas this touches; "none" is fine for small slices>

## Blockers

<other issues or decisions this depends on; "None." is fine>
```

### 2. File it

```bash
gh issue create \
  --repo zoelsner/baseball \
  --title "<conventional-commits-style title>" \
  --label "<comma-separated-labels>" \
  --body "$(cat <<'EOF'
<the body from step 1>
EOF
)"
```

`gh issue create` returns the URL on stdout. Surface that URL to the user before any code edits.

## Field guidance

| Field                | What to write                                                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `title`              | Conventional Commits style: `<type>(<scope>): <imperative summary>`. Same shape as the eventual PR title.              |
| `labels`             | At least one `type:*` (`type:feature` / `type:bug` / `type:chore`) and at least one `area:*` (`area:backend` / `area:frontend`). |
| Problem              | One short paragraph. What is broken or missing today.                                                                  |
| Intended outcome     | What is true when this slice is done. Concrete and observable.                                                         |
| Non-goals            | Things the agent might be tempted to bundle in but should not.                                                         |
| Acceptance criteria  | Task-list items the reviewer can check off. Each should be testable or observable in the running app.                  |
| Architecture impact  | Which directories, schemas, env vars, or external services this touches. "None" is a valid answer for small slices.    |
| Blockers             | Other issues this depends on, or decisions that need to land first. `None.` is fine.                                   |

## Available labels

Created on this repo (`gh label list` to verify):

- `type:feature` — new capability or enhancement
- `type:bug` — something is broken
- `type:chore` — tooling, infra, refactor, docs
- `area:backend` — FastAPI / Python (`sandlot_*.py`, `player_service.py`, `mlb_stats.py`)
- `area:frontend` — `web/sandlot/*.jsx`

Add new labels via `gh label create` — but resist proliferation. Five works for now.

## Output

The issue URL. Surface it to the user. The agent then uses the issue number as the branch slug source (e.g. `feat/12-lineup-assistant`) and adds `Closes #12` to the eventual PR body.

## See also

- `CLAUDE.md` — project-level conventions and the deploy gate.
- The full reference this is adapted from: `lightstrikelabs/repo-analyzer-green/.agents/skills/distill-issue/SKILL.md`. That repo's version has a renderer script and pairs with a stricter `AGENTS.md` checklist; this slim version drops both because Sandlot is single-developer and has no test suite.
