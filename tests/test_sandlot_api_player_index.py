import unittest

from sandlot_api import _player_index


class PlayerIndexTests(unittest.TestCase):
    def test_uses_all_team_roster_for_my_players_when_roster_rows_missing(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": []},
            "all_team_rosters": {
                "team-me": {
                    "is_me": True,
                    "rows": [
                        {"id": "p1", "name": "My Player", "team": "NYY", "slot": "2B"},
                    ],
                },
                "team-opp": {
                    "is_me": False,
                    "rows": [
                        {"id": "p2", "name": "Opponent Player", "team": "BOS", "slot": "SS"},
                    ],
                },
            },
            "free_agents": {
                "players": [
                    {"id": "p3", "name": "Free Agent", "team": "SEA", "slot": "BN"},
                ],
            },
        }

        by_id = {row["id"]: row for row in _player_index(data)}

        self.assertEqual(by_id["p1"]["source"], "mine")
        self.assertEqual(by_id["p1"]["team_id"], "team-me")
        self.assertEqual(by_id["p2"]["source"], "league")
        self.assertEqual(by_id["p3"]["source"], "free_agent")

    def test_dedups_my_players_when_both_roster_sources_exist(self):
        data = {
            "team_id": "team-me",
            "roster": {
                "rows": [
                    {"id": "p1", "name": "My Player", "team": "NYY", "slot": "2B"},
                ],
            },
            "all_team_rosters": {
                "team-me": {
                    "is_me": True,
                    "rows": [
                        {"id": "p1", "name": "My Player", "team": "NYY", "slot": "2B"},
                    ],
                },
            },
        }

        rows = [row for row in _player_index(data) if row["id"] == "p1"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "mine")

    def test_treats_matching_team_id_as_mine_when_is_me_flag_missing(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": []},
            "all_team_rosters": {
                "team-me": {
                    "rows": [
                        {"id": "p1", "name": "My Player", "team": "NYY", "slot": "2B"},
                    ],
                },
            },
        }

        rows = _player_index(data)

        self.assertEqual(rows[0]["source"], "mine")


if __name__ == "__main__":
    unittest.main()
