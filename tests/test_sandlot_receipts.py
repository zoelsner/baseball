import copy
import hashlib
import json
import os
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import sandlot_api
import sandlot_db
import sandlot_receipts
from scripts import run_monday_lineup


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)


def receipt_fixture(*, entries=None, result=None, week_start=date(2026, 7, 13)):
    entries = entries or [
        {
            "id": "bat",
            "name": "Bench Bat",
            "tokens": {"OF", "UT"},
            "proj": 12.34567,
            "hitter_proj": 12.34567,
            "pitcher_proj": 0.0,
            "basis": "2.1/gm x 6 games",
            "slot": "RES",
            "slot_source": "raw.statusId",
            "injury": "",
        },
        {
            "id": "two-way",
            "name": "Two Way",
            "tokens": {"OF", "SP"},
            "proj": 20.0,
            "hitter_proj": 7.0,
            "pitcher_proj": 20.0,
            "basis": "hitting plus pitching",
            "slot": "SP",
            "slot_source": "raw.posId",
            "injury": None,
        },
    ]
    result = result or {
        "lineup": [("SP", "Two Way"), ("OF", "Bench Bat")],
        "projected_total": 32.34567,
        "unfilled": ["RP"],
    }
    current = next((entry for entry in entries if entry.get("id") == "two-way"), entries[0])
    return sandlot_receipts.build_monday_lineup_receipt(
        snapshot={
            "id": 277,
            "taken_at": "2026-07-12T14:40:58Z",
            "source": "manual",
            "status": "success",
            "league_id": "league",
            "team_id": "team",
        },
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        result=result,
        entries=entries,
        current_active=[
            {
                **current,
                "slot": current.get("slot") or "OF",
                "assigned_projection": current.get("pitcher_proj", current.get("proj")),
            },
        ],
        current_total=20.0,
        generated_at=NOW,
    )


class RecommendationReceiptBuilderTests(unittest.TestCase):
    def test_hash_is_stable_across_input_and_assignment_order(self):
        baseline = receipt_fixture()
        entries = list(reversed(baseline["recommendation"]["projection_inputs"]))
        rebuilt_entries = [
            {
                "id": entry["id"],
                "name": entry["name"],
                "tokens": set(reversed(entry["tokens"])),
                "proj": entry["projected_points"],
                "hitter_proj": entry["hitter_projected_points"],
                "pitcher_proj": entry["pitcher_projected_points"],
                "basis": entry["basis"],
                "slot": entry["slot"],
                "slot_source": entry["slot_source"],
                "injury": entry["injury"],
            }
            for entry in entries
        ]
        reordered = receipt_fixture(
            entries=rebuilt_entries,
            result={
                "lineup": [("OF", "Bench Bat"), ("SP", "Two Way")],
                "projected_total": 32.34567,
                "unfilled": ["RP"],
            },
        )

        self.assertEqual(reordered["input_hash"], baseline["input_hash"])
        self.assertEqual(reordered["receipt_id"], baseline["receipt_id"])

    def test_material_projection_change_changes_identity(self):
        baseline = receipt_fixture()
        entries = copy.deepcopy(baseline["recommendation"]["projection_inputs"])
        rebuilt = []
        for entry in entries:
            projected = entry["projected_points"] + (1.0 if entry["id"] == "bat" else 0.0)
            rebuilt.append({
                "id": entry["id"],
                "name": entry["name"],
                "tokens": set(entry["tokens"]),
                "proj": projected,
                "hitter_proj": projected if entry["id"] == "bat" else entry["hitter_projected_points"],
                "pitcher_proj": entry["pitcher_projected_points"],
                "basis": entry["basis"],
                "slot": entry["slot"],
                "slot_source": entry["slot_source"],
                "injury": entry["injury"],
            })

        changed = receipt_fixture(entries=rebuilt, result={
            "lineup": [("SP", "Two Way"), ("OF", "Bench Bat")],
            "projected_total": 33.34567,
            "unfilled": ["RP"],
        })

        self.assertNotEqual(changed["input_hash"], baseline["input_hash"])

    def test_assignment_uses_slot_specific_two_way_projection(self):
        receipt = receipt_fixture()
        assignment = receipt["recommendation"]["proposed_assignment"]
        two_way = next(item for item in assignment if item["player_id"] == "two-way")

        self.assertEqual(two_way["slot"], "SP")
        self.assertEqual(two_way["projected_points"], 20.0)

    def test_scope_is_week_specific_and_snapshot_pruning_evidence_is_embedded(self):
        first = receipt_fixture()
        second = receipt_fixture(week_start=date(2026, 7, 20))

        self.assertNotEqual(first["scope_key"], second["scope_key"])
        self.assertEqual(first["recommendation"]["snapshot"]["id"], 277)
        self.assertEqual(first["recommendation"]["snapshot"]["taken_at"], "2026-07-12T14:40:58+00:00")
        self.assertTrue(first["expires_at"] > first["generated_at"])

    def test_builder_rejects_nonfinite_numbers_without_mutating_inputs(self):
        entries = [{
            "id": "bad",
            "name": "Bad Projection",
            "tokens": {"OF"},
            "proj": float("nan"),
            "hitter_proj": float("nan"),
            "pitcher_proj": 0.0,
            "slot": "OF",
            "slot_source": "raw.posId",
        }]
        original = copy.deepcopy(entries)

        with self.assertRaisesRegex(ValueError, "must be finite"):
            receipt_fixture(entries=entries, result={
                "lineup": [("OF", "Bad Projection")],
                "projected_total": 0.0,
                "unfilled": [],
            })

        self.assertEqual(entries[0]["tokens"], original[0]["tokens"])
        self.assertTrue(str(entries[0]["proj"]) == str(original[0]["proj"]))

    def test_duplicate_player_names_fail_closed(self):
        entries = [
            {
                "id": player_id,
                "name": "Same Name",
                "tokens": {"OF"},
                "proj": 4.0,
                "hitter_proj": 4.0,
                "pitcher_proj": 0.0,
                "slot": slot,
                "slot_source": "raw.posId",
            }
            for player_id, slot in (("one", "OF"), ("two", "RES"))
        ]

        with self.assertRaisesRegex(ValueError, "duplicate roster player name"):
            receipt_fixture(entries=entries, result={
                "lineup": [("OF", "Same Name")],
                "projected_total": 4.0,
                "unfilled": [],
            })


