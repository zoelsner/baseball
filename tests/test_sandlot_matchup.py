import unittest

import sandlot_matchup
from sandlot_api import _snapshot_payload


def future_game(day):
    return {"date": f"2026-05-{day:02d}"}


class MatchupProjectionTests(unittest.TestCase):
    def test_projects_active_rows_with_future_games_and_probability(self):
        snapshot = {
            "matchup": {
                "my_score": 10,
                "opponent_score": 8,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {
                "rows": [
                    {
                        "id": "mine-1",
                        "slot": "2B",
                        "fppg": 2.0,
                        "future_games": [future_game(14), future_game(21)],
                    },
                    {
                        "id": "mine-bench",
                        "slot": "BN",
                        "fppg": 99.0,
                        "future_games": [future_game(14)],
                    },
                    {
                        "id": "mine-negative",
                        "slot": "OF",
                        "fppg": -1.0,
                        "future_games": [future_game(14)],
                    },
                ],
            },
            "all_team_rosters": {
                "opp": {
                    "rows": [
                        {
                            "id": "opp-1",
                            "slot": "SS",
                            "fppg": 1.5,
                            "future_games": [future_game(14), future_game(15)],
                        },
                    ],
                },
            },
        }

        projection = sandlot_matchup.compute_projection(snapshot)

        self.assertEqual(projection["projected_my"], 11.0)
        self.assertEqual(projection["projected_opp"], 11.0)
        self.assertEqual(projection["my_remaining_games"], 2)
        self.assertEqual(projection["opp_remaining_games"], 2)
        self.assertEqual(projection["win_probability"], 0.5)
        self.assertFalse(projection["complete"])

    def test_returns_completed_projection_without_future_game_requirements(self):
        projection = sandlot_matchup.compute_projection({
            "matchup": {
                "my_score": 12,
                "opponent_score": 20,
                "complete": True,
            },
        })

        self.assertEqual(projection["projected_my"], 12.0)
        self.assertEqual(projection["projected_opp"], 20.0)
        self.assertEqual(projection["my_remaining_games"], 0)
        self.assertEqual(projection["opp_remaining_games"], 0)
        self.assertEqual(projection["win_probability"], 0.0)
        self.assertTrue(projection["complete"])

    def test_returns_none_when_opponent_roster_is_missing(self):
        projection = sandlot_matchup.compute_projection({
            "matchup": {
                "my_score": 10,
                "opponent_score": 8,
                "opponent_team_id": "missing",
                "end": "2026-05-20",
            },
            "roster": {"rows": []},
            "all_team_rosters": {},
        })

        self.assertIsNone(projection)

    def test_returns_none_when_non_complete_matchup_has_no_future_games(self):
        projection = sandlot_matchup.compute_projection({
            "matchup": {
                "my_score": 10,
                "opponent_score": 8,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {
                "rows": [
                    {"id": "mine-1", "slot": "2B", "fppg": 2.0},
                ],
            },
            "all_team_rosters": {
                "opp": {
                    "rows": [
                        {"id": "opp-1", "slot": "SS", "fppg": 1.5},
                    ],
                },
            },
        })

        self.assertIsNone(projection)

    def test_snapshot_payload_embeds_projection_in_matchup_block(self):
        row = {
            "id": 123,
            "data": {
                "team_id": "me",
                "matchup": {
                    "my_score": 1,
                    "opponent_score": 1,
                    "opponent_team_id": "opp",
                    "end": "2026-05-20",
                },
                "roster": {
                    "rows": [
                        {
                            "id": "mine-1",
                            "name": "My Player",
                            "slot": "2B",
                            "fppg": 2.0,
                            "future_games": [future_game(14)],
                        },
                    ],
                },
                "all_team_rosters": {
                    "opp": {
                        "rows": [
                            {
                                "id": "opp-1",
                                "name": "Opponent Player",
                                "slot": "SS",
                                "fppg": 1.0,
                                "future_games": [future_game(14)],
                            },
                        ],
                    },
                },
            },
        }

        payload = _snapshot_payload(row)

        self.assertEqual(payload["matchup"]["projection"]["projected_my"], 3.0)
        self.assertEqual(payload["matchup"]["projection"]["projected_opp"], 2.0)


if __name__ == "__main__":
    unittest.main()
