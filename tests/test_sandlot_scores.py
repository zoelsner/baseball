import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import sandlot_config
import sandlot_scores


def _hitting_game(**overrides):
    game = {
        "date": "2026-06-01", "opponent": "NYY", "home": True, "game_pk": 777001,
        "ab": 4, "h": 2, "hr": 1, "rbi": 3, "bb": 1, "k": 1, "sb": 0,
        "r": 2, "doubles": 1, "triples": 0, "hbp": 0, "cs": 0,
        "avg_game": 0.5, "line": "2-4, HR", "fpts_estimated": 10.0,
    }
    game.update(overrides)
    return game


def _pitching_game(**overrides):
    game = {
        "date": "2026-06-02", "opponent": "BOS", "home": False, "game_pk": 777002,
        "ip": 7.0, "h": 5, "er": 2, "bb": 1, "k": 8,
        "win": True, "save": False, "loss": False, "hold": False,
        "gs": True, "qs": True,
        "avg_game": None, "line": "7 IP, 2 ER", "fpts_estimated": 20.0,
    }
    game.update(overrides)
    return game


class StatGroupsTests(unittest.TestCase):
    def test_hitter_only(self):
        self.assertEqual(sandlot_scores.stat_groups({"SS", "2B"}), ["hitting"])

    def test_pitcher_only(self):
        self.assertEqual(sandlot_scores.stat_groups({"SP"}), ["pitching"])

    def test_two_way_gets_both(self):
        self.assertEqual(sandlot_scores.stat_groups({"UT", "SP"}), ["hitting", "pitching"])

    def test_empty_defaults_to_hitting(self):
        self.assertEqual(sandlot_scores.stat_groups(set()), ["hitting"])


class SnapshotPlayersTests(unittest.TestCase):
    def test_covers_all_rosters_and_dedupes(self):
        data = {
            "roster": {"rows": [
                {"id": "p1", "name": "Mine Guy", "team": "SEA", "positions": "SS"},
            ]},
            "all_team_rosters": {
                "t1": {"rows": [
                    {"id": "p1", "name": "Mine Guy", "team": "SEA", "positions": "SS"},
                    {"id": "p2", "name": "Rival Arm", "team": "NYY", "positions": "SP"},
                ]},
                "t2": {"rows": [
                    {"id": "p3", "name": "Other Bat", "team": "BOS", "positions": "OF"},
                    {"id": None, "name": "No Id"},
                ]},
            },
        }
        players = sandlot_scores.snapshot_players(data)
        self.assertEqual(set(players), {"p1", "p2", "p3"})
        self.assertEqual(players["p2"]["tokens"], {"SP"})
        self.assertEqual(players["p3"]["name"], "Other Bat")

    def test_empty_snapshot(self):
        self.assertEqual(sandlot_scores.snapshot_players({}), {})


class ScoreRowsTests(unittest.TestCase):
    def test_hitter_rows_use_league_scoring(self):
        with patch.object(sandlot_scores.mlb_stats, "fetch_game_log",
                          return_value=[_hitting_game()]) as fetch:
            rows = sandlot_scores.score_rows(660271, {"OF"}, 2026)
        fetch.assert_called_once_with(660271, season=2026, group="hitting")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # 1B(0) + 2B(1x2) + HR(1x4) + RBI(3) + R(2) + BB(1) - K(0.5) = 11.5
        self.assertEqual(row["pts"], 11.5)
        self.assertEqual(row["game_pk"], 777001)
        self.assertEqual(row["stat_group"], "hitting")
        self.assertFalse(row["gs"])
        # Derived display fields stay out of the stored stats blob.
        for stripped in ("line", "fpts_estimated", "avg_game"):
            self.assertNotIn(stripped, row["stats"])
        self.assertEqual(row["stats"]["hr"], 1)

    def test_two_way_player_gets_rows_from_both_groups(self):
        def fake_log(mlb_id, season, group):
            return [_hitting_game()] if group == "hitting" else [_pitching_game()]

        with patch.object(sandlot_scores.mlb_stats, "fetch_game_log", side_effect=fake_log):
            rows = sandlot_scores.score_rows(660271, {"UT", "SP"}, 2026)
        self.assertEqual([r["stat_group"] for r in rows], ["hitting", "pitching"])
        pitching = rows[1]
        # 3*IP(21) + K(8) - 2*ER(4) - H(5) - BB(1) + W(2) + QS(3) = 24
        self.assertEqual(pitching["pts"], 24.0)
        self.assertTrue(pitching["gs"])

    def test_missing_game_pk_and_date(self):
        games = [_hitting_game(game_pk=None), _hitting_game(date=None)]
        with patch.object(sandlot_scores.mlb_stats, "fetch_game_log", return_value=games):
            rows = sandlot_scores.score_rows(1, {"OF"}, 2026)
        self.assertEqual(len(rows), 1)  # dateless game dropped
        self.assertEqual(rows[0]["game_pk"], 0)


class ResolveMlbIdTests(unittest.TestCase):
    def test_positive_cache_short_circuits(self):
        with patch.object(sandlot_scores.sandlot_db, "get_mlb_id",
                          return_value={"mlb_id": 545361, "resolved_at": None}), \
             patch.object(sandlot_scores.mlb_stats, "lookup_player_by_name") as lookup:
            self.assertEqual(sandlot_scores.resolve_mlb_id("f1", "Mike Trout", "LAA", 2026), 545361)
        lookup.assert_not_called()

    def test_fresh_negative_cache_is_honored(self):
        fresh = datetime.now(timezone.utc) - timedelta(days=1)
        with patch.object(sandlot_scores.sandlot_db, "get_mlb_id",
                          return_value={"mlb_id": None, "resolved_at": fresh}), \
             patch.object(sandlot_scores.mlb_stats, "lookup_player_by_name") as lookup:
            self.assertIsNone(sandlot_scores.resolve_mlb_id("f1", "Nobody", None, 2026))
        lookup.assert_not_called()

    def test_stale_negative_cache_retries_and_writes_back(self):
        stale = datetime.now(timezone.utc) - timedelta(days=30)
        with patch.object(sandlot_scores.sandlot_db, "get_mlb_id",
                          return_value={"mlb_id": None, "resolved_at": stale}), \
             patch.object(sandlot_scores.mlb_stats, "lookup_player_by_name",
                          return_value=683002), \
             patch.object(sandlot_scores.sandlot_db, "set_mlb_id") as set_id:
            self.assertEqual(sandlot_scores.resolve_mlb_id("f1", "Call Up", "TB", 2026), 683002)
        set_id.assert_called_once_with("f1", 683002)


class ConfigFlagTests(unittest.TestCase):
    def test_sync_defaults_on(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("SANDLOT_")}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(sandlot_config.game_scores_sync_enabled())

    def test_kill_switch(self):
        with patch.dict(os.environ, {"SANDLOT_GAME_SCORES_SYNC_DISABLED": "1"}, clear=False):
            self.assertFalse(sandlot_config.game_scores_sync_enabled())


if __name__ == "__main__":
    unittest.main()
