import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import fastapi
import sandlot_api
import sandlot_db
import sandlot_matchup
import sandlot_refresh
from sandlot_api import _log_skipper_projection_surfaces, _snapshot_payload


def future_game(day=14):
    return {"date": f"2026-05-{day:02d}"}


@contextmanager
def fake_refresh_lock(locked=True):
    yield locked


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
    def test_outcome_batch_continues_after_one_poison_receipt(self):
        valid_outcome = {"scoring_version": "team_result_v1"}
        score = Mock()
        with patch.dict(sandlot_refresh.os.environ, {"DATABASE_URL": "postgres://test"}), patch.object(
            sandlot_refresh.sandlot_db,
            "pending_recommendation_receipts",
            return_value=[{"receipt_id": "bad"}, {"receipt_id": "good"}],
        ), patch.object(
            sandlot_refresh.sandlot_receipts,
            "build_team_result_outcome",
            side_effect=[ValueError("bad receipt"), valid_outcome],
        ), patch.object(
            sandlot_refresh.sandlot_db,
            "score_recommendation_receipt_team_result",
            score,
        ):
            sandlot_refresh._persist_recommendation_outcomes(
                123, {"timestamp": "2026-07-20T12:00:00Z"}
            )

        score.assert_called_once_with(receipt_id="good", outcome=valid_outcome)

    def test_projection_logs_survive_snapshot_pruning(self):
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
        self.assertIn(
            "snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL",
            schema_sql,
        )
        self.assertIn(
            "ALTER TABLE projection_logs ALTER COLUMN snapshot_id DROP NOT NULL",
            schema_sql,
        )
        self.assertIn(
            "FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE SET NULL",
            schema_sql,
        )

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
            sandlot_refresh, "_refresh_lock", return_value=fake_refresh_lock(True)
        ), patch.object(
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
        self.assertEqual(
            record["drivers"]["forecast_provenance"],
            sandlot_matchup.FORECAST_PROVENANCE_VERSION,
        )

    def test_completed_matchup_is_never_logged_as_a_forecast(self):
        snapshot = projection_ready_snapshot()
        snapshot["matchup"]["complete"] = True

        self.assertIsNone(sandlot_matchup.projection_log_payload(123, snapshot))

    def test_refresh_marks_empty_my_roster_snapshot_failed(self):
        snapshot = projection_ready_snapshot()
        snapshot["roster"] = {"rows": []}
        snapshot["errors"] = ["roster: 'Roster' object has no attribute 'positions'"]
        insert_snapshot = Mock(return_value=123)
        finish_refresh = Mock()
        upsert = Mock()
        prune = Mock()

        with patch.dict(
            sandlot_refresh.os.environ,
            {"FANTRAX_LEAGUE_ID": "league", "FANTRAX_TEAM_ID": "me"},
            clear=False,
        ), patch.object(sandlot_refresh.sandlot_db, "init_schema"), patch.object(
            sandlot_refresh, "_refresh_lock", return_value=fake_refresh_lock(True)
        ), patch.object(
            sandlot_refresh.sandlot_db, "create_refresh_run", return_value=7
        ), patch.object(
            sandlot_refresh, "_session_from_available_cookies", return_value=(object(), None, "test")
        ), patch.object(
            sandlot_refresh.fantrax_data, "collect_all", return_value=snapshot
        ), patch.object(
            sandlot_refresh.sandlot_db, "insert_snapshot", insert_snapshot
        ), patch.object(
            sandlot_refresh.sandlot_db, "finish_refresh_run", finish_refresh
        ), patch.object(
            sandlot_refresh.sandlot_db, "prune_successful_snapshots", prune
        ), patch.object(
            sandlot_refresh.sandlot_db, "upsert_projection_log", upsert
        ):
            result = sandlot_refresh.run_refresh(source="cron")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertIn("roster: 'Roster' object has no attribute 'positions'", result.errors)
        self.assertIn("No my-roster rows in snapshot", result.errors)
        self.assertEqual(result.snapshot["errors"], result.errors)
        insert_snapshot.assert_called_once()
        self.assertEqual(insert_snapshot.call_args.kwargs["status"], "failed")
        self.assertEqual(insert_snapshot.call_args.kwargs["errors"], result.errors)
        finish_refresh.assert_called_once()
        self.assertEqual(finish_refresh.call_args.kwargs["status"], "failed")
        self.assertIn("Roster", finish_refresh.call_args.kwargs["error"])
        upsert.assert_not_called()
        prune.assert_not_called()

    def test_dom_slot_proof_can_enrich_refresh_snapshot_when_enabled(self):
        snapshot = projection_ready_snapshot()
        snapshot["roster"]["rows"] = [
            {"id": "active", "slot": "OF", "slot_source": "position_fallback"},
            {"id": "reserve", "slot": "RES", "slot_source": "raw.statusId"},
            {"id": "conflict", "slot": "2B", "slot_source": "position_fallback"},
        ]
        snapshot["slot_provenance"] = {"raw_status": "kept"}

        with patch.dict(
            sandlot_refresh.os.environ,
            {sandlot_refresh.DOM_SLOT_CAPTURE_ENV: "1"},
            clear=False,
        ), patch.object(
            sandlot_refresh.fantrax_dom, "capture_roster_html", return_value="<html></html>"
        ) as capture, patch.object(
            sandlot_refresh.fantrax_dom,
            "lineup_slots_from_html",
            return_value={
                "active": {"slot": "UT", "slot_source": "dom.lineup-btn", "text": "UT"},
                "reserve": {"slot": "OF", "slot_source": "dom.lineup-btn", "text": "OF"},
                "conflict": {
                    "slot": "SS",
                    "slot_source": "dom.lineup-btn",
                    "conflicts": [{"slot": "UT", "text": "UT"}],
                },
            },
        ):
            updated = sandlot_refresh._maybe_apply_dom_slot_proof(
                snapshot,
                cookies=[{"name": "JSESSIONID", "value": "secret"}],
                league_id="league",
                team_id="me",
            )

        capture.assert_called_once()
        self.assertEqual(updated["roster"]["rows"][0]["slot"], "UT")
        self.assertEqual(updated["roster"]["rows"][0]["slot_source"], "dom.lineup-btn")
        self.assertEqual(updated["roster"]["rows"][1]["slot"], "RES")
        self.assertEqual(updated["roster"]["rows"][1]["slot_source"], "raw.statusId")
        self.assertEqual(updated["roster"]["rows"][2]["slot"], "2B")
        self.assertEqual(updated["slot_provenance"]["dom_slots_found"], 3)
        self.assertEqual(updated["slot_provenance"]["dom_slots_applied"], 1)
        self.assertEqual(updated["slot_provenance"]["dom_slots_conflicted"], 1)
        self.assertEqual(updated["slot_provenance"]["active_rows_before"], 2)
        self.assertEqual(updated["slot_provenance"]["active_trusted_before"], 0)
        self.assertEqual(updated["slot_provenance"]["active_rows_after"], 2)
        self.assertEqual(updated["slot_provenance"]["active_trusted_after"], 1)
        self.assertEqual(updated["slot_provenance"]["active_dom_slots_applied"], 1)
        self.assertEqual(updated["slot_provenance"]["active_untrusted_examples_after"], ["conflict"])
        self.assertEqual(updated["slot_provenance"]["raw_status"], "kept")

    def test_dom_slot_proof_failure_is_non_fatal_and_fail_closed(self):
        snapshot = projection_ready_snapshot()
        snapshot["roster"]["rows"][0]["slot_source"] = "position_fallback"

        with patch.dict(
            sandlot_refresh.os.environ,
            {sandlot_refresh.DOM_SLOT_CAPTURE_ENV: "1"},
            clear=False,
        ), patch.object(
            sandlot_refresh.fantrax_dom, "capture_roster_html", side_effect=RuntimeError("browser unavailable")
        ):
            updated = sandlot_refresh._maybe_apply_dom_slot_proof(
                snapshot,
                cookies=[{"name": "JSESSIONID", "value": "secret"}],
                league_id="league",
                team_id="me",
            )

        self.assertEqual(updated["roster"]["rows"][0]["slot_source"], "position_fallback")
        self.assertEqual(updated.get("errors"), snapshot.get("errors"))
        self.assertEqual(updated["slot_provenance"]["dom_slots_applied"], 0)
        self.assertEqual(updated["slot_provenance"]["active_rows_before"], 1)
        self.assertEqual(updated["slot_provenance"]["active_trusted_after"], 0)
        self.assertIn("browser unavailable", updated["slot_provenance"]["dom_capture_error"])

    def test_completed_refresh_fills_actuals_for_prior_logs(self):
        snapshot = projection_ready_snapshot()
        snapshot["matchup"]["complete"] = True
        update_actuals = Mock()

        with patch.dict(
            sandlot_refresh.os.environ,
            {"FANTRAX_LEAGUE_ID": "league", "FANTRAX_TEAM_ID": "me"},
            clear=False,
        ), patch.object(sandlot_refresh.sandlot_db, "init_schema"), patch.object(
            sandlot_refresh, "_refresh_lock", return_value=fake_refresh_lock(True)
        ), patch.object(
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

    def test_refresh_skips_when_lock_is_held(self):
        with patch.object(sandlot_refresh.sandlot_db, "init_schema"), patch.object(
            sandlot_refresh, "_refresh_lock", return_value=fake_refresh_lock(False)
        ), patch.object(
            sandlot_refresh.sandlot_db, "create_refresh_run", return_value=7
        ), patch.object(
            sandlot_refresh.sandlot_db, "finish_refresh_run"
        ) as finish:
            result = sandlot_refresh.run_refresh(source="manual")

        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.ok)
        self.assertIn("already in progress", result.errors[0])
        finish.assert_called_once()
        self.assertEqual(finish.call_args.kwargs["status"], "skipped")
        self.assertIsNone(finish.call_args.kwargs["snapshot_id"])

    def test_refresh_api_returns_latest_snapshot_when_refresh_is_already_running(self):
        result = sandlot_refresh.RefreshResult(
            status="skipped",
            snapshot_id=None,
            duration_ms=5,
            errors=["Refresh already in progress"],
        )
        row = {
            "id": 123,
            "taken_at": datetime.now(timezone.utc),
            "source": "manual",
            "status": "success",
            "data": projection_ready_snapshot(),
        }

        with patch.object(sandlot_api, "_require_refresh_token"), patch.object(
            sandlot_refresh, "run_refresh", return_value=result
        ), patch.object(
            sandlot_api.sandlot_db, "latest_successful_snapshot", return_value=row
        ):
            payload = sandlot_api.refresh(None, None)

        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["snapshot_id"], 123)
        self.assertEqual(payload["snapshot"]["snapshot_id"], 123)
        self.assertIn("already running", payload["fallback_reason"])

    def test_refresh_api_raises_when_skipped_without_fallback_snapshot(self):
        result = sandlot_refresh.RefreshResult(
            status="skipped",
            snapshot_id=None,
            duration_ms=5,
            errors=["Refresh already in progress"],
        )

        with patch.object(sandlot_api, "_require_refresh_token"), patch.object(
            sandlot_refresh, "run_refresh", return_value=result
        ), patch.object(
            sandlot_api.sandlot_db, "latest_successful_snapshot", return_value=None
        ), self.assertRaises(fastapi.HTTPException) as raised:
            sandlot_api.refresh(None, None)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(raised.exception.detail["fallback"])

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
            def __init__(self, *, one=None, many=None):
                self.one = one
                self.many = many or []

            def fetchone(self):
                return self.one

            def fetchall(self):
                return self.many

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                if "SELECT id" in sql:
                    return FakeResult(many=[{
                        "id": 1, "my_team_id": "me", "opponent_team_id": "opp",
                        "actual_my": None, "actual_opp": None, "actual_winner": None,
                    }, {
                        "id": 2, "my_team_id": "me", "opponent_team_id": "opp",
                        "actual_my": None, "actual_opp": None, "actual_winner": None,
                    }])
                return FakeResult(many=[{"id": 1}, {"id": 2}])

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
        self.assertEqual(len(calls), 2)
        conflict_sql, conflict_params = calls[0]
        self.assertIn("FOR UPDATE", conflict_sql)
        self.assertEqual(conflict_params, ("league:4:me:opp", "4"))
        sql, params = calls[1]
        self.assertIn("UPDATE projection_logs", sql)
        self.assertIn("period_id = %s", sql)
        self.assertNotIn("IS NULL OR", sql)
        self.assertEqual(params[3], "league:4:me:opp")
        self.assertEqual(params[4], "4")
        self.assertEqual(params[5:], (12.0, 10.0, "me"))

    def test_update_projection_actuals_rejects_incomplete_or_contradictory_identity(self):
        with self.assertRaisesRegex(ValueError, "identity is incomplete"):
            sandlot_db.update_projection_actuals(
                matchup_key="league:4:me:opp", period_id="", actual_my=12.0,
                actual_opp=10.0, actual_winner="me",
            )

        class FakeResult:
            def fetchall(self):
                return [{
                    "id": 1, "my_team_id": "me", "opponent_team_id": "opp",
                    "actual_my": 11.0, "actual_opp": 10.0, "actual_winner": "me",
                }]

        class FakeConn:
            def execute(self, sql, params=None):
                self.sql = sql
                self.params = params
                return FakeResult()

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect), self.assertRaisesRegex(
            ValueError, "contradict immutable"
        ):
            sandlot_db.update_projection_actuals(
                matchup_key="league:4:me:opp", period_id="4", actual_my=12.0,
                actual_opp=10.0, actual_winner="me",
            )

    def test_update_projection_actuals_validates_winner_and_is_idempotent(self):
        class Result:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        target = {
            "id": 1, "my_team_id": "me", "opponent_team_id": "opp",
            "actual_my": 12.0, "actual_opp": 10.0, "actual_winner": "me",
        }

        class Conn:
            def execute(self, sql, params=None):
                return Result([target] if "SELECT id" in sql else [{"id": 1}])

        @contextmanager
        def fake_connect():
            yield Conn()

        with patch.object(sandlot_db, "connect", fake_connect):
            self.assertEqual(sandlot_db.update_projection_actuals(
                matchup_key="league:4:me:opp", period_id="4", actual_my=12.0,
                actual_opp=10.0, actual_winner="me",
            ), 1)
            with self.assertRaisesRegex(ValueError, "winner contradicts"):
                sandlot_db.update_projection_actuals(
                    matchup_key="league:4:me:opp", period_id="4", actual_my=12.0,
                    actual_opp=10.0, actual_winner="opp",
                )

        class EmptyConn:
            def execute(self, sql, params=None):
                return Result([])

        @contextmanager
        def empty_connect():
            yield EmptyConn()

        with patch.object(sandlot_db, "connect", empty_connect):
            self.assertEqual(sandlot_db.update_projection_actuals(
                matchup_key="league:99:me:opp", period_id="99", actual_my=1.0,
                actual_opp=0.0, actual_winner="me",
            ), 0)


if __name__ == "__main__":
    unittest.main()
