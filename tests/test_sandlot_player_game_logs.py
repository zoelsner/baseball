import unittest
from contextlib import contextmanager
from unittest.mock import patch

import player_service
import sandlot_db


class PlayerGameLogTests(unittest.TestCase):
    def test_two_way_stat_group_follows_assigned_side(self):
        two_way = {"positions": "UT,SP", "all_positions": ["UT", "SP"]}

        self.assertEqual(player_service._stat_group({**two_way, "slot": "UT"}), "hitting")
        self.assertEqual(player_service._stat_group({**two_way, "slot": "OF"}), "hitting")
        self.assertEqual(player_service._stat_group({**two_way, "slot": "SP"}), "pitching")
        self.assertEqual(player_service._stat_group({**two_way, "slot": "RES"}), "hitting")

    def test_game_log_schema_uses_group_and_season_cache_key(self):
        calls = []

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            sandlot_db.init_schema()

        schema_sql = "\n".join(sql for sql, _params in calls)
        self.assertIn("PRIMARY KEY (mlb_id, group_type, season)", schema_sql)
        self.assertIn("ALTER TABLE player_game_logs DROP CONSTRAINT IF EXISTS player_game_logs_pkey", schema_sql)

    def test_game_log_read_is_scoped_to_group_and_season(self):
        calls = []

        class FakeResult:
            def fetchone(self):
                return {
                    "mlb_id": 660271,
                    "group_type": "hitting",
                    "season": 2026,
                    "games": [],
                }

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                return FakeResult()

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            row = sandlot_db.get_player_game_log(660271, group_type="hitting", season=2026)

        self.assertEqual(row["group_type"], "hitting")
        self.assertEqual(calls[0][1], (660271, "hitting", 2026))
        self.assertIn("AND group_type = %s", calls[0][0])
        self.assertIn("AND season = %s", calls[0][0])

    def test_game_log_write_does_not_overwrite_other_group_or_season(self):
        calls = []

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            sandlot_db.set_player_game_log(
                660271,
                group_type="pitching",
                season=2026,
                games=[{"date": "2026-07-10"}],
            )

        self.assertIn("ON CONFLICT (mlb_id, group_type, season) DO UPDATE", calls[0][0])
        self.assertEqual(calls[0][1][:3], (660271, "pitching", 2026))


if __name__ == "__main__":
    unittest.main()
