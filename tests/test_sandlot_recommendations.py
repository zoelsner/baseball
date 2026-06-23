import unittest

import sandlot_matchup


def future_game(day=14, **extra):
    return {"date": f"2026-05-{day:02d}", **extra}


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


def snapshot(rows):
    return {
        "league_id": "league",
        "team_id": "me",
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


def raw_lineup_change(value):
    raw = {"scorer": {}}
    if value != "missing":
        raw["scorer"]["disableLineupChange"] = value
    return raw


class MatchupRecommendationTests(unittest.TestCase):
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
        self.assertGreater(rec["win_probability_delta"], 0)
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
        self.assertFalse(card["proposal"]["writes_enabled"])
        self.assertTrue(card["proposal"]["confirmation_required"])
        self.assertEqual(card["movability"]["state"], "unknown")
        self.assertEqual(
            [check["state"] for check in card["proposal"]["safety_checks"]],
            ["passed", "passed", "passed", "warning", "blocked"],
        )
        self.assertFalse(card["safety"]["live_writes"])
        self.assertFalse(card["safety"]["add_drop"])
        self.assertEqual(card["safety"]["movability"], "unknown")
        self.assertIn("latest Fantrax snapshot", card["provenance"]["source"])
        self.assertIsNone(result["no_action"])

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
        self.assertFalse(card["proposal"]["writes_enabled"])
        self.assertIn("unavailable for lineup changes", card["execution"]["reason"])

    def test_movable_participants_pass_movability_but_executor_remains_blocked(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, raw=raw_lineup_change(False)),
            player("bench2b", slot="BN", positions="2B", fppg=4.0, raw=raw_lineup_change(False)),
        ]))

        card = result["recommendations"][0]["replacement_card"]

        self.assertEqual(card["movability"]["state"], "movable")
        self.assertEqual(card["proposal"]["safety_checks"][3]["state"], "passed")
        self.assertEqual(card["proposal"]["safety_checks"][-1]["key"], "executor_ready")
        self.assertEqual(card["proposal"]["safety_checks"][-1]["state"], "blocked")
        self.assertEqual(card["execution"]["state"], "blocked")
        self.assertIn("does not mark", card["execution"]["reason"])

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
        self.assertIn("No safe lineup move", result["no_action"]["reason"])
        self.assertIn("slot provenance is untrusted", result["no_action"]["reason"])

    def test_unrelated_untrusted_slot_does_not_block_trusted_hot_swap_pair(self):
        result = sandlot_matchup.rank_matchup_improvement_actions(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0, slot_source="raw.lineupSlot"),
            player("bench2b", slot="BN", positions="2B", fppg=4.0, slot_source="raw.statusId"),
            player("unrelated", slot="OF", positions="OF", fppg=2.0, slot_source="position_fallback"),
        ]))

        self.assertEqual(result["recommendations"][0]["replacement_card"]["move_in"]["id"], "bench2b")
        self.assertEqual(result["recommendations"][0]["replacement_card"]["move_out"]["id"], "weak2b")

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
