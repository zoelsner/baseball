# Second-Opinion Gate Prompt

Use this before implementing any API contract, data model, auth/security
boundary, migration, model tool-calling flow, cross-module architecture, or
high-blast-radius refactor.

Run from the repo root:

```bash
claude -p "$(cat docs/quality/second-opinion-gate.md)"
```

For real work, copy this file to a task-specific prompt and replace the
bracketed fields. Do not include secrets, tokens, cookies, private production
data, or unnecessary proprietary context.

---

Act as a skeptical senior engineering reviewer. Review this design before
implementation.

Goal:
[Write the user-visible goal and success bar.]

Current design:
[Summarize the API contract, data model, components, modules, boundaries,
assumptions, failure behavior, rollout/migration plan, and anything already
decided.]

Relevant constraints:
[List compatibility needs, existing patterns to preserve, performance/security
constraints, data authority, cost limits, or product rules.]

Validation plan:
[List the tests, browser/API checks, migrations, observability, rollback, and
manual verification currently planned.]

Please identify:

1. correctness or architecture risks
2. missing edge cases
3. simpler alternatives
4. validation and test gaps
5. security, privacy, cost, or operational risks
6. the highest-impact changes to make before implementation

Be blunt. Prefer concrete failure modes over general advice. If the design is
acceptable, say what still needs test evidence before shipping.
