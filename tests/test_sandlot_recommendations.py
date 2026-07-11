import unittest

import sandlot_matchup
from sandlot_api import _snapshot_payload


def future_game(day=14, **extra):
    return {"date": f"2026-05-{day:02d}", "gameDate": f"2026-05-{day:02d}T23:05:00Z", **extra}


def player(pid, *, slot, positions, fppg, games=1, name=None, slot_source=None, **extra):
    return {
        "id": pid,
        "name": name or pid,
        "slot": slot,
        "slot_source": slot_source or ("raw.statusId" if slot == "BN" else "raw.lineupSlot"),
        "positions": positions,
        "all_positions": positions if isinstance(positions, list) else str(positions).split("/"),
        "fppg": fppg,
        "future_games": [future_game(14 + idx) for idx in range(games)],
        **extra,
    }


def snapshot(rows, **overrides):
    data = {
        "snapshot_id": "test-snapshot",
        "league_id": "league",
        "team_id": "me",
        "movability_now": "2026-05-13T12:00:00Z",
        "matchup": {
            "my_score": 0,
            "opponent_score": 0,
            "opponent_team_id": "opp",
            "period_number": 4,
            "end": "2026-05-20",
        },
        "roster": {"rows": rows},
        "all_team_rosters": {
            "opp": {"rows": [player("opp", slot="SS", positions="SS", fppg=1.0)]},
        },
    }
    data.update(overrides)
    return data


def raw_lineup_change(value):
    raw = {"scorer": {}}
    if value != "missing":
        raw["scorer"]["disableLineupChange"] = value
    return raw


def destination_eligibility(*, statuses, positions):
    return {
        "source": "fantrax.raw.eligibleStatusIds+eligiblePosIds",
        "eligible_statuses": statuses,
        "eligible_positions": positions,
    }


