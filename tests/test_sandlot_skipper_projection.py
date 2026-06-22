import copy
import unittest

import sandlot_skipper


def normalized_payload():
    return {
        "snapshot_id": 123,
        "taken_at": "2026-05-13T12:00:00Z",
        "team_id": "me",
        "team_name": "My Team",
        "roster": [
            {
                "id": "mine-1",
                "name": "Good Bat",
                "slot": "2B",
                "slot_source": "raw.lineupSlot",
                "positions": "2B",
                "fppg": 2.0,
            },
        ],
        "roster_meta": {"active": 1, "active_max": 1},
        "standings": [],
        "my_standing": {"rank": 4, "win": 6, "loss": 4, "fantasy_points": 1234.5},
        "data_quality": {
            "projection_ready": True,
            "recommendations_ready": True,
            "lineup_recommendations_ready": True,
            "add_drop_recommendations_ready": True,
            "projection_reasons": [],
            "recommendation_reasons": [],
            "lineup_recommendation_reasons": [],
            "add_drop_recommendation_reasons": [],
            "reasons": [],
        },
        "matchup": {
            "period_number": 4,
            "period_name": "Week 4",
            "my_score": 60,
            "opponent_score": 57,
            "opponent_team_id": "opp",
            "opponent_team_name": "Opp Team",
            "projection": {
                "model_version": "matchup_projection_v1",
                "projected_my": 122.5,
                "projected_opp": 114.2,
                "win_probability": 0.62,
                "my_remaining_games": 12,
                "opp_remaining_games": 10,
                "complete": False,
                "drivers": {
                    "current_margin": 3.0,
                    "projected_margin": 8.3,
                    "rest_of_period_delta": 5.3,
                    "game_volume_edge": 2,
                    "risk_level": "medium",
                    "summary": "You lead now by 3; You lead projected by 8.3.",
                },
            },
            "recommendations": {
                "recommendations": [
                    {
                        "rank": 1,
                        "action": {
                            "move_type": "lineup_swap",
                            "move_shape": "direct_swap",
                            "chain": [
                                {
                                    "player_id": "bench-1",
                                    "player_name": "Bench Bat",
                                    "from_slot": "BN",
                                    "to_slot": "2B",
                                },
                            ],
                        },
                        "points_delta": 2.0,
                        "win_probability_delta": 0.02,
                        "confidence": "medium",
                        "reason_chips": ["higher FP/G"],
                    },
                ],
                "no_action": None,
            },
        },
        "player_index": [],
    }


class SkipperProjectionTests(unittest.TestCase):
    def test_context_uses_normalized_payload_projection_and_quality(self):
        context = sandlot_skipper.build_context(2, normalized_payload(), prompt="deep matchup analysis")

        self.assertIn('"snapshot_id": 123', context)
        self.assertIn('"projection_ready": true', context)
        self.assertIn('"projection"', context)
        self.assertIn('"recommendations"', context)
        self.assertIn('"drivers"', context)
        self.assertIn('"my_roster"', context)

    def test_context_omits_stale_recommendations_when_lineup_slots_untrusted(self):
        payload = copy.deepcopy(normalized_payload())
        payload["data_quality"]["lineup_recommendations_ready"] = False
        payload["data_quality"]["lineup_recommendation_reasons"] = [
            "Lineup-slot source trusted for 17/37 roster players",
        ]

        context = sandlot_skipper.build_context(2, payload, prompt="deep matchup analysis")

        self.assertIn('"projection"', context)
        self.assertIn('"lineup_recommendations_ready": false', context)
        self.assertIn('"lineup_advice"', context)
        self.assertIn('"state": "paused"', context)
        self.assertNotIn('"recommendations"', context)
        self.assertNotIn('"slot":', context)
        self.assertNotIn('"slot_source":', context)

    def test_missing_lineup_ready_key_fails_closed_for_chat_context(self):
        payload = copy.deepcopy(normalized_payload())
        del payload["data_quality"]["lineup_recommendations_ready"]

        context = sandlot_skipper.build_context(2, payload, prompt="deep matchup analysis")

        self.assertIn('"lineup_advice"', context)
        self.assertIn("lineup recommendation readiness is not explicitly trusted", context)
        self.assertNotIn('"recommendations"', context)
        self.assertNotIn('"slot":', context)
        self.assertNotIn('"slot_source":', context)

    def test_context_marks_add_drop_advice_paused_when_not_ready(self):
        payload = copy.deepcopy(normalized_payload())
        payload["data_quality"]["add_drop_recommendations_ready"] = False
        payload["data_quality"]["add_drop_recommendation_reasons"] = [
            "Lineup-slot source trusted for 17/37 roster players",
        ]

        context = sandlot_skipper.build_context(2, payload, prompt="any waiver swaps?")

        self.assertIn('"add_drop_advice"', context)
        self.assertIn('"state": "paused"', context)
        self.assertIn("Lineup-slot source trusted for 17/37 roster players", context)

    def test_quick_matchup_reply_uses_projection_bands_and_drivers(self):
        reply = sandlot_skipper.deterministic_reply("how am i doing in the matchup?", normalized_payload())

        self.assertIn("favored with a slight edge", reply)
        self.assertIn("Biggest driver:", reply)
        self.assertIn("Move read:", reply)
        self.assertIn("Best lineup action:", reply)
        self.assertNotIn("%", reply)

    def test_quick_matchup_reply_pauses_lineup_action_when_slots_untrusted(self):
        payload = copy.deepcopy(normalized_payload())
        payload["data_quality"]["lineup_recommendations_ready"] = False
        payload["data_quality"]["lineup_recommendation_reasons"] = [
            "Lineup-slot source trusted for 17/37 roster players",
        ]

        reply = sandlot_skipper.deterministic_reply("how am i doing in the matchup?", payload)

        self.assertIn("Lineup action: paused", reply)
        self.assertIn("Lineup-slot source trusted for 17/37 roster players", reply)
        self.assertNotIn("Best lineup action:", reply)
        self.assertNotIn("Move read:", reply)
        self.assertNotIn("swap", reply.lower())
        self.assertNotIn("No active injury", reply)

    def test_incomplete_projection_falls_back_to_score_based_reply(self):
        payload = normalized_payload()
        payload["data_quality"] = {
            "projection_ready": False,
            "projection_reasons": ["No all-team rosters to find opponent"],
            "reasons": ["No all-team rosters to find opponent"],
        }
        payload["matchup"]["projection"] = None

        reply = sandlot_skipper.deterministic_reply("how am i doing in the matchup?", payload)

        self.assertIn("Data incomplete", reply)
        self.assertIn("score-based view only", reply)
        self.assertIn("You're up 3", reply)
        self.assertNotIn("favored with", reply)

    def test_missing_projection_with_ready_quality_is_explicit(self):
        payload = normalized_payload()
        payload["matchup"]["projection"] = None

        reply = sandlot_skipper.deterministic_reply("how am i doing in the matchup?", payload)

        self.assertIn("Projection is unavailable", reply)
        self.assertIn("You're up 3", reply)


if __name__ == "__main__":
    unittest.main()
