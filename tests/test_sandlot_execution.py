import hashlib
import os
import unittest
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import sandlot_api
import sandlot_db
import sandlot_execution
from scripts import sandlot_execution_runner


NOW = datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc)
OWNER_TOKEN = "owner-secret"
RUNNER_TOKEN = "runner-secret"


def valid_fixture():
    moves = [
        {
            "order": 1,
            "player_id": "bench",
            "player_name": "Bench Bat",
            "from_slot": "RES",
            "to_slot": "OF",
        },
        {
            "order": 2,
            "player_id": "starter",
            "player_name": "Current Starter",
            "from_slot": "OF",
            "to_slot": "RES",
        },
    ]
    target = {
        "period_number": 17,
        "matchup_key": 6,
        "start": "2026-07-13",
        "end": "2026-07-26",
    }
    expected = {
        "proposal_id": "lineup-swap:starter:bench:OF",
        "input_hash": "a" * 64,
        "snapshot_id": 274,
        "target_period": target,
        "slot_moves": moves,
    }
    contract = {
        "version": 2,
        "proposal_id": expected["proposal_id"],
        "type": "lineup_swap",
        "executable": False,
        "writes_enabled": False,
        "action": "change_slot",
        "snapshot_id": 274,
        "league_id": "league",
        "team_id": "team",
        "target_period": target,
        "slot_moves": moves,
        "requires_multi_step": False,
        "movability": {
            "deadline": {
                "state": "known",
                "at": (NOW + timedelta(hours=1)).isoformat(),
            },
        },
        "confirmation": {
            "required": True,
            "mode": "exact_contract_match",
            "expected": expected,
        },
        "input_hash": expected["input_hash"],
    }
    action = {
        "id": expected["proposal_id"],
        "review": {
            "state": "reviewable",
            "proposal_id": expected["proposal_id"],
            "snapshot_id": 274,
            "input_hash": expected["input_hash"],
            "target_period": target,
            "slot_moves": moves,
            "contract": contract,
        },
    }
    snapshot = {
        "id": 274,
        "taken_at": NOW,
        "data": {
            "league_id": "league",
            "team_id": "team",
            "roster": {
                "rows": [
                    {"id": "bench", "name": "Bench Bat"},
                    {"id": "starter", "name": "Current Starter"},
                    {"id": "judge", "name": "Aaron Judge"},
                ],
            },
        },
    }
    submitted = {
        "mode": "dry_run",
        "proposal_id": expected["proposal_id"],
        "snapshot_id": 274,
        "input_hash": expected["input_hash"],
        "confirmation": deepcopy(expected),
    }
    return snapshot, action, submitted


def live_fixture():
    return {
        "snapshot": {
            "roster": {
                "period_number": 17,
                "rows": [
                    {
                        "id": "bench",
                        "name": "Bench Bat",
                        "slot": "RES",
                        "lineup_eligibility": {
                            "eligible_statuses": ["ACTIVE", "RES"],
                            "eligible_positions": ["OF", "UT"],
                        },
                    },
                    {
                        "id": "starter",
                        "name": "Current Starter",
                        "slot": "OF",
                        "lineup_eligibility": {
                            "eligible_statuses": ["ACTIVE", "RES"],
                            "eligible_positions": ["OF", "UT"],
                        },
                    },
                    {
                        "id": "judge",
                        "name": "Aaron Judge",
                        "slot": "OF",
                        "lineup_eligibility": {
                            "eligible_statuses": ["ACTIVE", "RES"],
                            "eligible_positions": ["OF", "UT"],
                        },
                    },
                ],
            },
        },
        "dom_slots": {
            "bench": {"slot": "RES", "slot_source": "dom.lineup-btn"},
            "starter": {"slot": "OF", "slot_source": "dom.lineup-btn"},
            "judge": {"slot": "OF", "slot_source": "dom.lineup-btn"},
        },
    }


