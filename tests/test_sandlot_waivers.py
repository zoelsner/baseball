import unittest

import sandlot_waivers


class WaiverSwapCandidateTests(unittest.TestCase):
    def test_aaron_judge_is_an_explicit_never_drop_anchor(self):
        roster = [{
            "id": "judge",
            "name": "  AARON   JUDGE ",
            "slot": "RES",
            "positions": "OF",
            "age": 34,
            "fppg": 0.1,
        }]

        candidates, protected = sandlot_waivers._move_out_candidates(roster, ["OF"])

        self.assertEqual(candidates, [])
        self.assertEqual(protected, ["  AARON   JUDGE "])

    def test_protected_slots_are_not_move_out_candidates(self):
        roster = [
            {"id": "min1", "name": "Charlie Condon", "slot": "MIN", "positions": "1B", "fppg": 0.0, "age": 23},
            {"id": "ir1", "name": "Brandon Woodruff", "slot": "IR", "positions": "SP", "fppg": 0.0, "age": 33},
            {"id": "res1", "name": "Reserve Corner", "slot": "RES", "positions": "1B", "fppg": 0.5, "age": 29},
        ]
        free_agents = [
            {"id": "fa1", "name": "Paul Goldschmidt", "team": "NYY", "positions": "1B", "age": 38, "stats": {"FP/G": 3.3}},
            {"id": "fa2", "name": "Brandon Young", "team": "BAL", "positions": "SP", "age": 27, "stats": {"FP/G": 12.1}},
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

    def test_inferred_fpg_cards_are_excluded_from_actionable_swaps(self):
        roster = [
            {"id": "res1", "name": "Bryan Hudson", "slot": "RES", "positions": "SP,RP", "fppg": 2.9, "age": 29},
        ]
        free_agents = [
            {
                "id": "fa1",
                "name": "Brandon Young",
                "team": "BAL",
                "positions": "SP",
                "age": 27,
                "stats": {"_cells": ["688", "", "27", "140.0", "12.1", "12%"]},
            },
        ]

        cards, diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster,
            fa_players=free_agents,
            snapshot_id=42,
            limit=8,
        )

        self.assertEqual(cards, [])
        self.assertEqual(diagnostics["parsed_add_count"], 1)
        self.assertEqual(diagnostics["usable_add_count"], 0)
        self.assertEqual(diagnostics["excluded_untrusted_value_count"], 1)

    def test_util_slot_does_not_match_il_status(self):
        roster = [
            {
                "id": "util1",
                "name": "Healthy Utility Bat",
                "slot": "UTIL",
                "positions": "OF",
                "fppg": 4.0,
                "age": 28,
                "injury": "",
            },
        ]
        free_agents = [
            {"id": "fa1", "name": "Better Outfielder", "positions": "OF", "age": 30, "stats": {"FP/G": 5.0}},
        ]

        cards, _diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster,
            fa_players=free_agents,
            snapshot_id=42,
        )

        self.assertFalse(sandlot_waivers._has_status_issue(roster[0]))
        self.assertEqual(len(cards), 1)
        self.assertNotIn("Status issue", cards[0]["evidence_chips"])

    def test_non_positive_net_delta_never_emits_a_swap_card(self):
        roster = [
            {
                "id": "dtd1",
                "name": "Day-to-day Starter",
                "slot": "OF",
                "positions": "OF",
                "fppg": 4.0,
                "age": 30,
                "injury": "DTD",
            },
        ]
        free_agents = [
            {"id": "fa1", "name": "Equal Free Agent", "positions": "OF", "age": 27, "stats": {"FP/G": 4.0}},
            {"id": "fa2", "name": "Worse Free Agent", "positions": "OF", "age": 31, "stats": {"FP/G": 3.0}},
        ]

        cards, _diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster,
            fa_players=free_agents,
            snapshot_id=42,
        )

        self.assertTrue(sandlot_waivers._has_status_issue(roster[0]))
        self.assertEqual(cards, [])

    def test_explicit_keeper_and_minor_flags_are_hard_protected(self):
        flags = [
            "protected",
            "is_protected",
            "keeper",
            "is_keeper",
            "keeper_protected",
            "minor_league",
            "minors",
            "is_minor_leaguer",
        ]
        roster = [
            {
                "id": f"protected-{flag}",
                "name": f"Protected {flag}",
                "slot": "BN",
                "positions": "1B",
                "fppg": 1.0,
                flag: True,
            }
            for flag in flags
        ]
        roster.extend([
            {
                "id": "protected-raw",
                "name": "Protected raw",
                "slot": "BN",
                "positions": "1B",
                "fppg": 1.0,
                "raw": {"protected": "yes"},
            },
            {
                "id": "protected-player",
                "name": "Protected player",
                "slot": "BN",
                "positions": "1B",
                "fppg": 1.0,
                "raw": {"player": {"keeper_protected": "1"}},
            },
            {
                "id": "protected-scorer",
                "name": "Protected scorer",
                "slot": "BN",
                "positions": "1B",
                "fppg": 1.0,
                "raw": {"scorer": {"is_protected": True}},
            },
            {
                "id": "unprotected",
                "name": "Unprotected Reserve",
                "slot": "RES",
                "positions": "1B",
                "fppg": 0.5,
                "age": 29,
            },
        ])
        free_agents = [
            {"id": "fa1", "name": "Available First Baseman", "positions": "1B", "age": 28, "stats": {"FP/G": 3.0}},
        ]

        cards, diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster,
            fa_players=free_agents,
            snapshot_id=42,
        )

        move_names = {(card.get("move_out") or {}).get("name") for card in cards}
        self.assertEqual(move_names, {"Unprotected Reserve"})
        self.assertEqual(diagnostics["protected_move_out_count"], 11)
        self.assertEqual(len(diagnostics["protected_move_outs"]), 8)

    def test_young_and_unknown_age_players_are_hard_protected_in_dynasty(self):
        roster = [
            {"id": "young", "name": "Young Upside", "slot": "RES", "positions": "OF", "fppg": 0.5, "age": 23},
            {"id": "unknown", "name": "Unknown Age", "slot": "RES", "positions": "OF", "fppg": 0.4},
            {"id": "untrusted", "name": "Untrusted Age", "slot": "RES", "positions": "OF", "fppg": 0.2, "age": 29, "raw": {}},
            {"id": "veteran", "name": "Veteran Reserve", "slot": "RES", "positions": "OF", "fppg": 0.3, "age": 29},
        ]
        free_agents = [
            {"id": "fa1", "name": "Available Outfielder", "positions": "OF", "age": 27, "stats": {"FP/G": 3.0}},
        ]

        cards, diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster,
            fa_players=free_agents,
            snapshot_id=42,
        )

        self.assertEqual({card["move_out"]["name"] for card in cards}, {"Veteran Reserve"})
        self.assertEqual(diagnostics["protected_move_out_count"], 3)
        self.assertIn("Young Upside", diagnostics["protected_move_outs"])
        self.assertIn("Unknown Age", diagnostics["protected_move_outs"])
        self.assertIn("Untrusted Age", diagnostics["protected_move_outs"])

    def test_schema_checked_free_agent_cell_age_is_preserved(self):
        candidate = sandlot_waivers._add_candidate(
            {
                "id": "fa-cell-age",
                "name": "Cell Age Free Agent",
                "positions": "OF",
                "stats": {
                    "FP/G": 5.0,
                    "_cells": ["688", "", "27", "140.0", "5.0", "12%"],
                },
            }
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["age"], 27)
        self.assertEqual(candidate["age_source"], "stats._cells[2]")
        self.assertTrue(candidate["true_fpg"])


if __name__ == "__main__":
    unittest.main()
