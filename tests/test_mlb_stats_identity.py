import unittest
from unittest.mock import patch

import mlb_stats


class MlbStatsIdentityTests(unittest.TestCase):
    def test_team_mismatch_and_ambiguous_name_fail_closed(self):
        players = [
            {"id": 1, "fullName": "Andrés Muñoz", "currentTeam": {"id": 136}},
            {"id": 2, "fullName": "Same Name", "currentTeam": {"abbreviation": "NYY"}},
            {"id": 3, "fullName": "Same Name", "currentTeam": {"abbreviation": "BOS"}},
        ]
        with (
            patch.object(mlb_stats, "_get_active_players", return_value=players),
            patch.object(mlb_stats, "_get_team_abbreviations", return_value={136: "SEA"}),
        ):
            self.assertEqual(mlb_stats.lookup_player_by_name("Andres Munoz", "SEA", 2026), 1)
            self.assertIsNone(mlb_stats.lookup_player_by_name("Andres Munoz", "TOR", 2026))
            self.assertIsNone(mlb_stats.lookup_player_by_name("Same Name", None, 2026))
            self.assertEqual(mlb_stats.lookup_player_by_name("Same Name", "BOS", 2026), 3)

            mismatch = mlb_stats.resolve_player_identity("Andres Munoz", "TOR", 2026)
            ambiguous = mlb_stats.resolve_player_identity("Same Name", None, 2026)

        self.assertEqual(mismatch["status"], "team_mismatch")
        self.assertIsNone(mismatch["mlb_id"])
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertIsNone(ambiguous["mlb_id"])


if __name__ == "__main__":
    unittest.main()
