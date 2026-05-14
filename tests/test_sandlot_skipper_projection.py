import unittest

import sandlot_skipper


def normalized_payload():
    return {
        "snapshot_id": 123,
        "taken_at": "2026-05-13T12:00:00Z",
        "team_id": "me",
        "team_name": "My Team",
        "roster": [
            {"id": "mine-1", "name": "Good Bat", "slot": "2B", "positions": "2B", "fppg": 2.0},
        ],
        "roster_meta": {"active": 1, "active_max": 1},
        "standings": [],
        "my_standing": {"rank": 4, "win": 6, "loss": 4, "fantasy_points": 1234.5},
        "data_quality": {
            "projection_ready": True,
            "recommendations_ready": True,
            "projection_reasons": [],
            "recommendation_reasons": [],
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
        },
        "player_index": [],
    }


class SkipperProjectionTests(unittest.TestCase):
    def test_context_uses_normalized_payload_projection_and_quality(self):
        context = sandlot_skipper.build_context(2, normalized_payload(), prompt="deep matchup analysis")

        self.assertIn('"snapshot_id": 123', context)
        self.assertIn('"projection_ready": true', context)
        self.assertIn('"projection"', context)
        self.assertIn('"drivers"', context)
        self.assertIn('"my_roster"', context)

    def test_quick_matchup_reply_uses_projection_bands_and_drivers(self):
        reply = sandlot_skipper.deterministic_reply("how am i doing in the matchup?", normalized_payload())

        self.assertIn("favored with a slight edge", reply)
        self.assertIn("Biggest driver:", reply)
        self.assertIn("Move read:", reply)
        self.assertNotIn("%", reply)

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
