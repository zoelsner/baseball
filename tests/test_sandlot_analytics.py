import math
import unittest
from unittest.mock import patch

import mlb_stats
import sandlot_autopsy
import sandlot_lineup
import sandlot_scoring
from scripts import run_autopsy


def pitching_split(innings="6.2", **overrides):
    stat = {
        "inningsPitched": innings,
        "hits": 5,
        "earnedRuns": 2,
        "baseOnBalls": 1,
        "strikeOuts": 7,
        "wins": 1,
        "losses": 0,
        "saves": 0,
        "holds": 0,
        "gamesStarted": 1,
    }
    stat.update(overrides)
    return {
        "date": "2026-07-10",
        "stat": stat,
        "opponent": {"id": 147, "abbreviation": "NYY"},
        "game": {"gamePk": 1},
    }


class LeagueScoringTests(unittest.TestCase):
    def test_weights_match_live_fantrax_league_rules(self):
        self.assertEqual(sandlot_scoring.HITTING, {
            "single": 1.0,
            "double": 2.0,
            "triple": 3.0,
            "hr": 4.0,
            "run": 1.0,
            "rbi": 1.0,
            "bb": 1.0,
            "hbp": 1.0,
            "sb": 1.0,
            "cs": -0.5,
            "so": -0.5,
        })
        self.assertEqual(sandlot_scoring.PITCHING, {
            "ip": 3.0,
            "k": 1.0,
            "er": -2.0,
            "hit": -1.0,
            "bb": -1.0,
            "win": 2.0,
            "loss": -2.0,
            "qs": 3.0,
            "save": 4.0,
            "hold": 3.5,
        })

    def test_mlb_baseball_innings_notation_is_converted_to_true_innings(self):
        game = mlb_stats._normalize_split(pitching_split("6.2"), "pitching")

        self.assertAlmostEqual(game["ip"], 6 + 2 / 3)
        self.assertEqual(game["ip_display"], "6.2")
        self.assertTrue(game["line"].startswith("6.2 IP"))
        self.assertTrue(game["qs"])
        self.assertEqual(sandlot_scoring.pitching_points(game), 22.0)

    def test_one_recorded_out_scores_as_one_full_inning_point(self):
        game = mlb_stats._normalize_split(
            pitching_split(
                "0.1",
                hits=0,
                earnedRuns=0,
                baseOnBalls=0,
                strikeOuts=1,
                wins=0,
                gamesStarted=0,
            ),
            "pitching",
        )

        self.assertAlmostEqual(game["ip"], 1 / 3)
        self.assertEqual(sandlot_scoring.pitching_points(game), 2.0)

    def test_hitting_formula_counts_total_bases_without_double_counting_hits(self):
        points = sandlot_scoring.hitting_points({
            "h": 3,
            "doubles": 1,
            "triples": 0,
            "hr": 1,
            "r": 2,
            "rbi": 3,
            "bb": 1,
            "hbp": 1,
            "sb": 1,
            "cs": 1,
            "k": 2,
        })

        self.assertEqual(points, 13.5)


class AnalyticsDynastySafetyTests(unittest.TestCase):
    def test_minors_asset_is_not_treated_as_a_free_hindsight_promotion(self):
        rows = [
            {"id": "active", "name": "Active OF", "slot": "OF", "positions": "OF"},
            {"id": "bench", "name": "Bench OF", "slot": "RES", "positions": "OF"},
            {"id": "prospect", "name": "Protected Prospect", "slot": "MIN", "positions": "OF"},
        ]
        points = {"active": 1.0, "bench": 3.0, "prospect": 20.0}

        result = sandlot_autopsy.team_day(rows, points)
        coverage = sandlot_autopsy.coverage(rows, points, set(points))

        self.assertEqual(result["actual"], 1.0)
        self.assertEqual(result["optimal"], 3.0)
        self.assertEqual(result["points_left"], 2.0)
        self.assertEqual(result["assignment"], [("OF", "Bench OF")])
        self.assertEqual(coverage["n_players"], 2)

    def test_optimizer_core_excludes_minors_injuries_and_nonfinite_values(self):
        result = sandlot_lineup.propose([
            {"name": "Protected Prospect", "tokens": {"OF"}, "proj": 100, "slot": "MIN"},
            {"name": "Injured Star", "tokens": {"OF"}, "proj": 90, "slot": "RES", "injury": "IL10"},
            {"name": "Bad Value", "tokens": {"OF"}, "proj": math.nan, "slot": "RES"},
            {"name": "Healthy OF", "tokens": {"OF"}, "proj": 5, "slot": "RES"},
        ], template=["OF"])

        self.assertEqual(result["lineup"], [("OF", "Healthy OF")])
        self.assertEqual(result["projected_total"], 5.0)


