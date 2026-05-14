import unittest

import sandlot_matchup


def future_game(day=14):
    return {"date": f"2026-05-{day:02d}"}


def player(pid, *, slot, positions, fppg, games=1, name=None):
    return {
        "id": pid,
        "name": name or pid,
        "slot": slot,
        "positions": positions,
        "all_positions": positions if isinstance(positions, list) else str(positions).split("/"),
        "fppg": fppg,
        "future_games": [future_game(14 + idx) for idx in range(games)],
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
        self.assertIsNone(result["no_action"])

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


if __name__ == "__main__":
    unittest.main()
