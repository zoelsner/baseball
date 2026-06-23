Act as a skeptical senior engineering reviewer. Review the next implementation plan before we build it.

Goal:
Unpause Sandlot Hot Swaps safely by implementing the two missing data-readiness layers:
1. future-game coverage for active players in the current scoring period
2. trusted lineup-slot provenance for active roster rows

Current production state:
- Railway production roster scrape is recovered.
- Latest checked snapshot: 218.
- Roster rows: 37.
- Snapshot errors: [].
- FP/G coverage: ok, 40/40.
- Eligibility coverage: ok, 40/40.
- Future-game coverage: missing, 0/40.
- Lineup-slot provenance: partial, 17/37 trusted.
- `/api/hot-swaps/latest`: state paused, zero proposals.
- Pause reason: `Future-game coverage 0/40, plus 1 more issue`.

Current code shape:
- `sandlot_data_quality.snapshot_data_quality()` requires future games and lineup slots before `lineup_recommendations_ready` becomes true.
- `sandlot_matchup._games_remaining()` counts `row.future_games` through the matchup period end. For pitchers, it only counts games with pitcher-specific appearance/probable-start markers.
- `fantrax_data.extract_roster()` normalizes raw `getTeamRosterInfo` rows into `name`, `id`, `team`, `positions`, `all_positions`, `slot`, `slot_source`, `fpts`, `fppg`, `injury`, and optional `future_games`.
- Production raw Fantrax roster rows currently do not provide usable future games, so the gate reports 0/40.
- Trusted slot sources include non-fallback sources such as `raw.statusId`, `raw.lineupSlot`, and `dom.lineup-btn`; `position_fallback` is untrusted.
- `sandlot_refresh._maybe_apply_dom_slot_proof()` already has an opt-in read-only DOM enrichment path guarded by `SANDLOT_CAPTURE_ROSTER_DOM_SLOTS=1`. It captures roster page HTML with cookies and applies `fantrax_dom.lineup_slots_from_html()` overrides. No clicks or Fantrax writes.
- `fantrax_dom.py` parses `lineup-btn` DOM text by player id/headshot id and returns `{player_id: {slot, slot_source: "dom.lineup-btn"}}`.
- `mlb_stats.py` already wraps MLB Stats API for player lookup and game logs, but not team schedules.

Initial implementation idea:
1. Build future-game coverage from MLB Stats schedule by MLB team abbreviation for the current scoring period (`matchup.start` through `matchup.end`), not from Fantrax player rows.
2. Enrich every roster row with a deterministic `future_games` list based on the player's MLB team schedule:
   - hitters get each remaining team game in the period
   - pitchers get team games too, but mark them as team games only; do not pretend probable-start certainty unless we can source it
3. Adjust pitcher counting semantics if needed so lack of probable-start markers does not keep all pitchers at zero forever, while still labeling pitcher game certainty lower.
4. Keep the data-quality gate explicit: future-game coverage can become ok when rows have schedule-backed games, but any uncertainty should be exposed in provenance metadata.
5. For lineup slots, enable/read the existing DOM slot proof path in a controlled way and require a production refresh to prove trusted coverage improves from 17/37 to all active rows before recommendations unpause.
6. Keep all Fantrax/Zo/add-drop/trade writes disabled.

Please review:
1. Is MLB team schedule the right source for future-game coverage, or should we find a Fantrax-specific endpoint first?
2. How should hitters vs pitchers be represented so Hot Swaps can rank the coming week without overstating pitcher certainty?
3. Should future-game coverage and lineup-slot provenance be one PR or two?
4. What tests would prove the gates are safe?
5. What production checks should be required before turning `lineup_recommendations_ready` true?
6. Any simpler or safer architecture for getting from paused to real OUT/IN Hot Swap proposals?