class AnalyticsProvenanceTests(unittest.TestCase):
    def test_game_log_failure_is_not_counted_as_trustworthy_zero(self):
        players = {
            "ok": {"name": "Known Zero", "tokens": {"OF"}},
            "bad": {"name": "Fetch Failed", "tokens": {"OF"}},
        }

        def fetch_game_log(mlb_id, *, season, group):
            if mlb_id == 2:
                raise RuntimeError("upstream failed")
            return []

        with patch.object(run_autopsy.mlb_stats, "fetch_game_log", side_effect=fetch_game_log):
            points, successful, failures = run_autopsy.fetch_daily_points(
                players,
                {"ok": 1, "bad": 2},
                2026,
            )

        self.assertEqual(points, {"ok": {}, "bad": {}})
        self.assertEqual(successful, {"ok"})
        self.assertIn("bad", failures)
        coverage = sandlot_autopsy.coverage(
            [
                {"id": "ok", "slot": "OF", "positions": "OF"},
                {"id": "bad", "slot": "RES", "positions": "OF"},
            ],
            {"ok": 0.0},
            {"ok", "bad"},
        )
        self.assertEqual(coverage["points_coverage"], 0.5)
        self.assertFalse(run_autopsy.coverage_is_trusted(coverage))

    def test_coverage_threshold_requires_both_ids_and_game_logs(self):
        self.assertTrue(run_autopsy.coverage_is_trusted({
            "points_coverage": 0.9,
            "id_coverage": 1.0,
        }))
        self.assertFalse(run_autopsy.coverage_is_trusted({
            "points_coverage": 1.0,
            "id_coverage": 0.89,
        }))


class LineupAssignmentTests(unittest.TestCase):
    def test_two_way_projection_keeps_hitting_and_pitching_opportunities_separate(self):
        result = sandlot_lineup.project_week(
            {"OF", "SP"},
            hitting_season_points=[5.0] * 7,
            hitting_recent_points=[5.0] * 7,
            pitching_season_points=[20.0],
            pitching_recent_points=[20.0],
            team_games_next=7,
            team_games_recent=7,
            starts_recent=1,
            probable_starts=1,
        )

        self.assertEqual(result["projected_total"], 55.0)
        self.assertEqual([component["group"] for component in result["components"]], ["hitting", "pitching"])
        self.assertEqual(result["components"][0]["points"], 35.0)
        self.assertEqual(result["components"][1]["points"], 20.0)

    def test_specific_pitcher_slots_do_not_accept_wrong_role(self):
        result = sandlot_lineup.propose([
            {"name": "Closer", "tokens": {"RP"}, "proj": 100, "slot": "RES"},
            {"name": "Starter", "tokens": {"SP"}, "proj": 10, "slot": "RES"},
        ], template=["SP"])

        self.assertEqual(result["lineup"], [("SP", "Starter")])
        self.assertEqual(result["projected_total"], 10.0)

    def test_two_way_player_is_never_assigned_to_both_sides(self):
        result = sandlot_lineup.propose([
            {"name": "Two Way", "tokens": {"OF", "SP"}, "proj": 10, "slot": "RES"},
            {"name": "Hitter", "tokens": {"OF"}, "proj": 9, "slot": "RES"},
            {"name": "Pitcher", "tokens": {"SP"}, "proj": 8, "slot": "RES"},
        ], template=["OF", "SP"])

        names = [name for _slot, name in result["lineup"]]
        self.assertEqual(names.count("Two Way"), 1)
        self.assertEqual(len(names), 2)
        self.assertEqual(result["projected_total"], 19.0)

    def test_two_way_value_depends_on_fantrax_scoring_group_of_assigned_slot(self):
        two_way = {
            "name": "Two Way",
            "tokens": {"OF", "SP"},
            "proj": 30,
            "hitter_proj": 5,
            "pitcher_proj": 30,
            "slot": "RES",
        }
        result = sandlot_lineup.propose([
            two_way,
            {"name": "Hitter", "tokens": {"OF"}, "proj": 9, "slot": "RES"},
            {"name": "Pitcher", "tokens": {"SP"}, "proj": 20, "slot": "RES"},
        ], template=["OF", "SP"])

        self.assertEqual(set(result["lineup"]), {("OF", "Hitter"), ("SP", "Two Way")})
        self.assertEqual(result["projected_total"], 39.0)
        self.assertEqual(sandlot_lineup.projected_for_slot(two_way, "OF"), 5.0)
        self.assertEqual(sandlot_lineup.projected_for_slot(two_way, "SP"), 30.0)

    def test_expected_game_models_are_bounded_by_role_inputs(self):
        hitter = sandlot_lineup.expected_games(
            {"OF"},
            team_games_next=7,
            team_games_recent=30,
            games_recent=30,
            starts_recent=0,
            probable_starts=0,
        )
        starter = sandlot_lineup.expected_games(
            {"SP"},
            team_games_next=7,
            team_games_recent=30,
            games_recent=6,
            starts_recent=6,
            probable_starts=2,
        )
        reliever = sandlot_lineup.expected_games(
            {"RP"},
            team_games_next=7,
            team_games_recent=30,
            games_recent=10,
            starts_recent=0,
            probable_starts=0,
        )

        self.assertEqual(hitter, 7.0)
        self.assertEqual(starter, 2.0)
        self.assertAlmostEqual(reliever, 10 / 30 * 7)
        self.assertEqual(len(sandlot_lineup.FULL_ACTIVE_TEMPLATE), 20)


if __name__ == "__main__":
    unittest.main()