class ExecutionContractTests(unittest.TestCase):
    def test_prepares_short_lived_read_only_request_with_full_roster_invariant(self):
        snapshot, action, submitted = valid_fixture()

        request = sandlot_execution.prepare_dry_run_request(
            snapshot_row=snapshot,
            action=action,
            submitted=submitted,
            now=NOW,
        )

        self.assertEqual(request["mode"], "dry_run")
        self.assertEqual(request["state"], "pending")
        self.assertFalse(request["writes_enabled"])
        self.assertEqual(request["expected_roster_ids"], ["bench", "judge", "starter"])
        self.assertEqual(request["safety"]["roster_departures"], [])
        self.assertFalse(request["safety"]["protected_players_may_leave_roster"])
        self.assertLessEqual(request["expires_at"], NOW + timedelta(seconds=120))

    def test_exact_confirmation_mismatch_is_rejected(self):
        snapshot, action, submitted = valid_fixture()
        submitted["confirmation"]["slot_moves"][0]["to_slot"] = "UT"

        with self.assertRaisesRegex(sandlot_execution.ExecutionContractError, "Exact confirmation"):
            sandlot_execution.prepare_dry_run_request(
                snapshot_row=snapshot,
                action=action,
                submitted=submitted,
                now=NOW,
            )

    def test_multi_step_or_non_lineup_contract_is_rejected(self):
        snapshot, action, submitted = valid_fixture()
        action["review"]["contract"]["requires_multi_step"] = True

        with self.assertRaisesRegex(sandlot_execution.ExecutionContractError, "simple two-player"):
            sandlot_execution.prepare_dry_run_request(
                snapshot_row=snapshot,
                action=action,
                submitted=submitted,
                now=NOW,
            )

    def test_expired_deadline_is_rejected(self):
        snapshot, action, submitted = valid_fixture()
        action["review"]["contract"]["movability"]["deadline"]["at"] = (NOW - timedelta(seconds=1)).isoformat()

        with self.assertRaisesRegex(sandlot_execution.ExecutionContractError, "deadline has passed"):
            sandlot_execution.prepare_dry_run_request(
                snapshot_row=snapshot,
                action=action,
                submitted=submitted,
                now=NOW,
            )

    def test_hashed_credentials_are_role_specific(self):
        env = {
            "SANDLOT_OWNER_ACTION_TOKEN_SHA256": hashlib.sha256(OWNER_TOKEN.encode()).hexdigest(),
            "SANDLOT_RUNNER_TOKEN_SHA256": hashlib.sha256(RUNNER_TOKEN.encode()).hexdigest(),
        }
        with patch.dict(os.environ, env, clear=True):
            sandlot_execution.require_hashed_bearer(
                f"Bearer {OWNER_TOKEN}", digest_env="SANDLOT_OWNER_ACTION_TOKEN_SHA256"
            )
            with self.assertRaises(PermissionError):
                sandlot_execution.require_hashed_bearer(
                    f"Bearer {RUNNER_TOKEN}", digest_env="SANDLOT_OWNER_ACTION_TOKEN_SHA256"
                )

    def test_sensitive_preflight_evidence_is_rejected(self):
        with self.assertRaisesRegex(sandlot_execution.ExecutionContractError, "Sensitive data"):
            sandlot_execution.validate_preflight_report({
                "outcome": "passed",
                "checks": [{"key": "ok", "state": "passed", "detail": "ok"}],
                "evidence": {"cookies": ["secret"]},
                "observed_at": NOW,
                "writes_attempted": False,
            })


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        row = self.rows.pop(0) if self.rows else None
        return FakeResult(row)


@contextmanager
def fake_connect(connection):
    yield connection