class MatchupRecommendationTests(unittest.TestCase):
    def test_snapshot_payload_binds_contract_to_persisted_snapshot_id(self):
        data = snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player("bench2b", slot="BN", positions="2B", fppg=4.0),
        ])
        data.pop("snapshot_id")

        payload = _snapshot_payload({"id": 777, "data": data})

        contract = payload["matchup"]["recommendations"]["recommendations"][0]["replacement_card"]["proposal"]["contract"]
        self.assertEqual(contract["snapshot_id"], 777)

    def test_ranks_meaningful_actions_by_delta(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("corner", slot="3B", positions=["1B", "3B"], fppg=4.0),
            player("weak1b", slot="1B", positions="1B", fppg=1.0),
            player("bench3b", slot="BN", positions="3B", fppg=5.0),
        ]))

        rec = result["recommendations"][0]
        self.assertEqual(rec["rank"], 1)
        self.assertEqual(rec["action"]["move_shape"], "freeing_up_swap")
        self.assertEqual(rec["points_delta"], 4.0)
        self.assertIsNone(rec["win_probability_delta"])
        self.assertFalse(rec["probability_calibrated"])
        self.assertEqual(rec["confidence_basis"], "projected_points_magnitude")
        self.assertIn(rec["confidence"], {"medium", "high"})
        self.assertIn("legal 3B/1B chain", rec["reason_chips"])
        card = rec["replacement_card"]
        self.assertEqual(card["type"], "lineup_hot_swap")
        self.assertEqual(card["move_in"]["name"], "bench3b")
        self.assertEqual(card["move_out"]["name"], "weak1b")
        self.assertEqual(card["execution"]["state"], "blocked")
        self.assertEqual(card["execution"]["label"], "Propose swap")
        self.assertEqual(card["proposal"]["id"], "lineup-swap:weak1b:bench3b:3B")
        self.assertEqual(card["proposal"]["status"], "blocked")
        self.assertFalse(card["proposal"]["executable"])
        self.assertFalse(card["proposal"]["writes_enabled"])
        self.assertTrue(card["proposal"]["confirmation_required"])
        contract = card["proposal"]["contract"]
        self.assertEqual(contract["version"], 2)
        self.assertEqual(contract["snapshot_id"], "test-snapshot")
        self.assertEqual(contract["move_out"]["id"], "weak1b")
        self.assertEqual(contract["move_in"]["id"], "bench3b")
        self.assertEqual(contract["target_slot"], "3B")
        self.assertFalse(contract["executable"])
        self.assertFalse(contract["writes_enabled"])
        self.assertEqual(len(contract["input_hash"]), 64)
        self.assertTrue(contract["freshness_policy"]["requires_live_preflight"])
        self.assertEqual(contract["freshness_policy"]["preflight_snapshot_max_age_minutes"], 5)
        self.assertEqual(contract["freshness_policy"]["confirmation_max_age_seconds"], 120)
        self.assertTrue(contract["post_write_verification"]["required"])
        self.assertEqual(contract["confirmation"]["mode"], "exact_contract_match")
        self.assertIsNone(contract["projected_benefit"]["win_probability_delta"])
        self.assertFalse(contract["projected_benefit"]["probability_calibrated"])
        self.assertEqual(
            contract["confirmation"]["expected"]["input_hash"],
            contract["input_hash"],
        )
        self.assertEqual(
            contract["confirmation"]["match_fields"],
            ["proposal_id", "input_hash", "snapshot_id", "slot_moves"],
        )
        self.assertTrue(contract["requires_multi_step"])
        self.assertEqual(
            [(move["player_id"], move["from_slot"], move["to_slot"]) for move in contract["slot_moves"]],
            [("corner", "3B", "1B"), ("bench3b", "BN", "3B"), ("weak1b", "1B", "BN")],
        )
        self.assertEqual(card["movability"]["state"], "unknown")
        self.assertEqual(
            [check["state"] for check in card["proposal"]["safety_checks"]],
            ["passed", "passed", "passed", "warning", "blocked"],
        )
        self.assertFalse(card["safety"]["live_writes"])
        self.assertFalse(card["safety"]["add_drop"])
        self.assertEqual(card["safety"]["movability"], "unknown")
        self.assertEqual(card["risk_label"], "unknown")
        self.assertEqual(card["confidence_basis"], "projected_points_magnitude")
        self.assertIn("win probability is not calibrated", card["risk"])
        self.assertIsNone(card["projected_benefit"]["win_probability_delta"])
        self.assertFalse(card["projected_benefit"]["probability_calibrated"])
        self.assertIn("latest Fantrax snapshot", card["provenance"]["source"])
        self.assertIsNone(result["thresholds"]["win_probability_delta"])
        self.assertFalse(result["thresholds"]["probability_calibrated"])
        self.assertIsNone(result["no_action"])

    def test_uncalibrated_probability_does_not_suppress_large_point_gain(self):
        data = snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player("bench2b", slot="BN", positions="2B", fppg=6.0),
        ])
        data["matchup"]["my_score"] = 500
        data["matchup"]["opponent_score"] = 0

        result = sandlot_matchup.rank_matchup_improvement_actions(data)

        self.assertEqual(result["recommendations"][0]["points_delta"], 5.0)
        self.assertIsNone(result["recommendations"][0]["win_probability_delta"])
        self.assertEqual(result["recommendations"][0]["confidence"], "high")

    def test_suspended_active_player_can_be_replaced_but_not_promoted(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "suspended",
                slot="2B",
                positions="2B",
                fppg=4.0,
                injury="SUSP",
                raw=raw_lineup_change(False),
            ),
            player(
                "available-bench",
                slot="BN",
                positions="2B",
                fppg=3.0,
                raw=raw_lineup_change(False),
            ),
        ]))

        recommendation = result["recommendations"][0]
        card = recommendation["replacement_card"]
        self.assertEqual(card["move_in"]["id"], "available-bench")
        self.assertEqual(card["move_out"]["id"], "suspended")
        self.assertEqual(card["move_out"]["injury"], "SUSP")
        self.assertTrue(card["move_out"]["unavailable"])
        self.assertFalse(card["move_in"]["unavailable"])
        self.assertEqual(recommendation["points_delta"], 3.0)

    def test_raw_suspended_bench_player_is_never_promoted(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak", slot="2B", positions="2B", fppg=1.0),
            player(
                "raw-suspended",
                slot="BN",
                positions="2B",
                fppg=8.0,
                raw={"scorer": {"disableLineupChange": False}, "player": {"suspended": True}},
            ),
            player("available", slot="BN", positions="2B", fppg=3.0),
        ]))

        promoted_ids = {
            recommendation["replacement_card"]["move_in"]["id"]
            for recommendation in result["recommendations"]
        }
        self.assertEqual(promoted_ids, {"available"})

    def test_suppresses_longer_chain_when_direct_swap_has_same_outcome(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("cortes", slot="OF", positions=["OF", "UT"], fppg=2.0),
            player("bridge", slot="UT", positions=["SS", "OF", "UT"], fppg=5.0),
            player("lile", slot="BN", positions=["OF", "UT"], fppg=4.0),
        ]))

        matching = [
            recommendation
            for recommendation in result["recommendations"]
            if recommendation["replacement_card"]["move_in"]["id"] == "lile"
            and recommendation["replacement_card"]["move_out"]["id"] == "cortes"
        ]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["action"]["move_shape"], "direct_swap")
        self.assertEqual(len(matching[0]["action"]["chain"]), 2)

    def test_multi_step_chain_checks_bridge_player_movability(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "locked-corner",
                slot="3B",
                positions=["1B", "3B"],
                fppg=4.0,
                raw=raw_lineup_change(True),
            ),
            player(
                "weak1b",
                slot="1B",
                positions="1B",
                fppg=1.0,
                raw=raw_lineup_change(False),
            ),
            player(
                "bench3b",
                slot="BN",
                positions="3B",
                fppg=5.0,
                raw=raw_lineup_change(False),
            ),
        ]))

        chain_card = next(
            recommendation["replacement_card"]
            for recommendation in result["recommendations"]
            if recommendation["action"]["move_shape"] == "freeing_up_swap"
        )

        self.assertEqual(chain_card["movability"]["state"], "locked")
        self.assertEqual(
            chain_card["movability"]["participants"]["bridge_1"]["id"],
            "locked-corner",
        )
        self.assertEqual(
            chain_card["movability"]["participants"]["bridge_1"]["state"],
            "locked",
        )
        self.assertIn("locked-corner", chain_card["movability"]["reason"])
        self.assertEqual(chain_card["proposal"]["safety_checks"][3]["state"], "blocked")

    def test_locked_movability_surfaces_but_keeps_recommendation_non_executable(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, raw=raw_lineup_change(True)),
            player("bench2b", slot="BN", positions="2B", fppg=4.0, raw=raw_lineup_change(False)),
        ]))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["move_in"]["id"], "bench2b")
        self.assertEqual(card["move_out"]["id"], "weak2b")
        self.assertEqual(card["movability"]["state"], "locked")
        self.assertIn("weak2b", card["movability"]["reason"])
        self.assertEqual(card["movability"]["participants"]["move_out"]["state"], "locked")
        self.assertEqual(card["movability"]["participants"]["move_in"]["state"], "movable")
        self.assertEqual(card["proposal"]["safety_checks"][3]["key"], "fantrax_movability")
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "blocked")
        self.assertEqual(card["proposal"]["status"], "blocked")
        self.assertFalse(card["proposal"]["executable"])
        self.assertFalse(card["proposal"]["writes_enabled"])
        self.assertIn("unavailable for lineup changes", card["execution"]["reason"])

    def test_movable_participants_pass_movability_but_executor_remains_blocked(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, raw=raw_lineup_change(False)),
            player("bench2b", slot="BN", positions="2B", fppg=4.0, raw=raw_lineup_change(False)),
        ]))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["movability"]["state"], "movable")
        self.assertEqual(card["deadline"]["state"], "known")
        self.assertEqual(card["deadline"]["at"], "2026-05-14T23:05:00+00:00")
        self.assertEqual(card["proposal"]["contract"]["movability"]["deadline"], card["deadline"])
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "passed")
        self.assertEqual(card["proposal"]["safety_checks"][-1]["key"], "executor_ready")
        self.assertEqual(card["proposal"]["safety_checks"][-1]["state"], "blocked")
        self.assertEqual(card["execution"]["state"], "blocked")
        self.assertIn("does not mark", card["execution"]["reason"])
        self.assertEqual(card["proposal"]["contract"]["movability"]["state"], "movable")
        self.assertEqual(card["proposal"]["contract"]["blocked_by"], ["executor_ready"])
        self.assertFalse(card["proposal"]["contract"]["requires_multi_step"])
        self.assertEqual(len(card["proposal"]["contract"]["slot_moves"]), 2)

    def test_lineup_deadline_uses_earliest_participant_game_start(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "weak2b",
                slot="2B",
                positions="2B",
                fppg=1.0,
                raw=raw_lineup_change(False),
                future_games=[future_game(14, gameDate="2026-05-14T21:10:00Z")],
            ),
            player(
                "bench2b",
                slot="BN",
                positions="2B",
                fppg=4.0,
                raw=raw_lineup_change(False),
                future_games=[future_game(14, gameDate="2026-05-14T23:05:00Z")],
            ),
        ], movability_now="2026-05-14T12:00:00Z"))

        deadline = result["recommendations"][0]["replacement_card"]["deadline"]

        self.assertEqual(deadline["state"], "known")
        self.assertEqual(deadline["at"], "2026-05-14T21:10:00+00:00")
        self.assertEqual(deadline["participant_role"], "move_out")
        self.assertEqual(deadline["participant_id"], "weak2b")

    def test_contract_hash_covers_freshness_and_post_write_policies(self):
        base = snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, raw=raw_lineup_change(False)),
            player("bench2b", slot="BN", positions="2B", fppg=4.0, raw=raw_lineup_change(False)),
        ])
        first = sandlot_matchup.rank_matchup_improvement_actions(base)
        first_contract = first["recommendations"][0]["replacement_card"]["proposal"]["contract"]

        changed = dict(first_contract)
        changed["freshness_policy"] = {
            **first_contract["freshness_policy"],
            "confirmation_max_age_seconds": 121,
        }

        self.assertNotEqual(
            sandlot_matchup._contract_input_hash(changed),
            first_contract["input_hash"],
        )

    def test_started_mlb_game_locks_even_when_provider_says_movable(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "weak2b",
                slot="2B",
                positions="2B",
                fppg=1.0,
                raw=raw_lineup_change(False),
                future_games=[future_game(14, gameDate="2026-05-14T10:00:00Z")],
            ),
            player(
                "bench2b",
                slot="BN",
                positions="2B",
                fppg=4.0,
                raw=raw_lineup_change(False),
                future_games=[future_game(14, gameDate="2026-05-14T23:05:00Z")],
            ),
        ], movability_now="2026-05-14T12:00:00Z"))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["movability"]["state"], "locked")
        self.assertEqual(card["movability"]["participants"]["move_out"]["provider"]["raw_value"], False)
        self.assertEqual(card["movability"]["participants"]["move_out"]["schedule"]["state"], "locked")
        self.assertIn("already started", card["movability"]["participants"]["move_out"]["schedule"]["reason"])
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "blocked")
        self.assertEqual(card["proposal"]["contract"]["blocked_by"], ["fantrax_movability", "executor_ready"])

    def test_missing_mlb_game_start_is_unknown_when_provider_says_movable(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "weak2b",
                slot="2B",
                positions="2B",
                fppg=1.0,
                raw=raw_lineup_change(False),
                future_games=[future_game(14, gameDate=None)],
            ),
            player(
                "bench2b",
                slot="BN",
                positions="2B",
                fppg=4.0,
                raw=raw_lineup_change(False),
                future_games=[future_game(15)],
            ),
        ], movability_now="2026-05-14T12:00:00Z"))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["movability"]["state"], "unknown")
        self.assertEqual(card["movability"]["participants"]["move_out"]["schedule"]["state"], "unknown")
        self.assertIn("missing a start time", card["movability"]["participants"]["move_out"]["schedule"]["reason"])
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "warning")
        self.assertEqual(card["proposal"]["contract"]["blocked_by"], ["fantrax_movability", "executor_ready"])

    def test_missing_movability_field_is_unknown_warning(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, raw=raw_lineup_change("missing")),
            player("bench2b", slot="BN", positions="2B", fppg=4.0, raw=raw_lineup_change(False)),
        ]))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["movability"]["state"], "unknown")
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "warning")
        self.assertIn("missing", card["movability"]["reason"])
        self.assertEqual(card["execution"]["state"], "blocked")

    def test_current_fantrax_destination_fields_prove_direct_swap_without_legacy_flag(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "weak2b",
                slot="2B",
                positions="2B",
                fppg=1.0,
                raw=raw_lineup_change("missing"),
                lineup_eligibility=destination_eligibility(statuses=["ACTIVE", "RES"], positions=["2B", "UT"]),
            ),
            player(
                "bench2b",
                slot="BN",
                positions="2B",
                fppg=4.0,
                raw=raw_lineup_change("missing"),
                lineup_eligibility=destination_eligibility(statuses=["ACTIVE", "RES"], positions=["2B", "UT"]),
            ),
        ]))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["movability"]["state"], "movable")
        self.assertEqual(card["movability"]["participants"]["move_in"]["target_slot"], "2B")
        self.assertEqual(card["movability"]["participants"]["move_out"]["target_slot"], "BN")
        self.assertEqual(
            card["movability"]["participants"]["move_in"]["provider"]["destination"]["state"],
            "eligible",
        )
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "passed")

    def test_current_fantrax_destination_fields_block_unlisted_target(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "weak2b",
                slot="2B",
                positions="2B",
                fppg=1.0,
                raw=raw_lineup_change("missing"),
                lineup_eligibility=destination_eligibility(statuses=["ACTIVE"], positions=["2B"]),
            ),
            player(
                "bench2b",
                slot="BN",
                positions="2B",
                fppg=4.0,
                raw=raw_lineup_change("missing"),
                lineup_eligibility=destination_eligibility(statuses=["ACTIVE", "RES"], positions=["2B"]),
            ),
        ]))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["movability"]["state"], "locked")
        self.assertEqual(
            card["movability"]["participants"]["move_out"]["provider"]["destination"]["state"],
            "ineligible",
        )
        self.assertIn("does not allow", card["movability"]["reason"])
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "blocked")

    def test_current_fantrax_drop_action_proves_roster_exit_before_game_start(self):
        row = player(
            "drop-candidate",
            slot="BN",
            positions="2B",
            fppg=1.0,
            transaction_eligibility={
                "source": "fantrax.raw.actions.typeId",
                "action_type_ids": ["3", "4"],
                "drop_available": True,
            },
        )

        availability = sandlot_matchup.player_roster_exit_availability(
            row,
            now=sandlot_matchup._parse_game_start("2026-05-14T12:00:00Z"),
        )

        self.assertEqual(availability["state"], "movable")
        self.assertTrue(availability["provider"]["drop_available"])

    def test_missing_fantrax_drop_action_blocks_roster_exit(self):
        row = player(
            "no-drop",
            slot="BN",
            positions="2B",
            fppg=1.0,
            transaction_eligibility={
                "source": "fantrax.raw.actions.typeId",
                "action_type_ids": ["4"],
                "drop_available": False,
            },
        )

        availability = sandlot_matchup.player_roster_exit_availability(row)

        self.assertEqual(availability["state"], "locked")
        self.assertIn("does not expose a Drop action", availability["reason"])

    def test_suppresses_moves_below_meaningful_threshold(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("ok2b", slot="2B", positions="2B", fppg=1.0),
            player("bench2b", slot="BN", positions="2B", fppg=1.3),
        ]))

        self.assertEqual(result["recommendations"], [])
        self.assertIn("meaningful-gain threshold", result["no_action"]["reason"])
        self.assertEqual(result["no_action"]["best_rejected_delta"], 0.3)
        self.assertEqual(result["no_action"]["threshold"], sandlot_matchup.MIN_MEANINGFUL_POINTS_DELTA)

    def test_no_action_when_no_legal_move_exists(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player("benchc", slot="BN", positions="C", fppg=10.0),
        ]))

        self.assertEqual(result["recommendations"], [])
        self.assertIn("meaningful-gain threshold", result["no_action"]["reason"])
        self.assertIsNone(result["no_action"]["best_rejected_delta"])

    def test_data_quality_suppresses_recommendations(self):
        data_quality = {
            "projection_ready": True,
            "recommendations_ready": False,
            "recommendation_reasons": ["Eligibility/position coverage 0/2"],
            "reasons": ["Eligibility/position coverage 0/2"],
        }

        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player("bench2b", slot="BN", positions="2B", fppg=4.0),
        ]), data_quality)

        self.assertEqual(result["recommendations"], [])
        self.assertIn("Recommendation data incomplete", result["no_action"]["reason"])
        self.assertIn("Eligibility/position", result["no_action"]["reason"])

    def test_missing_lineup_ready_flag_fails_closed_even_when_legacy_quality_is_ready(self):
        data_quality = {
            "projection_ready": True,
            "recommendations_ready": True,
            "recommendation_reasons": [],
            "reasons": [],
        }

        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player("bench2b", slot="BN", positions="2B", fppg=4.0),
        ]), data_quality)

        self.assertEqual(result["recommendations"], [])
        self.assertIn("Recommendation data incomplete", result["no_action"]["reason"])
        self.assertIn("lineup", result["no_action"]["reason"].lower())

    def test_untrusted_slot_source_suppresses_recommendations(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, slot_source="position_fallback"),
            player("bench2b", slot="BN", positions="2B", fppg=4.0),
        ]))

        self.assertEqual(result["recommendations"], [])
        self.assertIsNone(result["base_projection"])
        self.assertIn("No matchup projection is available", result["no_action"]["reason"])
        self.assertIn("Projection lineup-slot source usable for 1/2 active players", result["no_action"]["reason"])

    def test_unrelated_untrusted_slot_fails_closed_when_base_projection_is_unavailable(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, slot_source="raw.lineupSlot"),
            player("bench2b", slot="BN", positions="2B", fppg=4.0, slot_source="raw.statusId"),
            player("unrelated", slot="OF", positions="OF", fppg=2.0, slot_source="position_fallback"),
        ]))

        self.assertEqual(result["recommendations"], [])
        self.assertIsNone(result["base_projection"])
        self.assertIn("No matchup projection is available", result["no_action"]["reason"])
        self.assertIn("Projection lineup-slot source usable for 2/3 active players", result["no_action"]["reason"])

    def test_failed_future_game_provenance_blocks_participant_hot_swap(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player(
                "bench2b",
                slot="BN",
                positions="2B",
                fppg=4.0,
                future_games=[],
                future_games_source="mlb_schedule",
                future_games_status="unresolved_team",
            ),
        ]))

        self.assertEqual(result["recommendations"], [])
        self.assertIn("future-game provenance is not trusted", result["no_action"]["reason"])

    def test_pitcher_without_probable_start_provenance_is_not_hot_swap_candidate(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player(
                "weak-sp",
                slot="SP",
                positions="SP",
                fppg=1.0,
                future_games=[future_game(14, probable_start=True)],
                future_games_source="mlb_schedule",
                future_games_status="ok",
                future_games_scope="pitcher_probable_starts",
            ),
            player(
                "bench-sp",
                slot="BN",
                positions="SP",
                fppg=12.0,
                future_games=[],
                future_games_source="mlb_schedule",
                future_games_status="pitcher_probables_unavailable",
                future_games_scope="pitcher_probable_starts",
            ),
        ]))

        self.assertEqual(result["recommendations"], [])
        self.assertIn("future-game provenance is not trusted", result["no_action"]["reason"])

    def test_protected_minors_and_il_players_are_not_hot_swap_candidates(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("protected", slot="OF", positions="OF", fppg=1.0, protected=True),
            player("min", slot="MIN", positions="OF", fppg=20.0),
            player("il", slot="IR", positions="OF", fppg=20.0),
            player("bench", slot="BN", positions="OF", fppg=8.0),
        ]))

        self.assertEqual(result["recommendations"], [])
        self.assertIn("No active-and-bench roster combination", result["no_action"]["reason"])


if __name__ == "__main__":
    unittest.main()
