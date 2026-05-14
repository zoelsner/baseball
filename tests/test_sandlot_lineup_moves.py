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
            "opp": {
                "rows": [
                    player("opp", slot="SS", positions="SS", fppg=1.0),
                ],
            },
        },
    }


class LineupMoveImpactTests(unittest.TestCase):
    def test_simulates_legal_direct_bench_to_active_swap(self):
        result = sandlot_matchup.simulate_lineup_move_impact(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player("steady3b", slot="3B", positions="3B", fppg=2.0),
            player("bench2b", slot="BN", positions="2B", fppg=4.0),
        ]))

        direct = next(action for action in result["actions"] if action["move_shape"] == "direct_swap")
        self.assertEqual(direct["chain"][0]["player_id"], "bench2b")
        self.assertEqual(direct["chain"][0]["from_slot"], "BN")
        self.assertEqual(direct["chain"][0]["to_slot"], "2B")
        self.assertEqual(direct["points_delta"], 3.0)
        self.assertGreater(direct["win_probability_delta"], 0)
        self.assertIn("higher FP/G", direct["reason_chips"])

    def test_simulates_one_hop_freeing_up_swap(self):
        result = sandlot_matchup.simulate_lineup_move_impact(snapshot([
            player("corner", slot="3B", positions=["1B", "3B"], fppg=4.0),
            player("weak1b", slot="1B", positions="1B", fppg=1.0),
            player("bench3b", slot="BN", positions="3B", fppg=5.0),
        ]))

        top = result["actions"][0]
        self.assertEqual(top["move_shape"], "freeing_up_swap")
        self.assertEqual([step["player_id"] for step in top["chain"]], ["corner", "bench3b", "weak1b"])
        self.assertEqual([(step["from_slot"], step["to_slot"]) for step in top["chain"]], [
            ("3B", "1B"),
            ("BN", "3B"),
            ("1B", "BN"),
        ])
        self.assertEqual(top["points_delta"], 4.0)
        self.assertIn("legal 3B/1B chain", top["reason_chips"])

    def test_rejects_swap_when_slot_legality_cannot_be_proven(self):
        result = sandlot_matchup.simulate_lineup_move_impact(snapshot([
            player("weak2b", slot="2B", positions="2B", fppg=1.0),
            player("benchc", slot="BN", positions="C", fppg=10.0),
        ]))

        self.assertEqual(result["actions"], [])
        self.assertIn("No legal bench-to-active move improves", result["no_action"]["reason"])
        self.assertIsNone(result["no_action"]["best_rejected_delta"])

    def test_returns_no_action_when_best_legal_swap_is_negative(self):
        result = sandlot_matchup.simulate_lineup_move_impact(snapshot([
            player("good2b", slot="2B", positions="2B", fppg=4.0),
            player("bench2b", slot="BN", positions="2B", fppg=1.0),
        ]))

        self.assertEqual(result["actions"], [])
        self.assertIn("No legal bench-to-active move improves", result["no_action"]["reason"])
        self.assertEqual(result["no_action"]["best_rejected_delta"], -3.0)


if __name__ == "__main__":
    unittest.main()