class ExecutionPersistenceTests(unittest.TestCase):
    def test_schema_retains_snapshot_identity_without_blocking_snapshot_pruning(self):
        connection = FakeConnection()
        with patch("sandlot_db.connect", lambda: fake_connect(connection)):
            sandlot_db.init_schema()

        table_sql = next(sql for sql, _params in connection.calls if "CREATE TABLE IF NOT EXISTS execution_requests" in sql)
        self.assertIn("snapshot_id BIGINT NOT NULL", table_sql)
        self.assertNotIn("REFERENCES snapshots", table_sql)
        self.assertIn("safety JSONB NOT NULL", table_sql)

    def test_claim_is_atomic_skip_locked_and_expired_claims_are_not_requeued(self):
        connection = FakeConnection(rows=[None, {"request_id": "xreq", "state": "claimed"}])
        with patch("sandlot_db.connect", lambda: fake_connect(connection)):
            row = sandlot_db.claim_next_execution_request(
                runner_id="zach-mac",
                lease_token_hash="digest",
                lease_seconds=90,
            )

        self.assertEqual(row["state"], "claimed")
        all_sql = "\n".join(sql for sql, _params in connection.calls)
        self.assertIn("FOR UPDATE SKIP LOCKED", all_sql)
        self.assertIn("state = 'expired'", all_sql)
        self.assertNotIn("SET state = 'pending'", all_sql)

    def test_terminal_result_uses_claimed_state_and_live_lease_compare_and_swap(self):
        connection = FakeConnection(rows=[None, {"request_id": "xreq", "state": "preflight_passed"}])
        with patch("sandlot_db.connect", lambda: fake_connect(connection)):
            row = sandlot_db.finish_execution_preflight(
                request_id="xreq",
                lease_token_hash="lease-digest",
                outcome="passed",
                evidence={"writes_attempted": False},
                failure_reason=None,
            )

        self.assertEqual(row["state"], "preflight_passed")
        terminal_sql = connection.calls[-1][0]
        self.assertIn("state = 'claimed'", terminal_sql)
        self.assertIn("lease_token_hash = %s", terminal_sql)
        self.assertIn("lease_token_hash = NULL", terminal_sql)

    def test_runner_free_text_is_not_persisted_and_evidence_is_allowlisted(self):
        report = sandlot_execution.validate_preflight_report({
            "outcome": "failed",
            "checks": [{"key": "deadline", "state": "failed", "detail": "do not store this arbitrary text"}],
            "evidence": {
                "source": "visible_fantrax_dom+authenticated_read_api",
                "fantrax_click_count": 0,
                "fantrax_write_count": 0,
            },
            "observed_at": NOW,
            "writes_attempted": False,
        })
        self.assertEqual(report["checks"][0]["detail"], "Live check failed.")
        with self.assertRaisesRegex(sandlot_execution.ExecutionContractError, "Unsupported"):
            sandlot_execution.validate_preflight_report({
                "outcome": "passed",
                "checks": [{"key": "ok", "state": "passed", "detail": "ok"}],
                "evidence": {
                    "fantrax_click_count": 0,
                    "fantrax_write_count": 0,
                    "arbitrary": "not allowed",
                },
                "observed_at": NOW,
                "writes_attempted": False,
            })

    def test_report_identity_is_bound_back_to_claimed_contract(self):
        snapshot, action, submitted = valid_fixture()
        claimed = sandlot_execution.prepare_dry_run_request(
            snapshot_row=snapshot,
            action=action,
            submitted=submitted,
            now=NOW,
        )
        report = {
            "outcome": "passed",
            "checks": [{"key": "all", "state": "passed", "detail": "ok"}],
            "evidence": {
                "source": "visible_fantrax_dom+authenticated_read_api",
                "target_period": 17,
                "roster_player_count": 3,
                "participant_ids": ["wrong", "players"],
                "fantrax_click_count": 0,
                "fantrax_write_count": 0,
            },
            "observed_at": NOW,
            "writes_attempted": False,
        }

        with self.assertRaisesRegex(sandlot_execution.ExecutionContractError, "participants"):
            sandlot_execution.validate_preflight_report(report, request_row=claimed)


