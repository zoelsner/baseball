# Raw audit reports — working material, not conclusions

These are the unedited outputs of the four audit agents that examined
`origin/main` @ `bd54135` on 2026-07-24. They are preserved because they contain
substantially more detail than the synthesized documents — full route tables,
verbatim formulas, complete field-by-field contract traces, per-document
divergence lists.

**Read the synthesized documents first.** These four files are the evidence base
behind them:

- [../risk-register.md](../risk-register.md)
- [../assessment.md](../assessment.md)
- [../doc-drift.md](../doc-drift.md)
- [../orientation.md](../orientation.md)

## Caveats — read before citing anything here

**These reports were not fully verified.** The synthesized documents mark each
finding ✅ (independently re-checked against source) or 🔶 (agent-reported only).
That distinction does not exist in these files. Everything here is at the 🔶
level unless it also appears with a ✅ in the register.

Specific known issues:

- **Line numbers drift.** Roughly one finding in six cited a line 2–3 off. Substance was generally accurate; exact citations were not always.
- **`audit-contract.md` was produced under a partially incorrect brief.** The agent was initially told the frontend used in-browser Babel with no module system — false since May 2026. It detected the contradiction early and audited the real esbuild tree, and its report opens with that correction. Treat any residual Babel-era framing as an artifact.
- **Counts and totals were sometimes estimated** rather than computed, particularly in `audit-docs.md` where the test suite could not be executed (no venv available in the audit worktree).
- **Severity ratings are the agents' own** and were in several cases adjusted during synthesis.

| File | Scope |
|---|---|
| `audit-security.md` | Route exposure table, write-boundary analysis, credential handling, supply chain |
| `audit-analysis.md` | Projection formulas verbatim, learning-loop gate analysis, ML recommendations |
| `audit-contract.md` | API↔UI field trace, test-coverage gap map, frontend state, bundle drift |
| `audit-docs.md` | Per-document divergence lists with both-side citations |