class RecommendationReceiptPersistenceTests(unittest.TestCase):
    def test_schema_is_durable_and_all_states_are_constrained(self):
        calls = []

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            sandlot_db.init_schema()

        sql = "\n".join(statement for statement, _params in calls)
        self.assertIn("CREATE TABLE IF NOT EXISTS recommendation_receipts", sql)
        self.assertIn("snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL", sql)
        self.assertIn("CHECK (lifecycle_state IN ('active', 'superseded', 'expired'))", sql)
        self.assertIn("CHECK (decision_state IN ('pending', 'accepted', 'rejected'))", sql)
        self.assertIn("WHERE lifecycle_state = 'active'", sql)

    def test_new_receipt_supersedes_changed_pending_scope(self):
        receipt = receipt_fixture()
        old = {**receipt, "receipt_id": "old", "input_hash": "old-hash", "decision_state": "pending"}
        calls = []

        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                if "WHERE receipt_id = %s FOR UPDATE" in sql:
                    return Result(None)
                if "WHERE scope_key = %s AND lifecycle_state = 'active'" in sql:
                    return Result(old)
                if "INSERT INTO recommendation_receipts" in sql:
                    return Result({**receipt, "lifecycle_state": "active", "decision_state": "pending"})
                return Result(None)

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            row, created = sandlot_db.record_recommendation_receipt(receipt)

        self.assertTrue(created)
        self.assertEqual(row["receipt_id"], receipt["receipt_id"])
        update_params = [params for sql, params in calls if "SET superseded_by = %s" in sql]
        self.assertEqual(update_params, [(receipt["receipt_id"], "old")])

    def test_exact_replay_is_idempotent_but_collision_fails(self):
        receipt = receipt_fixture()

        @contextmanager
        def existing_connect(row):
            class Result:
                def fetchone(self):
                    return row

            class FakeConn:
                def execute(self, _sql, _params=None):
                    return Result()

            yield FakeConn()

        existing = {
            **receipt,
            "snapshot_id": None,
            "recommendation": json.loads(json.dumps(receipt["recommendation"])),
            "lifecycle_state": "active",
            "decision_state": "pending",
        }
        with patch.object(sandlot_db, "connect", lambda: existing_connect(existing)):
            row, created = sandlot_db.record_recommendation_receipt(receipt)
        self.assertFalse(created)
        self.assertEqual(row["receipt_id"], receipt["receipt_id"])
        self.assertIsNone(row["snapshot_id"])

        collision = copy.deepcopy(existing)
        collision["recommendation"]["evaluation"]["projected_gain"] = 999.0
        with patch.object(sandlot_db, "connect", lambda: existing_connect(collision)):
            with self.assertRaisesRegex(RuntimeError, "identity collision"):
                sandlot_db.record_recommendation_receipt(receipt)

    def test_decided_receipt_cannot_be_superseded(self):
        receipt = receipt_fixture()

        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeConn:
            def __init__(self):
                self.selects = 0

            def execute(self, sql, _params=None):
                if "WHERE receipt_id = %s FOR UPDATE" in sql:
                    return Result(None)
                if "WHERE scope_key = %s AND lifecycle_state = 'active'" in sql:
                    return Result({"receipt_id": "accepted", "decision_state": "accepted"})
                return Result(None)

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            with self.assertRaisesRegex(RuntimeError, "decided recommendation"):
                sandlot_db.record_recommendation_receipt(receipt)

    def test_latest_active_receipt_is_filtered_and_deterministic(self):
        calls = []

        class Result:
            def fetchone(self):
                return {"receipt_id": "latest"}

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                return Result()

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            row = sandlot_db.latest_active_recommendation_receipt(source="monday_lineup")

        self.assertEqual(row, {"receipt_id": "latest"})
        sql, params = calls[0]
        self.assertIn("lifecycle_state = 'active'", sql)
        self.assertIn("expires_at > clock_timestamp()", sql)
        self.assertIn("ORDER BY generated_at DESC, receipt_id DESC", sql)
        self.assertEqual(params, ("monday_lineup",))

    def test_decision_is_atomic_and_same_state_replay_is_idempotent(self):
        receipt = {
            **receipt_fixture(),
            "lifecycle_state": "active",
            "decision_state": "pending",
            "is_expired": False,
        }
        updated = {**receipt, "decision_state": "accepted"}
        calls = []

        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeConn:
            def __init__(self, rows):
                self.rows = list(rows)

            def execute(self, sql, params=None):
                calls.append((sql, params))
                return Result(self.rows.pop(0))

        @contextmanager
        def deciding_connect():
            yield FakeConn([receipt, updated])

        with patch.object(sandlot_db, "connect", deciding_connect):
            row, changed = sandlot_db.decide_recommendation_receipt(
                receipt_id=receipt["receipt_id"],
                input_hash=receipt["input_hash"],
                decision="accepted",
                source="owner_bridge",
                reason="Using it",
            )
        self.assertTrue(changed)
        self.assertEqual(row["decision_state"], "accepted")
        update_sql, update_params = calls[1]
        self.assertIn("decision_state = 'pending'", update_sql)
        self.assertIn("expires_at > clock_timestamp()", update_sql)
        self.assertIn("expires_at <= clock_timestamp() AS is_expired", calls[0][0])
        self.assertIn("decided_at = clock_timestamp()", update_sql)
        self.assertEqual(update_params[:3], ("accepted", "owner_bridge", "Using it"))

        replay = {**updated, "is_expired": False}

        @contextmanager
        def replay_connect():
            yield FakeConn([replay])

        with patch.object(sandlot_db, "connect", replay_connect):
            row, changed = sandlot_db.decide_recommendation_receipt(
                receipt_id=receipt["receipt_id"],
                input_hash=receipt["input_hash"],
                decision="accepted",
                source="owner_bridge",
            )
        self.assertFalse(changed)
        self.assertNotIn("is_expired", row)

    def test_decision_rejects_missing_stale_expired_superseded_and_conflicting_receipts(self):
        base = {
            **receipt_fixture(),
            "lifecycle_state": "active",
            "decision_state": "pending",
            "is_expired": False,
        }

        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        @contextmanager
        def fake_connect(row):
            class FakeConn:
                def execute(self, _sql, _params=None):
                    return Result(row)
            yield FakeConn()

        cases = [
            (None, LookupError, "not found", base["input_hash"], "accepted"),
            (base, ValueError, "stale or mismatched", "f" * 64, "accepted"),
            ({**base, "is_expired": True}, ValueError, "expired", base["input_hash"], "accepted"),
            ({**base, "lifecycle_state": "superseded"}, ValueError, "no longer active", base["input_hash"], "accepted"),
            ({**base, "decision_state": "rejected"}, ValueError, "already rejected", base["input_hash"], "accepted"),
        ]
        for stored, exception, message, input_hash, decision in cases:
            with self.subTest(message=message), patch.object(
                sandlot_db, "connect", lambda stored=stored: fake_connect(stored)
            ):
                with self.assertRaisesRegex(exception, message):
                    sandlot_db.decide_recommendation_receipt(
                        receipt_id=base["receipt_id"],
                        input_hash=input_hash,
                        decision=decision,
                        source="owner_bridge",
                    )


