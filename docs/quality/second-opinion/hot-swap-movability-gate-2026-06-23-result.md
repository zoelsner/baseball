External Claude review:
- Attempted with `claude --model opus --effort xhigh --tools ""`.
- Blocked by environment privacy policy because the review prompt included repo and production design details.
- No workaround attempted.

Internal skeptical review:
- Treat `raw.scorer.disableLineupChange === true` as a hard execution-readiness lock.
- Do not suppress the recommendation solely because it is locked; the card is still useful as a candidate and can tell the user why it cannot be executed now.
- Treat missing/non-boolean movability as `unknown`; this should be warning metadata now and a hard preflight block before any future write path.
- Keep `proposal.status = blocked` and `writes_enabled = false` regardless of movability until a separate confirmed executor contract exists.
- Put movability on the replacement card and proposal safety checklist, not only in a pre-recommendation blocker. Hiding locked candidates would make the app less explainable.
- Highest-impact next executor prerequisite: prove `disableLineupChange` against the live Fantrax DOM for locked and movable rows, then add preflight refresh and post-write verification.

Implementation checkpoint:
- Live Railway `/api/hot-swaps/latest` on snapshot `221` is still `ready` with the TJ Friedl/Ildemaro Vargas read-only proposal.
- Live `/api/snapshot/latest` row inspection shows both proposal participants carry `raw.scorer.disableLineupChange: true`.
- A production-shaped local fixture using those live rows emits the same OUT/IN pair with `movability.state = locked`, `fantrax_movability = blocked`, `executor_ready = blocked`, and `writes_enabled = false`.
