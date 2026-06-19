import unittest

import sandlot_waivers


class WaiverSwapCandidateTests(unittest.TestCase):
    def test_protected_slots_are_not_move_out_candidates(self):
        roster = [
            {"id": "min1", "name": "Charlie Condon", "slot": "MIN", "positions": "1B", "fppg": 0.0},
            {"id": "ir1", "name": "Brandon Woodruff", "slot": "IR", "positions": "SP", "fppg": 0.0},
            {"id": "res1", "name": "Reserve Corner", "slot": "RES", "positions": "1B", "fppg": 0.5},
        ]
        free_agents = [
            {"id": "fa1", "name": "Paul Goldschmidt", "team": "NYY", "positions": "1B", "stats": {"FP/G": 3.3}},
            {"id": "fa2", "name": "Brandon Young", "team": "BAL", "positions": "SP", "stats": {"FP/G": 12.1}},
        ]

        cards, diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster,
            fa_players=free_agents,
            snapshot_id=42,
            limit=8,
        )

        move_names = {(card.get("move_out") or {}).get("name") for card in cards}
        self.assertNotIn("Charlie Condon", move_names)
        self.assertNotIn("Brandon Woodruff", move_names)
        self.assertIn("Reserve Corner", move_names)
        self.assertEqual(diagnostics["protected_move_out_count"], 2)
        self.assertIn("Charlie Condon", diagnostics["protected_move_outs"])
        self.assertIn("Brandon Woodruff", diagnostics["protected_move_outs"])


if __name__ == "__main__":
    unittest.main()
