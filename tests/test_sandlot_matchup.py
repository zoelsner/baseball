import unittest

import sandlot_matchup
from sandlot_api import _snapshot_payload


def future_game(day, **extra):
    return {"date": f"2026-05-{day:02d}", **extra}


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

        self.assertEqual(projection["model_version"], sandlot_matchup.MODEL_VERSION)
        self.assertEqual(projection["projected_my"], 11.0)
        self.assertEqual(projection["projected_opp"], 11.0)
        self.assertEqual(projection["my_remaining_games"], 2)
        self.assertEqual(projection["opp_remaining_games"], 2)
        self.assertEqual(projection["win_probability"], 0.5)
        self.assertEqual(projection["drivers"]["current_margin"], 2.0)
        self.assertEqual(projection["drivers"]["projected_margin"], 0.0)
        self.assertEqual(projection["drivers"]["rest_of_period_delta"], -2.0)
        self.assertEqual(projection["drivers"]["game_volume_edge"], 0)
        self.assertEqual(projection["drivers"]["risk_level"], "high")
        self.assertIn("Rest-of-period swing is -2", projection["drivers"]["summary"])
        self.assertFalse(projection["complete"])

    def test_does_not_project_pitcher_team_games_as_appearances(self):
        snapshot = {
            "matchup": {
                "my_score": 0,
                "opponent_score": 0,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {
                "rows": [
                    {
                        "id": "mine-hitter",
                        "slot": "2B",
                        "positions": "2B",
                        "fppg": 2.0,
                        "future_games": [future_game(14), future_game(15)],
                    },
                    {
                        "id": "mine-sp",
                        "slot": "SP",
                        "positions": "SP",
                        "fppg": 12.0,
                        "future_games": [
                            future_game(14, player={"id": "mine-sp", "name": "Mine SP"}),
                            future_game(15, player={"id": "mine-sp", "name": "Mine SP"}),
                        ],
                    },
                ],
            },
            "all_team_rosters": {
                "opp": {
                    "rows": [
                        {
                            "id": "opp-hitter",
                            "slot": "SS",
                            "positions": "SS",
                            "fppg": 1.0,
                            "future_games": [future_game(14)],
                        },
                        {
                            "id": "opp-rp",
                            "slot": "RP",
                            "positions": "RP",
                            "fppg": 8.0,
                            "future_games": [
                                future_game(14, player={"id": "opp-rp", "name": "Opp RP"}),
                                future_game(15, player={"id": "opp-rp", "name": "Opp RP"}),
                                future_game(16, player={"id": "opp-rp", "name": "Opp RP"}),
                            ],
                        },
                    ],
                },
            },
        }

        projection = sandlot_matchup.compute_projection(snapshot)

        self.assertEqual(projection["projected_my"], 4.0)
        self.assertEqual(projection["projected_opp"], 1.0)
        self.assertEqual(projection["my_remaining_games"], 2)
        self.assertEqual(projection["opp_remaining_games"], 1)
        self.assertEqual(projection["drivers"]["game_volume_edge"], 1)

    def test_counts_pitcher_game_only_with_specific_appearance_marker(self):
        snapshot = {
            "matchup": {
                "my_score": 0,
                "opponent_score": 0,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {
                "rows": [
                    {
                        "id": "mine-sp",
                        "slot": "SP",
                        "positions": "SP",
                        "fppg": 10.0,
                        "future_games": [
                            future_game(14, probable_start=True),
                            future_game(15),
                        ],
                    },
                ],
            },
            "all_team_rosters": {"opp": {"rows": []}},
        }

        projection = sandlot_matchup.compute_projection(snapshot)

        self.assertEqual(projection["projected_my"], 10.0)
        self.assertEqual(projection["projected_opp"], 0.0)
        self.assertEqual(projection["my_remaining_games"], 1)
        self.assertEqual(projection["opp_remaining_games"], 0)

    def test_returns_completed_projection_without_future_game_requirements(self):
        projection = sandlot_matchup.compute_projection({
            "matchup": {
                "my_score": 12,
                "opponent_score": 20,
                "complete": True,
            },
        })

        self.assertEqual(projection["model_version"], sandlot_matchup.MODEL_VERSION)
        self.assertEqual(projection["projected_my"], 12.0)
        self.assertEqual(projection["projected_opp"], 20.0)
        self.assertEqual(projection["my_remaining_games"], 0)
        self.assertEqual(projection["opp_remaining_games"], 0)
        self.assertEqual(projection["win_probability"], 0.0)
        self.assertEqual(projection["drivers"]["current_margin"], -8.0)
        self.assertEqual(projection["drivers"]["projected_margin"], -8.0)
        self.assertEqual(projection["drivers"]["rest_of_period_delta"], 0.0)
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
        self.assertEqual(payload["matchup"]["projection"]["model_version"], sandlot_matchup.MODEL_VERSION)

    def test_projection_log_payload_matches_projection_output(self):
        snapshot = {
            "league_id": "league",
            "team_id": "me",
            "matchup": {
                "my_score": 1,
                "opponent_score": 1,
                "opponent_team_id": "opp",
                "period_number": 4,
                "end": "2026-05-20",
            },
            "roster": {
                "rows": [
                    {
                        "id": "mine-1",
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
                            "slot": "SS",
                            "fppg": 1.0,
                            "future_games": [future_game(14)],
                        },
                    ],
                },
            },
        }

        record = sandlot_matchup.projection_log_payload(123, snapshot)

        self.assertEqual(record["snapshot_id"], 123)
        self.assertEqual(record["model_version"], sandlot_matchup.MODEL_VERSION)
        self.assertEqual(record["matchup_key"], "league:4:me:opp")
        self.assertEqual(record["period_id"], "4")
        self.assertEqual(record["my_team_id"], "me")
        self.assertEqual(record["opponent_team_id"], "opp")
        self.assertEqual(record["predicted_my"], 3.0)
        self.assertEqual(record["predicted_opp"], 2.0)
        self.assertEqual(record["predicted_margin"], 1.0)
        self.assertGreater(record["win_probability"], 0.5)


if __name__ == "__main__":
    unittest.main()