class VisibleRunnerTests(unittest.TestCase):
    def claimed_request(self):
        snapshot, action, submitted = valid_fixture()
        request = sandlot_execution.prepare_dry_run_request(
            snapshot_row=snapshot,
            action=action,
            submitted=submitted,
            now=NOW,
        )
        return {
            **request,
            "contract": request["contract"],
        }

    def test_live_preflight_passes_with_period_roster_dom_api_and_eligibility_proof(self):
        result = sandlot_execution_runner.evaluate_preflight(
            self.claimed_request(), live_fixture(), now=NOW
        )

        self.assertEqual(result["outcome"], "passed")
        self.assertFalse(result["writes_attempted"])
        self.assertEqual(result["evidence"]["fantrax_click_count"], 0)
        self.assertEqual(result["evidence"]["fantrax_write_count"], 0)
        self.assertTrue(all(check["state"] == "passed" for check in result["checks"]))

    def test_visible_reader_uses_only_headful_capture_and_read_api(self):
        request = self.claimed_request()
        with (
            patch("scripts.sandlot_execution_runner.auth._load_cookies", return_value=[{"name": "sid", "value": "redacted"}]),
            patch("scripts.sandlot_execution_runner.auth._build_session", return_value="read-session") as session,
            patch("scripts.sandlot_execution_runner.fantrax_dom.capture_roster_html", return_value="<html></html>") as capture,
            patch("scripts.sandlot_execution_runner.fantrax_dom.lineup_slots_from_html", return_value={}) as parse,
            patch("scripts.sandlot_execution_runner.fantrax_data.collect_all", return_value={"roster": {"rows": []}}) as collect,
        ):
            live = sandlot_execution_runner.FantraxVisibleReader().read(request)

        self.assertEqual(live["dom_slots"], {})
        self.assertTrue(capture.call_args.kwargs["headful"])
        session.assert_called_once()
        parse.assert_called_once_with("<html></html>")
        collect.assert_called_once_with("read-session", "league", "team")

    def test_roster_change_fails_closed_and_protects_aaron_judge(self):
        live = live_fixture()
        live["snapshot"]["roster"]["rows"] = [
            row for row in live["snapshot"]["roster"]["rows"] if row["id"] != "judge"
        ]

        result = sandlot_execution_runner.evaluate_preflight(
            self.claimed_request(), live, now=NOW
        )

        self.assertEqual(result["outcome"], "failed")
        failed = {check["key"] for check in result["checks"] if check["state"] == "failed"}
        self.assertIn("roster_set", failed)
        self.assertIn("roster_departures", failed)

    def test_period_dom_or_destination_drift_each_fail_closed(self):
        variants = []
        wrong_period = live_fixture()
        wrong_period["snapshot"]["roster"]["period_number"] = 18
        variants.append((wrong_period, "target_period"))
        wrong_dom = live_fixture()
        wrong_dom["dom_slots"]["starter"]["slot"] = "RES"
        variants.append((wrong_dom, "from_slot:starter"))
        illegal_destination = live_fixture()
        illegal_destination["snapshot"]["roster"]["rows"][0]["lineup_eligibility"]["eligible_positions"] = ["UT"]
        variants.append((illegal_destination, "destination:bench"))

        for live, expected_key in variants:
            with self.subTest(expected_key=expected_key):
                result = sandlot_execution_runner.evaluate_preflight(
                    self.claimed_request(), live, now=NOW
                )
                failed = {check["key"] for check in result["checks"] if check["state"] == "failed"}
                self.assertEqual(result["outcome"], "failed")
                self.assertIn(expected_key, failed)


class ExecutionApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(sandlot_api.app, raise_server_exceptions=False)
        self.snapshot, self.action, self.submitted = valid_fixture()
        self.env = {
            "SANDLOT_EXECUTION_DRY_RUN_ENABLED": "true",
            "SANDLOT_OWNER_ACTION_TOKEN_SHA256": hashlib.sha256(OWNER_TOKEN.encode()).hexdigest(),
            "SANDLOT_RUNNER_TOKEN_SHA256": hashlib.sha256(RUNNER_TOKEN.encode()).hexdigest(),
        }

    def test_disabled_control_plane_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/execution-requests", json=self.submitted)
        self.assertEqual(response.status_code, 503)

    def test_owner_and_runner_credentials_are_not_interchangeable(self):
        with patch.dict(os.environ, self.env, clear=True):
            response = self.client.post(
                "/api/execution-requests",
                json=self.submitted,
                headers={"authorization": f"Bearer {RUNNER_TOKEN}"},
            )
        self.assertEqual(response.status_code, 401)

    def test_same_owner_and_runner_digest_is_a_configuration_failure(self):
        same = {
            **self.env,
            "SANDLOT_RUNNER_TOKEN_SHA256": self.env["SANDLOT_OWNER_ACTION_TOKEN_SHA256"],
        }
        with patch.dict(os.environ, same, clear=True):
            response = self.client.post(
                "/api/execution-requests",
                json=self.submitted,
                headers={"authorization": f"Bearer {OWNER_TOKEN}"},
            )
        self.assertEqual(response.status_code, 503)

    def test_exact_request_is_created_idempotently_without_contract_in_response(self):
        prepared = sandlot_execution.prepare_dry_run_request(
            snapshot_row=self.snapshot,
            action=self.action,
            submitted=self.submitted,
            now=NOW,
        )
        stored = {
            **prepared,
            "created_at": NOW,
            "claimed_at": None,
            "completed_at": None,
            "failure_reason": None,
            "evidence": {},
        }
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("sandlot_api._latest_reviewed_action", return_value=(self.snapshot, self.action, self.action["review"])),
            patch("sandlot_api.sandlot_execution.utc_now", return_value=NOW),
            patch("sandlot_api.sandlot_db.create_execution_request", return_value=(stored, False)) as create,
        ):
            response = self.client.post(
                "/api/execution-requests",
                json=self.submitted,
                headers={"authorization": f"Bearer {OWNER_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertNotIn("contract", response.json())
        self.assertFalse(response.json()["writes_enabled"])
        self.assertEqual(create.call_count, 1)

    def test_claim_returns_plaintext_lease_once_and_stores_only_digest(self):
        prepared = sandlot_execution.prepare_dry_run_request(
            snapshot_row=self.snapshot,
            action=self.action,
            submitted=self.submitted,
            now=NOW,
        )
        stored = {**prepared, "created_at": NOW, "lease_expires_at": NOW + timedelta(seconds=30)}
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("sandlot_api.sandlot_execution.new_lease", return_value=("plain-lease-token-value", "lease-digest")),
            patch("sandlot_api.sandlot_db.claim_next_execution_request", return_value=stored) as claim,
        ):
            response = self.client.post(
                "/api/execution-requests/claim",
                json={"runner_id": "zach-mac"},
                headers={"authorization": f"Bearer {RUNNER_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["lease_token"], "plain-lease-token-value")
        self.assertEqual(claim.call_args.kwargs["lease_token_hash"], "lease-digest")
        self.assertNotEqual(claim.call_args.kwargs["lease_token_hash"], "plain-lease-token-value")

    def test_terminal_preflight_is_compare_and_swap_bound_to_live_lease(self):
        body = {
            "lease_token": "a-valid-lease-token",
            "outcome": "passed",
            "checks": [{"key": "all", "state": "passed", "detail": "All live checks passed"}],
            "evidence": {
                "source": "visible_fantrax_dom+authenticated_read_api",
                "target_period": 17,
                "roster_player_count": 3,
                "participant_ids": ["bench", "starter"],
                "fantrax_click_count": 0,
                "fantrax_write_count": 0,
            },
            "observed_at": NOW.isoformat(),
            "writes_attempted": False,
        }
        claimed = sandlot_execution.prepare_dry_run_request(
            snapshot_row=self.snapshot,
            action=self.action,
            submitted=self.submitted,
            now=NOW,
        )
        claimed["state"] = "claimed"
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("sandlot_api.sandlot_db.execution_request_by_id", return_value=claimed),
            patch("sandlot_api.sandlot_db.finish_execution_preflight", return_value=None) as finish,
        ):
            response = self.client.post(
                "/api/execution-requests/xreq_one/preflight",
                json=body,
                headers={"authorization": f"Bearer {RUNNER_TOKEN}"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            finish.call_args.kwargs["lease_token_hash"],
            sandlot_execution.token_digest("a-valid-lease-token"),
        )


if __name__ == "__main__":
    unittest.main()
