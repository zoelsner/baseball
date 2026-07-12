import copy
import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

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