class RecommendationReceiptApiTests(unittest.TestCase):
    OWNER_TOKEN = "owner-secret"

    def setUp(self):
        self.client = TestClient(sandlot_api.app, raise_server_exceptions=False)
        self.receipt = {
            **receipt_fixture(),
            "lifecycle_state": "active",
            "decision_state": "pending",
            "decision_reason": None,
            "decided_at": None,
            "outcome_state": "pending",
        }
        self.env = {
            "SANDLOT_OWNER_ACTION_TOKEN_SHA256": hashlib.sha256(self.OWNER_TOKEN.encode()).hexdigest(),
        }

    def test_latest_receipt_is_public_but_projection_inputs_are_not(self):
        with patch("sandlot_api.sandlot_db.latest_active_recommendation_receipt", return_value=self.receipt):
            response = self.client.get("/api/recommendation-receipts/latest")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["receipt_id"], self.receipt["receipt_id"])
        self.assertEqual(payload["evaluation"]["projected_gain"], self.receipt["projected_gain"])
        self.assertNotIn("recommendation", payload)
        self.assertNotIn("projection_inputs", json.dumps(payload))
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["fantrax_changed"])
        self.assertFalse(payload["writes_enabled"])

    def test_latest_receipt_returns_no_content_when_none_is_active(self):
        with patch("sandlot_api.sandlot_db.latest_active_recommendation_receipt", return_value=None):
            response = self.client.get("/api/recommendation-receipts/latest")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_decision_requires_owner_auth_and_records_intent_only(self):
        body = {
            "decision": "accepted",
            "input_hash": self.receipt["input_hash"].upper(),
            "reason": "  I will   use this lineup  ",
        }
        with patch.dict(os.environ, self.env, clear=True):
            unauthorized = self.client.post(
                f"/api/recommendation-receipts/{self.receipt['receipt_id']}/decision",
                json=body,
            )
        self.assertEqual(unauthorized.status_code, 401)

        accepted = {**self.receipt, "decision_state": "accepted", "decision_reason": "I will use this lineup"}
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("sandlot_api.sandlot_db.decide_recommendation_receipt", return_value=(accepted, True)) as decide,
        ):
            response = self.client.post(
                f"/api/recommendation-receipts/{self.receipt['receipt_id']}/decision",
                json=body,
                headers={"authorization": f"Bearer {self.OWNER_TOKEN}"},
            )
        self.assertEqual(response.status_code, 200)
        decide.assert_called_once_with(
            receipt_id=self.receipt["receipt_id"],
            input_hash=self.receipt["input_hash"],
            decision="accepted",
            source="owner_bridge",
            reason="I will use this lineup",
        )
        payload = response.json()
        self.assertTrue(payload["changed"])
        self.assertFalse(payload["fantrax_changed"])
        self.assertFalse(payload["writes_enabled"])

    def test_stale_decision_is_a_conflict(self):
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch(
                "sandlot_api.sandlot_db.decide_recommendation_receipt",
                side_effect=ValueError("Recommendation receipt hash is stale or mismatched"),
            ),
        ):
            response = self.client.post(
                f"/api/recommendation-receipts/{self.receipt['receipt_id']}/decision",
                json={"decision": "rejected", "input_hash": self.receipt["input_hash"]},
                headers={"authorization": f"Bearer {self.OWNER_TOKEN}"},
            )
        self.assertEqual(response.status_code, 409)


