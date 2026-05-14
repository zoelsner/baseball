import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import sandlot_db
import sandlot_matchup
import sandlot_refresh
from sandlot_api import _log_skipper_projection_surfaces, _snapshot_payload


def future_game(day=14):
    return {"date": f"2026-05-{day:02d}"}


def projection_ready_snapshot():
    return {
        "league_id": "league",
        "team_id": "me",
        "team_name": "My Team",
        "matchup": {
            "my_score": 10,
            "opponent_score": 8,
            "opponent_team_id": "opp",
            "period_number": 4,
            "end": "2026-05-20",
        },
        "roster": {
            "rows": [
                {
                    "id": "mine-1",
                    "slot": "2B",
                    "positions": "2B",
                    "fppg": 2.0,
                    "future_games": [future_game()],
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
                        "fppg": 1.0,
                        "future_games": [future_game()],
                    },
                ],
            },
        },
    }


class ProjectionLoggingTests(unittest.TestCase):
    def test_upsert_projection_log_uses_idempotent_conflict_key(self):
        calls = []

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            sandlot_db.upsert_projection_log(
                snapshot_id=123,
                model_version=sandlot_matchup.MODEL_VERSION,
                matchup_key="league:4:me:opp",
                period_id="4",
                my_team_id="me",
                opponent_team_id="opp",
                predicted_my=12.0,
                predicted_opp=9.0,
                predicted_margin=3.0,
                win_probability=0.75,
                data_quality={"projection_ready": True},
            )

        sql, params = calls[0]
        self.assertIn("ON CONFLICT (model_version, matchup_key, surface, shown_date) DO UPDATE", sql)
        self.assertEqual(params[0], 123)
        self.assertEqual(params[1], sandlot_matchup.MODEL_VERSION)
        self.assertEqual(params[2], "api")
        self.assertEqual(params[4], "league:4:me:opp")
        self.assertEqual(params[-1].obj, {})

    def test_successful_refresh_persists_projection_log(self):
        snapshot = projection_ready_snapshot()
        upsert = Mock()

        with patch.dict(
            sandlot_refresh.os.environ,
            {"FANTRAX_LEAGUE_ID": "league", "FANTRAX_TEAM_ID": "me"},
            clear=False,
        ), patch.object(sandlot_refresh.sandlot_db, "init_schema"), patch.object(
            sandlot_refresh.sandlot_db, "create_refresh_run", return_value=7
        ), patch.object(
            sandlot_refresh, "_session_from_available_cookies", return_value=(object(), None, "test")
        ), patch.object(
            sandlot_refresh.fantrax_data, "collect_all", return_value=snapshot
        ), patch.object(
            sandlot_refresh.sandlot_db, "insert_snapshot", return_value=123
        ), patch.object(
            sandlot_refresh.sandlot_db, "finish_refresh_run"
        ), patch.object(
            sandlot_refresh.sandlot_db, "prune_successful_snapshots"
        ), patch.object(
            sandlot_refresh.sandlot_db, "upsert_projection_log", upsert
        ):
            result = sandlot_refresh.run_refresh(source="manual")

        self.assertTrue(result.ok)
        upsert.assert_called_once()
        record = upsert.call_args.kwargs
        self.assertEqual(record["snapshot_id"], 123)
        self.assertEqual(record["model_version"], sandlot_matchup.MODEL_VERSION)
        self.assertEqual(record["surface"], "api")
        self.assertEqual(record["matchup_key"], "league:4:me:opp")
        self.assertEqual(record["predicted_margin"], 3.0)
        self.assertIn("drivers", record)

    def test_completed_refresh_fills_actuals_for_prior_logs(self):
        snapshot = projection_ready_snapshot()
        snapshot["matchup"]["complete"] = True
        update_actuals = Mock()

        with patch.dict(
            sandlot_refresh.os.environ,
            {"FANTRAX_LEAGUE_ID": "league", "FANTRAX_TEAM_ID": "me"},
            clear=False,
        ), patch.object(sandlot_refresh.sandlot_db, "init_schema"), patch.object(
            sandlot_refresh.sandlot_db, "create_refresh_run", return_value=7
        ), patch.object(
            sandlot_refresh, "_session_from_available_cookies", return_value=(object(), None, "test")
        ), patch.object(
            sandlot_refresh.fantrax_data, "collect_all", return_value=snapshot
        ), patch.object(
            sandlot_refresh.sandlot_db, "insert_snapshot", return_value=123
        ), patch.object(
            sandlot_refresh.sandlot_db, "finish_refresh_run"
        ), patch.object(
            sandlot_refresh.sandlot_db, "prune_successful_snapshots"
        ), patch.object(
            sandlot_refresh.sandlot_db, "upsert_projection_log"
        ), patch.object(
            sandlot_refresh.sandlot_db, "update_projection_actuals", update_actuals
        ):
            result = sandlot_refresh.run_refresh(source="manual")

        self.assertTrue(result.ok)
        update_actuals.assert_called_once()
        actual = update_actuals.call_args.kwargs
        self.assertEqual(actual["matchup_key"], "league:4:me:opp")
        self.assertEqual(actual["actual_my"], 10.0)
        self.assertEqual(actual["actual_opp"], 8.0)
        self.assertEqual(actual["actual_winner"], "me")

    def test_skipper_projection_logging_tags_user_visible_surfaces(self):
        snapshot = projection_ready_snapshot()
        row = {"id": 123, "data": snapshot}
        payload = _snapshot_payload(row)
        upsert = Mock()

        with patch.object(sandlot_db, "upsert_projection_log", upsert):
            _log_skipper_projection_surfaces(row, "deep matchup analysis", payload)

        surfaces = sorted(call.kwargs["surface"] for call in upsert.call_args_list)
        self.assertEqual(surfaces, ["skipper_card", "skipper_chat"])
        for call in upsert.call_args_list:
            self.assertEqual(call.kwargs["snapshot_id"], 123)
            self.assertEqual(call.kwargs["model_version"], sandlot_matchup.MODEL_VERSION)

    def test_update_projection_actuals_matches_existing_log_rows(self):
        calls = []

        class FakeResult:
            def fetchall(self):
                return [{"id": 1}, {"id": 2}]

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                return FakeResult()

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            count = sandlot_db.update_projection_actuals(
                matchup_key="league:4:me:opp",
                period_id="4",
                actual_my=12.0,
                actual_opp=10.0,
                actual_winner="me",
            )

        self.assertEqual(count, 2)
        sql, params = calls[0]
        self.assertIn("UPDATE projection_logs", sql)
        self.assertEqual(params[3], "league:4:me:opp")


if __name__ == "__main__":
    unittest.main()
