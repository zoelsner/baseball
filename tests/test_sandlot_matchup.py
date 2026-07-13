import unittest

import sandlot_data_quality
import sandlot_matchup
from sandlot_api import _snapshot_payload


def future_game(day, **extra):
    return {"date": f"2026-05-{day:02d}", **extra}


class MatchupProjectionTests(unittest.TestCase):
    def test_pitchers_without_posted_probables_produce_labeled_lower_bound(self):
        snapshot = {
            "matchup": {
                "my_score": 10,
                "opponent_score": 12,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {"rows": [
                {
                    "id": "mine-hitter", "slot": "2B", "positions": "2B", "fppg": 2.0,
                    "future_games": [future_game(14)], "future_games_source": "mlb_schedule", "future_games_status": "ok",
                },
                {
                    "id": "mine-sp", "slot": "SP", "positions": "SP", "fppg": 10.0,
                    "future_games": [], "team_future_games": [future_game(14)],
                    "future_games_source": "mlb_schedule", "future_games_status": "pitcher_probables_unavailable",
                    "future_games_scope": "pitcher_probable_starts",
                },
            ]},
            "all_team_rosters": {"opp": {"rows": [{
                "id": "opp-hitter", "slot": "SS", "positions": "SS", "fppg": 1.0,
                "future_games": [future_game(14)], "future_games_source": "mlb_schedule", "future_games_status": "ok",
            }]}},
        }
        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        projection = sandlot_matchup.compute_projection(snapshot, quality)

        self.assertTrue(quality["projection_ready"])
        self.assertEqual(projection["projected_my"], 12.0)
        self.assertEqual(projection["projected_opp"], 13.0)
        self.assertEqual(projection["opportunity_completeness"], "known_opportunities_lower_bound")
        self.assertEqual(projection["pitchers_without_probable_start"], 1)
        self.assertEqual(projection["drivers"]["pitchers_without_probable_start"], 1)

    def test_verified_cadence_changes_projection_but_never_authorizes_pitcher_action(self):
        cadence = {
            "version": "verified_gs_cadence_v1",
            "state": "estimated",
            "expected_starts": 2.0,
            "period_window": {"start": "2026-05-14", "end": "2026-05-20"},
            "action_eligible": False,
            "probability_release_eligible": False,
        }
        snapshot = {
            "matchup": {
                "my_score": 0,
                "opponent_score": 0,
                "opponent_team_id": "opp",
                "start": "2026-05-14",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {"rows": [
                {
                    "id": "active-sp", "name": "Active SP", "slot": "SP", "positions": "SP",
                    "slot_source": "raw.posId", "fppg": 10.0, "future_games": [],
                    "future_games_source": "mlb_schedule",
                    "future_games_status": "pitcher_probables_unavailable",
                    "future_games_scope": "pitcher_probable_starts",
                    "pitcher_opportunity_estimate": cadence,
                },
                {
                    "id": "bench-sp", "name": "Bench SP", "slot": "RES", "positions": "SP",
                    "slot_source": "raw.statusId", "fppg": 20.0, "future_games": [],
                    "future_games_source": "mlb_schedule",
                    "future_games_status": "pitcher_probables_unavailable",
                    "future_games_scope": "pitcher_probable_starts",
                    "pitcher_opportunity_estimate": {**cadence, "expected_starts": 3.0},
                },
            ]},
            "all_team_rosters": {"opp": {"rows": [{
                "id": "opp-hitter", "slot": "SS", "positions": "SS", "slot_source": "raw.posId",
                "fppg": 1.0, "future_games": [future_game(14)],
                "future_games_source": "mlb_schedule", "future_games_status": "ok",
            }]}},
        }
        quality = {
            "projection_ready": True,
            "recommendations_ready": True,
            "lineup_recommendations_ready": True,
        }

        projection = sandlot_matchup.compute_projection(snapshot, quality)
        simulation = sandlot_matchup.simulate_lineup_move_impact(snapshot, quality)

        self.assertEqual(projection["projected_my"], 20.0)
        self.assertEqual(projection["my_remaining_games"], 2.0)
        self.assertEqual(projection["opportunity_completeness"], "estimated_pitcher_opportunities")
        self.assertEqual(projection["pitchers_without_probable_start"], 1)
        self.assertEqual(projection["pitchers_with_cadence_estimate"], 1)
        self.assertEqual(projection["pitchers_without_opportunity_model"], 0)
        self.assertEqual(
            projection["pitchers_using_posted_probable_only"]
            + projection["pitchers_with_cadence_estimate"]
            + projection["pitchers_without_opportunity_model"],
            projection["active_pitchers"],
        )
        self.assertEqual(simulation["actions"], [])
        self.assertIn("future-game provenance is not trusted", simulation["no_action"]["reason"])

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

    def test_explicit_position_fallback_is_not_counted_as_an_active_player(self):
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
                        "id": "trusted-starter",
                        "slot": "2B",
                        "slot_source": "raw.lineupSlot",
                        "fppg": 2.0,
                        "future_games": [future_game(14)],
                    },
                    {
                        "id": "actual-bench-with-position-fallback",
                        "slot": "OF",
                        "slot_source": "position_fallback",
                        "fppg": 50.0,
                        "future_games": [future_game(14)],
                    },
                ],
            },
            "all_team_rosters": {"opp": {"rows": []}},
        }

        projection = sandlot_matchup.compute_projection(snapshot)

        self.assertEqual(projection["projected_my"], 2.0)
        self.assertEqual(projection["my_remaining_games"], 1)

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

    def test_excludes_games_before_matchup_window_start(self):
        snapshot = {
            "matchup": {
                "my_score": 0,
                "opponent_score": 0,
                "opponent_team_id": "opp",
                "start": "2026-05-15",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {
                "rows": [
                    {
                        "id": "mine-hitter",
                        "slot": "OF",
                        "positions": "OF",
                        "fppg": 2.0,
                        "future_games": [future_game(14), future_game(15), future_game(16)],
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
                            "future_games": [future_game(14), future_game(16)],
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

    def test_in_progress_zero_rates_keep_nonzero_uncertainty(self):
        snapshot = {
            "matchup": {
                "my_score": 1,
                "opponent_score": 0,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
                "complete": False,
            },
            "roster": {
                "rows": [
                    {
                        "id": "mine-1",
                        "slot": "2B",
                        "positions": "2B",
                        "fppg": 0.0,
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
                            "positions": "SS",
                            "fppg": 0.0,
                            "future_games": [future_game(14)],
                        },
                    ],
                },
            },
        }

        projection = sandlot_matchup.compute_projection(snapshot)

        self.assertGreater(projection["win_probability"], 0.5)
        self.assertLess(projection["win_probability"], 1.0)
        self.assertEqual(projection["scoring_basis"], "current_snapshot_fppg_x_remaining_opportunities")
        self.assertFalse(projection["probability_calibrated"])

    def test_nonfinite_scores_or_rates_do_not_emit_projection(self):
        snapshot = {
            "matchup": {
                "my_score": float("nan"),
                "opponent_score": 0,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
            },
            "roster": {
                "rows": [{"id": "mine", "slot": "2B", "fppg": 1.0, "future_games": [future_game(14)]}],
            },
            "all_team_rosters": {
                "opp": {
                    "rows": [{"id": "opp", "slot": "SS", "fppg": 1.0, "future_games": [future_game(14)]}],
                },
            },
        }

        self.assertIsNone(sandlot_matchup.compute_projection(snapshot))

        snapshot["matchup"]["my_score"] = 1
        snapshot["roster"]["rows"][0]["fppg"] = float("inf")
        self.assertIsNone(sandlot_matchup.compute_projection(snapshot))

        snapshot["roster"]["rows"][0]["fppg"] = 688
        self.assertIsNone(sandlot_matchup.compute_projection(snapshot))

    def test_suspended_active_row_does_not_block_or_score_in_projection(self):
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
                        "id": "suspended",
                        "slot": "OF",
                        "slot_source": "raw.lineupSlot",
                        "injury": "SUSP",
                        "fppg": None,
                        "future_games": [future_game(14)],
                    },
                    {
                        "id": "available",
                        "slot": "2B",
                        "slot_source": "raw.lineupSlot",
                        "fppg": 2.0,
                        "future_games": [future_game(14)],
                    },
                ],
            },
            "all_team_rosters": {
                "opp": {
                    "rows": [{
                        "id": "opp",
                        "slot": "SS",
                        "slot_source": "raw.lineupSlot",
                        "fppg": 1.0,
                        "future_games": [future_game(14)],
                    }],
                },
            },
        }

        projection = sandlot_matchup.compute_projection(snapshot)

        self.assertEqual(projection["projected_my"], 2.0)
        self.assertEqual(projection["my_remaining_games"], 1)

    def test_raw_suspended_flag_is_unavailable(self):
        self.assertTrue(sandlot_matchup._is_unavailable({
            "raw": {"player": {"suspended": True}},
        }))
        self.assertFalse(sandlot_matchup._is_unavailable({
            "raw": {"player": {"suspended": "false"}},
        }))

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