@unittest.skipUnless(os.environ.get("SANDLOT_TEST_DATABASE_URL"), "requires disposable Postgres")
class RecommendationReceiptPostgresConcurrencyTests(unittest.TestCase):
    def test_waiting_decision_rechecks_wall_clock_expiry_after_row_lock(self):
        database_url = os.environ["SANDLOT_TEST_DATABASE_URL"]
        receipt = receipt_fixture(week_start=date(2099, 1, 5))
        receipt["snapshot_id"] = None
        outcome = []
        waiting_transaction_started = threading.Event()

        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            sandlot_db.init_schema()
            sandlot_db.record_recommendation_receipt(receipt)
            try:
                with sandlot_db.connect() as setup:
                    setup.execute(
                        "UPDATE recommendation_receipts SET expires_at = clock_timestamp() + interval '300 milliseconds' WHERE receipt_id = %s",
                        (receipt["receipt_id"],),
                    )

                lock_conn = sandlot_db.psycopg.connect(database_url)
                lock_conn.execute(
                    "SELECT receipt_id FROM recommendation_receipts WHERE receipt_id = %s FOR UPDATE",
                    (receipt["receipt_id"],),
                )

                def decide_while_waiting():
                    waiting_conn = sandlot_db.psycopg.connect(database_url, row_factory=sandlot_db.dict_row)
                    waiting_conn.execute("SELECT now()")  # pin transaction time before the receipt expires

                    @contextmanager
                    def prestarted_connect():
                        try:
                            yield waiting_conn
                            waiting_conn.commit()
                        except Exception:
                            waiting_conn.rollback()
                            raise

                    waiting_transaction_started.set()
                    try:
                        with patch.object(sandlot_db, "connect", prestarted_connect):
                            sandlot_db.decide_recommendation_receipt(
                                receipt_id=receipt["receipt_id"],
                                input_hash=receipt["input_hash"],
                                decision="accepted",
                                source="concurrency_test",
                            )
                    except Exception as exc:  # captured for assertion in the test thread
                        outcome.append(exc)
                    finally:
                        waiting_conn.close()

                worker = threading.Thread(target=decide_while_waiting)
                worker.start()
                self.assertTrue(waiting_transaction_started.wait(timeout=2))
                time.sleep(0.45)
                lock_conn.commit()
                lock_conn.close()
                worker.join(timeout=3)

                self.assertFalse(worker.is_alive())
                self.assertEqual(len(outcome), 1)
                self.assertIsInstance(outcome[0], ValueError)
                self.assertIn("expired", str(outcome[0]))
            finally:
                with sandlot_db.connect() as cleanup:
                    cleanup.execute(
                        "DELETE FROM recommendation_receipts WHERE receipt_id = %s",
                        (receipt["receipt_id"],),
                    )


class MondayLineupReceiptGateTests(unittest.TestCase):
    def test_trusted_slots_pass_and_untrusted_slots_fail_closed(self):
        run_monday_lineup.require_trusted_roster_slots([
            {"id": "starter", "name": "Starter", "slot_source": "raw.posId"},
            {"id": "reserve", "name": "Reserve", "slot_source": "raw.statusId"},
        ])

        with self.assertRaisesRegex(RuntimeError, "trusted Fantrax slots missing"):
            run_monday_lineup.require_trusted_roster_slots([
                {"id": "starter", "name": "Starter", "slot_source": "position_fallback"},
            ])
        with self.assertRaisesRegex(RuntimeError, "trusted Fantrax slots missing"):
            run_monday_lineup.require_trusted_roster_slots([
                {"id": "starter", "name": "Starter"},
            ])
        with self.assertRaisesRegex(RuntimeError, "roster is empty"):
            run_monday_lineup.require_trusted_roster_slots([])


if __name__ == "__main__":
    unittest.main()
