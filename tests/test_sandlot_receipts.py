import copy
import hashlib
import inspect
import json
import os
import threading
import time
import unittest
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import sandlot_api
import sandlot_db
import sandlot_receipts
import fantrax_data
import sandlot_refresh
from scripts import run_monday_lineup


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
DEFAULT_UNFILLED = (
    ["C", "1B", "2B", "3B", "SS"]
    + ["OF"] * 2
    + ["UT"] * 3
    + ["SP"] * 5
    + ["RP"] * 3
)


def receipt_fixture(
    *, entries=None, result=None, week_start=date(2026, 7, 13),
    generated_at=NOW, decision_deadline_at=None,
):
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
        "unfilled": list(DEFAULT_UNFILLED),
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
        decision_deadline_at=decision_deadline_at or datetime.combine(week_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=23),
        generated_at=generated_at,
    )


def period_evidence_fixture(receipt, *, actual="proposed", capability=True):
    players = [
        {
            "player_id": "bat", "player_name": "Bench Bat", "scoring_role": "hitter",
            "slot": "OF" if actual == "proposed" else "RES", "slot_source": "raw.posId",
            "raw_pos_id": "012", "raw_status_id": "1", "eligibility_pos_ids": ["012", "014"],
            "period_fpts": "13", "period_fpts_source": "SCORE:fpts",
        },
        {
            "player_id": "two-way", "player_name": "Two Way", "scoring_role": "pitcher",
            "slot": "SP", "slot_source": "raw.posId", "raw_pos_id": "015", "raw_status_id": "1",
            "eligibility_pos_ids": ["015"], "period_fpts": "21", "period_fpts_source": "SCORE:fpts",
        },
    ]
    daily = [
        {
            "player_id": item["player_id"],
            "scoring_role": item["scoring_role"],
            "state": "active" if item["slot"] != "RES" else "bench",
            "raw_pos_id": item["raw_pos_id"],
        }
        for item in players
    ]
    observed = "34" if actual == "proposed" else "21"
    evidence = {
        "evidence_version": fantrax_data.LINEUP_PERIOD_EVIDENCE_VERSION,
        "league_id": receipt["league_id"],
        "team_id": receipt["team_id"],
        "period": {
            "number": "17", "start": str(receipt["period_start"]), "end": str(receipt["period_end"]),
        },
        "source": {"method": "getTeamRosterInfo"},
        "observed_team_total": observed,
        "active_player_total": observed,
        "active_player_count": sum(item["state"] == "active" for item in daily),
        "players": players,
        "parent_v1_evidence_hash": "a" * 64,
        "parent_v1_state": "linked",
        "counterfactual_capability": {
            "eligible": capability,
            "scope": "single_monday_lineup_window" if capability else None,
            "reason": None if capability else "period_player_fpts_not_attributed_to_lineup_windows",
        },
        "final_assignment_potential_total": observed,
        "final_assignment_total_state": "reconciled_single_window",
        "lineup_policy": {"lineup_changes_executed": "Weekly every Monday"},
        "participation": {
            "source_method": "getLiveScoringStats",
            "cadence": "monday_lineup_windows_with_daily_proof",
            "stable_within_windows": True,
            "window_count": 1 if capability else 2,
            "windows": [{"start": str(receipt["period_start"]), "end": str(receipt["period_end"]), "stable": True}],
            "observed_team_total": observed,
            "days": [{
                "date": str(receipt["period_end"]), "lineup_window_start": str(receipt["period_start"]),
                "response_period": "17", "all_events_finished": True,
                "active_count": sum(item["state"] == "active" for item in daily),
                "bench_count": sum(item["state"] == "bench" for item in daily),
                "credited_team_total": observed, "players": daily,
            }],
        },
    }
    evidence["evidence_hash"] = fantrax_data.lineup_period_evidence_hash(evidence)
    return evidence


class RecommendationReceiptBuilderTests(unittest.TestCase):
    def test_receipt_binds_exact_first_game_deadline_and_rejects_late_generation(self):
        receipt = receipt_fixture()
        deadline = receipt["recommendation"]["period"]["decision_deadline_at"]

        self.assertEqual(deadline, "2026-07-13T23:00:00+00:00")
        self.assertEqual(receipt["recommendation"]["period"]["deadline_source"], "mlb_schedule_first_game_v1")
        with self.assertRaisesRegex(ValueError, "before the first scoring deadline"):
            receipt_fixture(
                generated_at=datetime(2026, 7, 13, 23, 0, tzinfo=timezone.utc),
                decision_deadline_at=datetime(2026, 7, 13, 23, 0, tzinfo=timezone.utc),
            )

    def test_first_scheduled_game_deadline_uses_earliest_timezone_aware_game(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"dates": [{"games": [
            {"gameDate": "2026-07-13T23:10:00Z"},
            {"gameDate": "2026-07-13T17:05:00Z"},
        ]}]}
        with patch.object(run_monday_lineup.requests, "get", return_value=response):
            deadline = run_monday_lineup.first_scheduled_game_at(date(2026, 7, 13), date(2026, 7, 19))

        self.assertEqual(deadline.isoformat(), "2026-07-13T17:05:00+00:00")
    def test_trade_receipt_is_exact_snapshot_scoped_and_manual_only(self):
        result = {
            "snapshot_id": 281,
            "letter_grade": "B+", "fairness": 0.82, "my_delta": 1.5,
            "their_delta": -1.5, "age_delta": -2.0,
            "my_give_fppg": 4.0, "my_get_fppg": 5.5,
            "value_basis": "current_snapshot_fppg", "grade_scope": "current_rate_only",
            "dynasty_complete": False,
            "my_give": [{"id": "mine", "name": "Mine", "team": "NYY", "positions": "OF", "fppg": 4.0, "age": 30}],
            "my_get": [{"id": "theirs", "name": "Theirs", "team": "SEA", "positions": "2B", "fppg": 5.5, "age": 28}],
            "analysis": {"horizons": [
                {"key": "current_rate", "label": "Current rate", "status": "modeled", "value": 1.5, "unit": "FP/G", "detail": "Current snapshot."},
                {"key": "rest_of_season", "label": "Rest of season", "status": "unavailable", "value": None, "detail": "Not modeled."},
            ]},
            "eligibility_evidence": {
                "policy_version": "trade_eligibility_v2", "all_checks_passed": True,
                "participants": [
                    {"side": "give", "player_id": "mine", "slot": "OF", "age": 30, "age_source": "fantrax", "protected_trade_player": False, "available_for_current_rate_grade": True, "requires_manual_dynasty_review": False, "fppg_valid": True},
                    {"side": "get", "player_id": "theirs", "slot": "2B", "age": 28, "age_source": "fantrax", "protected_trade_player": False, "available_for_current_rate_grade": True, "requires_manual_dynasty_review": False, "fppg_valid": True},
                ],
            },
        }
        snapshot = {"id": 281, "taken_at": "2026-07-12T14:00:00Z", "league_id": "league", "team_id": "team"}
        receipt = sandlot_receipts.build_trade_assessment_receipt(snapshot=snapshot, result=result, generated_at=NOW)
        replay = sandlot_receipts.build_trade_assessment_receipt(snapshot=snapshot, result=result, generated_at=NOW)

        self.assertEqual(receipt["receipt_id"], replay["receipt_id"])
        self.assertEqual(receipt["source"], "trade_cockpit")
        self.assertEqual(receipt["action_type"], "trade_assessment")
        self.assertIn(":281:", receipt["scope_key"])
        self.assertEqual(receipt["projected_gain"], 1.5)
        self.assertEqual(receipt["baseline_value"], 4.0)
        self.assertEqual(receipt["projected_value"], 5.5)
        self.assertEqual(receipt["expires_at"], NOW + timedelta(hours=24))
        self.assertTrue(receipt["recommendation"]["guardrails"]["manual_execution_only"])
        self.assertFalse(receipt["recommendation"]["guardrails"]["fantrax_write_authorized"])
        self.assertFalse(receipt["recommendation"]["guardrails"]["dynasty_complete"])
        self.assertEqual(receipt["recommendation"]["guardrails"]["eligibility_policy_version"], "trade_eligibility_v2")
        self.assertTrue(all(
            participant["available_for_current_rate_grade"]
            for participant in receipt["recommendation"]["guardrails"]["eligibility"]
        ))
        self.assertEqual(receipt["builder_version"], "trade_assessment_v4")
        self.assertFalse(receipt["recommendation"]["outcome_contract"]["eligible"])
        self.assertIn(
            {"code": "period_calendar_missing"},
            receipt["recommendation"]["outcome_contract"]["blocking_reasons"],
        )
        self.assertEqual(receipt["recommendation"]["origin"], {
            "kind": "manual_entry",
            "fantrax_trade_id": None,
            "snapshot_id": 281,
            "proposed_by_team_id": None,
            "proposed_at_label": None,
            "scheduled_execution_at_label": None,
            "source_status": "manual_unbound",
            "execution_verification": "unverified",
        })

        incoming = sandlot_receipts.build_trade_assessment_receipt(
            snapshot=snapshot,
            result=result,
            generated_at=NOW,
            origin={
                "trade_id": "tx1",
                "snapshot_id": 281,
                "proposed_by_team_id": "other",
                "proposed_at_label": "Jul 12",
                "scheduled_execution_at_label": "Jul 13",
            },
        )
        self.assertNotEqual(incoming["receipt_id"], receipt["receipt_id"])
        self.assertEqual(incoming["recommendation"]["origin"], {
            "kind": "incoming_fantrax_offer",
            "fantrax_trade_id": "tx1",
            "snapshot_id": 281,
            "proposed_by_team_id": "other",
            "proposed_at_label": "Jul 12",
            "scheduled_execution_at_label": "Jul 13",
            "source_status": "pending",
            "execution_verification": "unverified",
        })
        with self.assertRaisesRegex(ValueError, "origin snapshot does not match"):
            sandlot_receipts.build_trade_assessment_receipt(
                snapshot=snapshot,
                result=result,
                generated_at=NOW,
                origin={"trade_id": "tx1", "snapshot_id": 999, "proposed_by_team_id": "other"},
            )

        newer = sandlot_receipts.build_trade_assessment_receipt(snapshot={**snapshot, "id": 282}, result={**result, "snapshot_id": 282}, generated_at=NOW)
        self.assertNotEqual(newer["scope_key"], receipt["scope_key"])
        self.assertNotEqual(newer["receipt_id"], receipt["receipt_id"])

        with self.assertRaisesRegex(ValueError, "does not match"):
            sandlot_receipts.build_trade_assessment_receipt(snapshot={**snapshot, "id": 999}, result=result, generated_at=NOW)
        overlap = copy.deepcopy(result)
        overlap["my_get"][0]["id"] = "mine"
        with self.assertRaisesRegex(ValueError, "disjoint"):
            sandlot_receipts.build_trade_assessment_receipt(snapshot=snapshot, result=overlap, generated_at=NOW)

        unavailable = copy.deepcopy(result)
        unavailable["eligibility_evidence"]["participants"][1]["available_for_current_rate_grade"] = False
        with self.assertRaisesRegex(ValueError, "did not pass all participant gates"):
            sandlot_receipts.build_trade_assessment_receipt(
                snapshot=snapshot, result=unavailable, generated_at=NOW,
            )

        for unsupported_policy in ("trade_eligibility_v1", "unknown"):
            with self.subTest(unsupported_policy=unsupported_policy):
                unsupported = copy.deepcopy(result)
                unsupported["eligibility_evidence"]["policy_version"] = unsupported_policy
                with self.assertRaisesRegex(ValueError, "policy version is unsupported"):
                    sandlot_receipts.build_trade_assessment_receipt(
                        snapshot=snapshot, result=unsupported, generated_at=NOW,
                    )

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
                "unfilled": list(DEFAULT_UNFILLED),
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


class RecommendationOutcomeBuilderTests(unittest.TestCase):
    def completed_snapshot(self, **matchup_overrides):
        matchup = {
            "source": "fantrax_schedule",
            "complete": True,
            "start": "2026-07-13",
            "end": "2026-07-19",
            "period_number": 17,
            "matchup_key": "week-17-team-opponent",
            "my_team_id": "team",
            "my_score": 188.5,
            "score_state": "live_or_final",
            **matchup_overrides,
        }
        return {"league_id": "league", "team_id": "team", "matchup": matchup}

    def test_scores_exact_completed_team_result_without_claiming_gain(self):
        outcome = sandlot_receipts.build_team_result_outcome(
            receipt=receipt_fixture(),
            snapshot=self.completed_snapshot(),
            snapshot_id=300,
            snapshot_taken_at="2026-07-20T12:00:00Z",
        )

        self.assertEqual(outcome["scoring_version"], "team_result_v1")
        self.assertEqual(outcome["actual_value"], 188.5)
        self.assertIsNone(outcome["actual_baseline"])
        self.assertIsNone(outcome["actual_gain"])
        evidence = outcome["outcome_evidence"]
        self.assertEqual(evidence["measurement_scope"], "observed_team_total")
        self.assertEqual(evidence["adherence_state"], "unverified")
        self.assertEqual(evidence["counterfactual_state"], "unavailable")
        self.assertEqual(evidence["team_total_residual"], 156.1543)
        self.assertEqual(len(evidence["evidence_hash"]), 64)

    def test_incomplete_or_wrong_period_remains_pending(self):
        receipt = receipt_fixture()

        self.assertIsNone(sandlot_receipts.build_team_result_outcome(
            receipt=receipt,
            snapshot=self.completed_snapshot(complete=False),
            snapshot_id=300,
            snapshot_taken_at="2026-07-20T12:00:00Z",
        ))
        self.assertIsNone(sandlot_receipts.build_team_result_outcome(
            receipt=receipt,
            snapshot=self.completed_snapshot(start="2026-07-20", end="2026-07-26"),
            snapshot_id=300,
            snapshot_taken_at="2026-07-27T12:00:00Z",
        ))

    def test_latest_completed_is_matched_by_exact_dates(self):
        snapshot = self.completed_snapshot(start="2026-07-20", end="2026-07-26", complete=False)
        snapshot["matchup"]["latest_completed"] = self.completed_snapshot()["matchup"]

        outcome = sandlot_receipts.build_team_result_outcome(
            receipt=receipt_fixture(),
            snapshot=snapshot,
            snapshot_id=301,
            snapshot_taken_at="2026-07-20T12:00:00Z",
        )

        self.assertEqual(outcome["actual_value"], 188.5)

    def test_nonfinite_score_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "completed team score must be finite"):
            sandlot_receipts.build_team_result_outcome(
                receipt=receipt_fixture(),
                snapshot=self.completed_snapshot(my_score=float("nan")),
                snapshot_id=300,
                snapshot_taken_at="2026-07-20T12:00:00Z",
            )

    def test_missing_team_wrong_source_and_nonfinal_score_fail_closed(self):
        cases = [
            ({"my_team_id": None}, "team does not match"),
            ({"source": "unknown"}, "source is not authoritative"),
            ({"score_state": "invalid_future_score"}, "score is not final"),
            ({"matchup_key": None}, "matchup key is required"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                sandlot_receipts.build_team_result_outcome(
                    receipt=receipt_fixture(),
                    snapshot=self.completed_snapshot(**overrides),
                    snapshot_id=300,
                    snapshot_taken_at="2026-07-20T12:00:00Z",
                )

    def test_duplicate_identity_with_conflicting_score_fails_closed(self):
        snapshot = self.completed_snapshot()
        snapshot["matchup"]["latest_completed"] = {
            **snapshot["matchup"],
            "my_score": 190.0,
        }

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            sandlot_receipts.build_team_result_outcome(
                receipt=receipt_fixture(),
                snapshot=snapshot,
                snapshot_id=300,
                snapshot_taken_at="2026-07-20T12:00:00Z",
            )

    def test_missed_result_terminalizes_only_after_grace_and_newer_final_period(self):
        receipt = receipt_fixture()
        snapshot = self.completed_snapshot(start="2026-07-20", end="2026-07-26")
        snapshot["matchup"] = {
            **snapshot["matchup"],
            "complete": False,
            "latest_completed": {
                **snapshot["matchup"],
                "complete": True,
                "start": "2026-07-20",
                "end": "2026-07-26",
            },
        }

        self.assertIsNone(sandlot_receipts.build_team_result_unavailable(
            receipt=receipt,
            snapshot=snapshot,
            snapshot_id=310,
            snapshot_taken_at="2026-07-27T12:00:00Z",
        ))
        unavailable = sandlot_receipts.build_team_result_unavailable(
            receipt=receipt,
            snapshot=snapshot,
            snapshot_id=311,
            snapshot_taken_at="2026-07-28T12:00:00Z",
        )
        self.assertEqual(unavailable["reason"], "completed_period_evidence_missed_after_grace_window")
        self.assertFalse(unavailable["retryable"])
        self.assertEqual(len(unavailable["evidence_hash"]), 64)


class CounterfactualLineupEvaluationTests(unittest.TestCase):
    def test_refresh_appends_counterfactual_even_after_legacy_team_result_scored(self):
        receipt = {
            **receipt_fixture(),
            "decision_state": "accepted",
            "outcome_state": "scored",
            "scoring_version": "team_result_v1",
        }
        receipt["period_evidence"] = period_evidence_fixture(receipt)
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}),
            patch.object(sandlot_db, "receipts_missing_outcome_evaluation", return_value=[receipt]) as missing,
            patch.object(sandlot_db, "record_recommendation_outcome_evaluation") as record,
            patch.object(sandlot_db, "pending_recommendation_receipts", return_value=[]),
        ):
            sandlot_refresh._persist_recommendation_outcomes(
                301, {"timestamp": "2026-07-20T12:00:00Z"}
            )

        missing.assert_called_once_with(
            source="monday_lineup",
            scoring_version="counterfactual_lineup_v1",
            evidence_version="fantrax_period_lineup_v2",
        )
        evaluation = record.call_args.kwargs["evaluation"]
        self.assertEqual(evaluation["metrics"]["counterfactual_gain"], 13.0)
        self.assertFalse(evaluation["evidence"]["autopilot_eligible"])

    def test_refresh_terminalizes_immutable_incompatibility_without_enabling_autopilot(self):
        receipt = receipt_fixture()
        receipt["period_evidence"] = period_evidence_fixture(receipt)
        record = Mock()
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}),
            patch.object(sandlot_db, "receipts_missing_outcome_evaluation", return_value=[receipt]),
            patch.object(
                sandlot_receipts,
                "build_counterfactual_lineup_evaluation",
                side_effect=ValueError("missing archived player"),
            ),
            patch.object(sandlot_db, "record_recommendation_outcome_evaluation", record),
            patch.object(sandlot_db, "pending_recommendation_receipts", return_value=[]),
        ):
            sandlot_refresh._persist_recommendation_outcomes(
                301, {"timestamp": "2026-07-20T12:00:00Z"}
            )

        unavailable = record.call_args.kwargs["evaluation"]
        self.assertEqual(unavailable["state"], "unavailable")
        self.assertEqual(unavailable["metrics"], {})
        self.assertFalse(unavailable["evidence"]["retryable"])
        self.assertFalse(unavailable["evidence"]["autopilot_eligible"])

    def test_refresh_does_not_terminalize_database_writer_failure(self):
        receipt = receipt_fixture()
        receipt["period_evidence"] = period_evidence_fixture(receipt)
        record = Mock(side_effect=ValueError("database validation failed"))
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}),
            patch.object(sandlot_db, "receipts_missing_outcome_evaluation", return_value=[receipt]),
            patch.object(sandlot_db, "record_recommendation_outcome_evaluation", record),
            patch.object(sandlot_db, "pending_recommendation_receipts", return_value=[]),
        ):
            sandlot_refresh._persist_recommendation_outcomes(
                301, {"timestamp": "2026-07-20T12:00:00Z"}
            )

        self.assertEqual(record.call_count, 1)
        self.assertEqual(record.call_args.kwargs["evaluation"]["state"], "scored")

    def test_repeated_real_slot_labels_and_partially_unfilled_type_are_valid(self):
        base = receipt_fixture()
        entries = [
            {
                "id": item["id"], "name": item["name"], "tokens": set(item["tokens"]),
                "proj": item["projected_points"],
                "hitter_proj": item["hitter_projected_points"],
                "pitcher_proj": item["pitcher_projected_points"],
                "basis": item["basis"], "slot": item["slot"],
                "slot_source": item["slot_source"], "injury": item["injury"],
            }
            for item in base["recommendation"]["projection_inputs"]
        ]
        entries.append({
            "id": "bat-2", "name": "Second Bat", "tokens": {"OF", "UT"},
            "proj": 5.0, "hitter_proj": 5.0, "pitcher_proj": 0.0,
            "basis": "test", "slot": "RES", "slot_source": "raw.statusId", "injury": None,
        })
        receipt = receipt_fixture(entries=entries, result={
            "lineup": [("SP", "Two Way"), ("OF", "Bench Bat"), ("OF", "Second Bat")],
            "projected_total": 37.34567,
            "unfilled": (
                ["C", "1B", "2B", "3B", "SS", "OF"]
                + ["UT"] * 3
                + ["SP"] * 5
                + ["RP"] * 3
            ),
        })
        archive = period_evidence_fixture(receipt)
        archive["players"].append({
            "player_id": "bat-2", "player_name": "Second Bat", "scoring_role": "hitter",
            "slot": "OF", "slot_source": "raw.posId", "raw_pos_id": "012", "raw_status_id": "1",
            "eligibility_pos_ids": ["012", "014"], "period_fpts": "5", "period_fpts_source": "SCORE:fpts",
        })
        archive["participation"]["days"][0]["players"].append({
            "player_id": "bat-2", "scoring_role": "hitter", "state": "active", "raw_pos_id": "012",
        })
        archive["observed_team_total"] = "39"
        archive["participation"]["observed_team_total"] = "39"
        archive["evidence_hash"] = fantrax_data.lineup_period_evidence_hash(archive)

        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=receipt, period_evidence=archive
        )

        self.assertEqual(evaluation["metrics"]["counterfactual_proposed_total"], 39.0)
        self.assertEqual(Counter(evaluation["evidence"]["unfilled_slots"]), Counter({
            "C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1,
            "OF": 1, "UT": 3, "SP": 5, "RP": 3,
        }))

    def test_scores_static_baseline_and_proposal_separately_from_observed_total(self):
        receipt = {**receipt_fixture(), "decision_state": "accepted"}
        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=receipt, period_evidence=period_evidence_fixture(receipt)
        )

        self.assertEqual(evaluation["scoring_version"], "counterfactual_lineup_v1")
        self.assertEqual(evaluation["metrics"], {
            "counterfactual_baseline_total": 21.0,
            "counterfactual_proposed_total": 34.0,
            "counterfactual_gain": 13.0,
            "observed_team_total": 34.0,
        })
        evidence = evaluation["evidence"]
        self.assertEqual(evidence["actual_assignment_match"], "proposed")
        self.assertEqual(evidence["actual_slot_match"], "proposed")
        self.assertEqual(evidence["decision_alignment"], "accepted_proposal_observed")
        self.assertFalse(evidence["causal_lift_claimed"])
        self.assertFalse(evidence["plan_execution_claimed"])
        self.assertFalse(evidence["autopilot_eligible"])
        self.assertEqual(len(evidence["evidence_hash"]), 64)

    def test_pending_or_rejected_coincidence_is_not_decision_alignment(self):
        for state in ("pending", "rejected"):
            with self.subTest(state=state):
                receipt = {**receipt_fixture(), "decision_state": state}
                evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
                    receipt=receipt, period_evidence=period_evidence_fixture(receipt)
                )
                self.assertEqual(evaluation["evidence"]["actual_assignment_match"], "proposed")
                self.assertEqual(evaluation["evidence"]["decision_alignment"], "not_established")

    def test_baseline_actual_is_classified_without_rewriting_counterfactual(self):
        receipt = {**receipt_fixture(), "decision_state": "accepted"}
        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=receipt, period_evidence=period_evidence_fixture(receipt, actual="baseline")
        )

        self.assertEqual(evaluation["evidence"]["actual_assignment_match"], "baseline")
        self.assertEqual(evaluation["metrics"]["counterfactual_gain"], 13.0)
        self.assertEqual(evaluation["metrics"]["observed_team_total"], 21.0)
        self.assertEqual(evaluation["evidence"]["decision_alignment"], "not_established")

    def test_two_way_player_is_scored_by_slot_role_not_name(self):
        receipt = receipt_fixture()
        archive = period_evidence_fixture(receipt)
        archive["players"].append({
            "player_id": "two-way", "player_name": "Two Way", "scoring_role": "hitter",
            "slot": "RES", "slot_source": "raw.posId", "raw_pos_id": "012", "raw_status_id": "2",
            "eligibility_pos_ids": ["012"], "period_fpts": "99", "period_fpts_source": "SCORE:fpts",
        })
        archive["participation"]["days"][0]["players"].append({
            "player_id": "two-way", "scoring_role": "hitter", "state": "bench", "raw_pos_id": "012",
        })
        archive["evidence_hash"] = fantrax_data.lineup_period_evidence_hash(archive)

        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=receipt, period_evidence=archive
        )

        self.assertEqual(evaluation["metrics"]["counterfactual_proposed_total"], 34.0)

    def test_incomplete_illegal_or_multiwindow_evidence_fails_closed(self):
        receipt = receipt_fixture()
        cases = []
        missing = period_evidence_fixture(receipt)
        missing["players"] = [item for item in missing["players"] if item["player_id"] != "bat"]
        missing["evidence_hash"] = fantrax_data.lineup_period_evidence_hash(missing)
        cases.append((missing, "absent from archived"))
        illegal = period_evidence_fixture(receipt)
        illegal["players"][0]["eligibility_pos_ids"] = ["003"]
        illegal["evidence_hash"] = fantrax_data.lineup_period_evidence_hash(illegal)
        cases.append((illegal, "not archive-eligible"))
        multiwindow = period_evidence_fixture(receipt, capability=False)
        cases.append((multiwindow, "not counterfactual eligible"))
        for archive, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                sandlot_receipts.build_counterfactual_lineup_evaluation(
                    receipt=receipt, period_evidence=archive
                )

    def test_proposal_and_unfilled_counts_must_equal_full_league_template(self):
        receipt = receipt_fixture()
        receipt["recommendation"]["unfilled_slots"].pop()

        with self.assertRaisesRegex(ValueError, "do not match the league active template"):
            sandlot_receipts.build_counterfactual_lineup_evaluation(
                receipt=receipt, period_evidence=period_evidence_fixture(receipt)
            )


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
        self.assertIn("CREATE TABLE IF NOT EXISTS recommendation_outcome_evaluations", sql)
        self.assertIn("PRIMARY KEY (receipt_id, scoring_version)", sql)
        self.assertIn("snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL", sql)
        self.assertIn("CHECK (lifecycle_state IN ('active', 'superseded', 'expired'))", sql)
        self.assertIn("CHECK (decision_state IN ('pending', 'accepted', 'rejected'))", sql)
        self.assertIn("WHERE lifecycle_state = 'active'", sql)
        self.assertIn("r.expires_at <= clock_timestamp()", inspect.getsource(
            sandlot_db.receipts_missing_outcome_evaluation
        ))

    def test_learning_report_deduplicates_periods_and_selects_only_scalar_details(self):
        calls = []

        class Result:
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
                if "count(*) AS evaluated" in sql:
                    return Result(one={"evaluated": 1, "scored": 1})
                return Result(many=[{
                    "state": "scored", "counterfactual_gain": 3.0,
                    "actual_assignment_match": "proposed",
                }])

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            report = sandlot_db.recommendation_outcome_evaluation_report(
                source="monday_lineup", scoring_version="counterfactual_lineup_v1", detail_limit=8
            )

        self.assertEqual(report["summary"]["evaluated"], 1)
        self.assertEqual(report["items"][0]["counterfactual_gain"], 3.0)
        combined_sql = "\n".join(sql for sql, _params in calls)
        self.assertIn(
            "PARTITION BY r.league_id, r.team_id, r.period_start, r.period_end",
            combined_sql,
        )
        self.assertNotIn("SELECT e.*", combined_sql)
        self.assertNotIn("SELECT * FROM samples", calls[1][0])
        self.assertEqual(calls[1][1], ("monday_lineup", "counterfactual_lineup_v1", 8))

    def test_counterfactual_evaluation_appends_without_touching_legacy_outcome(self):
        receipt = {
            **receipt_fixture(),
            "decision_state": "accepted",
            "outcome_state": "scored",
            "scoring_version": "team_result_v1",
            "actual_value": 34.0,
        }
        archive = period_evidence_fixture(receipt)
        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=receipt, period_evidence=archive
        )
        calls = []

        class Result:
            def __init__(self, row):
                self.row = row
            def fetchone(self):
                return self.row

        inserted = {
            "receipt_id": receipt["receipt_id"],
            "scoring_version": evaluation["scoring_version"],
            "state": "scored",
            "source_evidence_version": evaluation["source_evidence_version"],
            "source_evidence_hash": evaluation["source_evidence_hash"],
            "evidence_hash": evaluation["evidence"]["evidence_hash"],
            "metrics": evaluation["metrics"],
            "evidence": evaluation["evidence"],
        }

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                if "FROM recommendation_receipts" in sql:
                    return Result(receipt)
                if "SELECT evidence_hash FROM lineup_period_evidence" in sql:
                    return Result({"evidence_hash": archive["evidence_hash"]})
                if "INSERT INTO recommendation_outcome_evaluations" in sql:
                    return Result(inserted)
                raise AssertionError(sql)

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            row, changed = sandlot_db.record_recommendation_outcome_evaluation(
                receipt_id=receipt["receipt_id"], evaluation=evaluation
            )

        self.assertTrue(changed)
        self.assertEqual(row["metrics"]["counterfactual_gain"], 13.0)
        self.assertFalse(any("UPDATE recommendation_receipts" in sql for sql, _ in calls))

    def test_counterfactual_evaluation_replay_is_noop_and_conflict_fails(self):
        receipt = {**receipt_fixture(), "decision_state": "accepted"}
        archive = period_evidence_fixture(receipt)
        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=receipt, period_evidence=archive
        )
        existing = {
            "receipt_id": receipt["receipt_id"], "scoring_version": evaluation["scoring_version"],
            "state": evaluation["state"], "source_evidence_version": evaluation["source_evidence_version"],
            "source_evidence_hash": evaluation["source_evidence_hash"],
            "evidence_hash": evaluation["evidence"]["evidence_hash"],
            "metrics": evaluation["metrics"], "evidence": evaluation["evidence"],
        }

        class Result:
            def __init__(self, row):
                self.row = row
            def fetchone(self):
                return self.row

        class FakeConn:
            def __init__(self, conflict=False):
                self.conflict = conflict
            def execute(self, sql, params=None):
                if "FROM recommendation_receipts" in sql:
                    return Result(receipt)
                if "SELECT evidence_hash FROM lineup_period_evidence" in sql:
                    return Result({"evidence_hash": archive["evidence_hash"]})
                if "INSERT INTO recommendation_outcome_evaluations" in sql:
                    return Result(None)
                if "SELECT * FROM recommendation_outcome_evaluations" in sql:
                    return Result({**existing, "evidence_hash": "b" * 64} if self.conflict else existing)
                raise AssertionError(sql)

        @contextmanager
        def replay_connect():
            yield FakeConn()
        with patch.object(sandlot_db, "connect", replay_connect):
            _row, changed = sandlot_db.record_recommendation_outcome_evaluation(
                receipt_id=receipt["receipt_id"], evaluation=evaluation
            )
        self.assertFalse(changed)

        @contextmanager
        def conflict_connect():
            yield FakeConn(conflict=True)
        with patch.object(sandlot_db, "connect", conflict_connect):
            with self.assertRaisesRegex(ValueError, "different immutable evidence"):
                sandlot_db.record_recommendation_outcome_evaluation(
                    receipt_id=receipt["receipt_id"], evaluation=evaluation
                )

    def test_counterfactual_writer_rejects_contradictory_embedded_lineage(self):
        receipt = receipt_fixture()
        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=receipt, period_evidence=period_evidence_fixture(receipt)
        )
        evaluation["evidence"]["source_evidence"]["hash"] = "b" * 64
        evaluation["evidence"]["evidence_hash"] = sandlot_receipts.counterfactual_evidence_hash(
            evaluation["evidence"]
        )

        with self.assertRaisesRegex(ValueError, "source lineage is contradictory"):
            sandlot_db.record_recommendation_outcome_evaluation(
                receipt_id=receipt["receipt_id"], evaluation=evaluation
            )

    def test_counterfactual_writer_rejects_stale_decision_alignment(self):
        evaluated_receipt = {**receipt_fixture(), "decision_state": "accepted"}
        locked_receipt = {**evaluated_receipt, "decision_state": "rejected"}
        archive = period_evidence_fixture(evaluated_receipt)
        evaluation = sandlot_receipts.build_counterfactual_lineup_evaluation(
            receipt=evaluated_receipt, period_evidence=archive
        )

        class Result:
            def fetchone(self):
                return locked_receipt
        class FakeConn:
            def execute(self, sql, params=None):
                return Result()
        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            with self.assertRaisesRegex(ValueError, "decision state is stale"):
                sandlot_db.record_recommendation_outcome_evaluation(
                    receipt_id=evaluated_receipt["receipt_id"], evaluation=evaluation
                )

    def test_counterfactual_writer_persists_terminal_unavailable_state(self):
        receipt = receipt_fixture()
        archive = period_evidence_fixture(receipt)
        evaluation = sandlot_receipts.build_counterfactual_lineup_unavailable(
            receipt=receipt, period_evidence=archive, detail="missing archived player"
        )

        class Result:
            def __init__(self, row):
                self.row = row
            def fetchone(self):
                return self.row

        class FakeConn:
            def execute(self, sql, params=None):
                if "FROM recommendation_receipts" in sql:
                    return Result(receipt)
                if "SELECT evidence_hash FROM lineup_period_evidence" in sql:
                    return Result({"evidence_hash": archive["evidence_hash"]})
                if "INSERT INTO recommendation_outcome_evaluations" in sql:
                    return Result({
                        "receipt_id": receipt["receipt_id"],
                        "scoring_version": evaluation["scoring_version"],
                        "state": "unavailable",
                        "metrics": {},
                        "evidence": evaluation["evidence"],
                    })
                raise AssertionError(sql)

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            row, changed = sandlot_db.record_recommendation_outcome_evaluation(
                receipt_id=receipt["receipt_id"], evaluation=evaluation
            )

        self.assertTrue(changed)
        self.assertEqual(row["state"], "unavailable")

    def test_team_result_outcome_is_idempotent_and_counterfactual_fields_stay_null(self):
        receipt = {**receipt_fixture(), "outcome_state": "pending"}
        outcome = sandlot_receipts.build_team_result_outcome(
            receipt=receipt,
            snapshot={
                "league_id": "league",
                "team_id": "team",
                "matchup": {
                    "complete": True,
                    "source": "fantrax_schedule",
                    "start": "2026-07-13",
                    "end": "2026-07-19",
                    "period_number": 17,
                    "matchup_key": "week-17",
                    "my_team_id": "team",
                    "my_score": 188.5,
                    "score_state": "live_or_final",
                },
            },
            snapshot_id=300,
            snapshot_taken_at="2026-07-20T12:00:00Z",
        )
        calls = []

        class Result:
            def __init__(self, row):
                self.row = row
            def fetchone(self):
                return self.row

        scored = {
            **receipt,
            "outcome_state": "scored",
            "scoring_version": outcome["scoring_version"],
            "actual_value": outcome["actual_value"],
            "actual_baseline": None,
            "actual_gain": None,
            "outcome_evidence": outcome["outcome_evidence"],
        }

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                if "FOR UPDATE" in sql:
                    return Result(receipt)
                if "UPDATE recommendation_receipts" in sql:
                    return Result(scored)
                raise AssertionError(sql)

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            row, changed = sandlot_db.score_recommendation_receipt_team_result(
                receipt_id=receipt["receipt_id"], outcome=outcome
            )
        self.assertTrue(changed)
        self.assertEqual(row["actual_value"], 188.5)
        update_sql = next(sql for sql, _ in calls if "UPDATE recommendation_receipts" in sql)
        self.assertIn("actual_baseline = NULL", update_sql)
        self.assertIn("actual_gain = NULL", update_sql)
        self.assertIn("WHERE receipt_id = %s AND outcome_state = 'pending'", update_sql)

        class ReplayConn:
            def execute(self, sql, params=None):
                self.assert_for_update = sql
                return Result(scored)

        @contextmanager
        def replay_connect():
            yield ReplayConn()

        with patch.object(sandlot_db, "connect", replay_connect):
            replayed, changed = sandlot_db.score_recommendation_receipt_team_result(
                receipt_id=receipt["receipt_id"], outcome=outcome
            )
        self.assertFalse(changed)
        self.assertEqual(replayed["outcome_evidence"], outcome["outcome_evidence"])

    def test_team_result_rejects_different_evidence_after_scoring(self):
        outcome = {
            "scoring_version": "team_result_v1",
            "actual_value": 100.0,
            "actual_baseline": None,
            "actual_gain": None,
            "outcome_evidence": {
                "measurement_scope": "observed_team_total",
                "adherence_state": "unverified",
                "counterfactual_state": "unavailable",
                "counterfactual_reason": "per_player_period_scoring_and_lineup_participation_not_ingested",
            },
        }
        outcome["outcome_evidence"]["evidence_hash"] = sandlot_receipts.team_result_evidence_hash(
            outcome["outcome_evidence"]
        )
        existing = {
            "outcome_state": "scored",
            "scoring_version": "team_result_v1",
            "actual_value": 99.0,
            "actual_baseline": None,
            "actual_gain": None,
            "outcome_evidence": {"evidence_hash": "old"},
        }

        class Result:
            def fetchone(self):
                return existing
        class FakeConn:
            def execute(self, sql, params=None):
                return Result()
        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            with self.assertRaisesRegex(ValueError, "different outcome evidence"):
                sandlot_db.score_recommendation_receipt_team_result(receipt_id="receipt", outcome=outcome)

    def test_team_result_writer_rejects_invalid_hash_labels_and_value(self):
        base = {
            "scoring_version": "team_result_v1",
            "actual_value": 100.0,
            "actual_baseline": None,
            "actual_gain": None,
            "outcome_evidence": {
                "measurement_scope": "observed_team_total",
                "adherence_state": "unverified",
                "counterfactual_state": "unavailable",
                "counterfactual_reason": "per_player_period_scoring_and_lineup_participation_not_ingested",
            },
        }
        cases = [
            ({**base, "outcome_evidence": {**base["outcome_evidence"], "evidence_hash": "x"}}, "lowercase SHA-256"),
            ({**base, "actual_value": float("nan")}, "must be finite"),
            ({
                **base,
                "outcome_evidence": {
                    **base["outcome_evidence"],
                    "adherence_state": "verified",
                },
            }, "fixed non-counterfactual"),
        ]
        for candidate, message in cases:
            evidence = candidate["outcome_evidence"]
            if "evidence_hash" not in evidence:
                evidence["evidence_hash"] = sandlot_receipts.team_result_evidence_hash(evidence)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                sandlot_db.score_recommendation_receipt_team_result(
                    receipt_id="receipt", outcome=candidate
                )

    def test_team_result_writer_rejects_valid_outcome_for_different_receipt(self):
        receipt_a = {**receipt_fixture(), "outcome_state": "pending"}
        outcome = sandlot_receipts.build_team_result_outcome(
            receipt=receipt_a,
            snapshot={
                "league_id": "league",
                "team_id": "team",
                "matchup": {
                    "source": "fantrax_schedule",
                    "complete": True,
                    "start": "2026-07-13",
                    "end": "2026-07-19",
                    "period_number": 17,
                    "matchup_key": "week-17",
                    "my_team_id": "team",
                    "my_score": 188.5,
                    "score_state": "live_or_final",
                },
            },
            snapshot_id=300,
            snapshot_taken_at="2026-07-20T12:00:00Z",
        )
        receipt_b = {**receipt_a, "receipt_id": "monday-lineup:" + "b" * 64}

        class Result:
            def fetchone(self):
                return receipt_b
        class FakeConn:
            def execute(self, sql, params=None):
                return Result()
        @contextmanager
        def fake_connect():
            yield FakeConn()

        with patch.object(sandlot_db, "connect", fake_connect):
            with self.assertRaisesRegex(ValueError, "does not match the target receipt"):
                sandlot_db.score_recommendation_receipt_team_result(
                    receipt_id=receipt_b["receipt_id"], outcome=outcome
                )

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
            yield FakeConn([receipt, {"current_time": NOW}, updated])

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
        update_sql, update_params = calls[2]
        self.assertIn("decision_state = 'pending'", update_sql)
        self.assertIn("expires_at > clock_timestamp()", update_sql)
        self.assertEqual(calls[1][0], "SELECT clock_timestamp() AS current_time")
        self.assertIn("decided_at = clock_timestamp()", update_sql)
        self.assertEqual(update_params[:3], ("accepted", "owner_bridge", "Using it"))

        replay = dict(updated)

        @contextmanager
        def replay_connect():
            yield FakeConn([replay, {"current_time": NOW}])

        with patch.object(sandlot_db, "connect", replay_connect):
            row, changed = sandlot_db.decide_recommendation_receipt(
                receipt_id=receipt["receipt_id"],
                input_hash=receipt["input_hash"],
                decision="accepted",
                source="owner_bridge",
            )
        self.assertFalse(changed)

    def test_decision_rejects_missing_stale_expired_superseded_and_conflicting_receipts(self):
        base = {
            **receipt_fixture(),
            "lifecycle_state": "active",
            "decision_state": "pending",
        }

        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        @contextmanager
        def fake_connect(row, current_time=NOW):
            class FakeConn:
                def __init__(self):
                    self.rows = [row, {"current_time": current_time}]

                def execute(self, _sql, _params=None):
                    return Result(self.rows.pop(0))
            yield FakeConn()

        cases = [
            (None, LookupError, "not found", base["input_hash"], "accepted"),
            (base, ValueError, "stale or mismatched", "f" * 64, "accepted"),
            (base, ValueError, "expired", base["input_hash"], "accepted", base["expires_at"] + timedelta(seconds=1)),
            ({**base, "lifecycle_state": "superseded"}, ValueError, "no longer active", base["input_hash"], "accepted", NOW),
            ({**base, "decision_state": "rejected"}, ValueError, "already rejected", base["input_hash"], "accepted", NOW),
        ]
        for case in cases:
            stored, exception, message, input_hash, decision, *clock = case
            with self.subTest(message=message), patch.object(
                sandlot_db, "connect", lambda stored=stored, clock=clock: fake_connect(stored, clock[0] if clock else NOW)
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

    def test_trade_grade_persists_and_returns_sanitized_exact_receipt(self):
        snapshot = {"id": 281, "taken_at": "2026-07-12T14:00:00Z", "league_id": "league", "team_id": "team"}
        result = {
            "snapshot_id": 281,
            "letter_grade": "B+", "fairness": 0.82, "my_delta": 1.5,
            "their_delta": -1.5, "age_delta": -2.0,
            "my_give_fppg": 4.0, "my_get_fppg": 5.5,
            "value_basis": "current_snapshot_fppg", "grade_scope": "current_rate_only",
            "dynasty_complete": False,
            "my_give": [{"id": "mine", "name": "Mine", "team": "NYY", "positions": "OF", "fppg": 4.0, "age": 30}],
            "my_get": [{"id": "theirs", "name": "Theirs", "team": "SEA", "positions": "2B", "fppg": 5.5, "age": 28}],
            "analysis": {"horizons": [{"key": "current_rate", "label": "Current rate", "status": "modeled", "value": 1.5, "unit": "FP/G", "detail": "Current snapshot."}]},
            "eligibility_evidence": {
                "policy_version": "trade_eligibility_v2", "all_checks_passed": True,
                "participants": [
                    {"side": "give", "player_id": "mine", "slot": "OF", "age": 30, "age_source": "fantrax", "protected_trade_player": False, "available_for_current_rate_grade": True, "requires_manual_dynasty_review": False, "fppg_valid": True},
                    {"side": "get", "player_id": "theirs", "slot": "2B", "age": 28, "age_source": "fantrax", "protected_trade_player": False, "available_for_current_rate_grade": True, "requires_manual_dynasty_review": False, "fppg_valid": True},
                ],
            },
        }
        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot),
            patch("sandlot_api.sandlot_trades.grade_offer", return_value=copy.deepcopy(result)),
            patch("sandlot_api.sandlot_db.record_recommendation_receipt") as record,
        ):
            built = sandlot_receipts.build_trade_assessment_receipt(snapshot=snapshot, result=result, generated_at=NOW)
            record.return_value = ({**built, "lifecycle_state": "active", "decision_state": "pending", "outcome_state": "pending"}, True)
            response = self.client.post("/api/trades/grade", json={"give": ["mine"], "get": ["theirs"]})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["receipt"]["action_type"], "trade_assessment")
        self.assertEqual(payload["receipt"]["trade"]["give"][0]["player_id"], "mine")
        self.assertEqual(payload["receipt"]["trade"]["origin"]["kind"], "manual_entry")
        self.assertFalse(payload["receipt"]["trade"]["outcome_contract"]["eligible"])
        self.assertFalse(payload["receipt"]["trade"]["outcome_contract"]["execution_claimed"])
        self.assertFalse(payload["receipt"]["trade"]["outcome_contract"]["dynasty_claimed"])
        self.assertFalse(payload["receipt"]["trade"]["outcome_contract"]["autopilot_eligible"])
        self.assertTrue(payload["receipt"]["trade"]["guardrails"]["manual_execution_only"])
        self.assertFalse(payload["receipt"]["fantrax_changed"])
        self.assertFalse(payload["receipt"]["writes_enabled"])
        self.assertNotIn("recommendation", payload["receipt"])
        persisted = record.call_args.args[0]
        self.assertTrue(persisted["receipt_id"].startswith("trade-assessment:"))
        self.assertEqual(len(persisted["input_hash"]), 64)
        self.assertEqual(persisted["builder_version"], "trade_assessment_v4")
        self.assertFalse(persisted["recommendation"]["outcome_contract"]["eligible"])

    def test_trade_receipt_failure_does_not_expose_database_details(self):
        snapshot = {"id": 281, "taken_at": "2026-07-12T14:00:00Z", "league_id": "league", "team_id": "team"}
        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot),
            patch("sandlot_api.sandlot_trades.grade_offer", return_value={"snapshot_id": 281}),
            patch("sandlot_api.sandlot_receipts.build_trade_assessment_receipt", side_effect=RuntimeError("postgres secret.internal")),
        ):
            response = self.client.post("/api/trades/grade", json={"give": ["mine"], "get": ["theirs"]})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Trade analysis is temporarily unavailable")
        self.assertNotIn("secret.internal", response.text)

    def test_incoming_trades_are_sanitized_exact_and_manual_only(self):
        snapshot = {
            "id": 281, "taken_at": NOW, "team_id": "mine",
            "data": {"team_id": "mine", "pending_trades": [
                {
                    "trade_id": "tx1", "proposed_by_id": "other", "proposed_by": "Other Team",
                    "proposed": "Jul 12", "executed": "Jul 13",
                    "moves": [
                        {"from_team_id": "mine", "to_team_id": "other", "player_id": "p1", "player": "Mine"},
                        {"from_team_id": "other", "to_team_id": "mine", "player_id": "p2", "player": "Theirs"},
                    ],
                    "private_raw": "do not expose",
                },
                {"trade_id": "outbound", "proposed_by_id": "mine", "moves": []},
            ]},
        }
        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot),
            patch("sandlot_api.sandlot_trades.offer_validation_error", return_value=None),
        ):
            response = self.client.get("/api/trades/incoming")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["offers"]), 1)
        offer = payload["offers"][0]
        self.assertEqual(offer["give"], [{"player_id": "p1", "player_name": "Mine"}])
        self.assertEqual(offer["get"], [{"player_id": "p2", "player_name": "Theirs"}])
        self.assertEqual(offer["proposed_by_team_id"], "other")
        self.assertEqual(offer["scheduled_execution_at_label"], "Jul 13")
        self.assertNotIn("executes_at", offer)
        self.assertTrue(offer["gradeable"])
        self.assertTrue(offer["manual_only"])
        self.assertFalse(payload["fantrax_changed"])
        self.assertFalse(payload["writes_enabled"])
        self.assertNotIn("private_raw", response.text)

    def test_incoming_trade_with_draft_pick_fails_closed(self):
        snapshot = {
            "id": 281, "taken_at": NOW, "team_id": "mine",
            "data": {"team_id": "mine", "pending_trades": [{
                "trade_id": "tx-pick", "proposed_by_id": "other", "moves": [
                    {"from_team_id": "mine", "to_team_id": "other", "player_id": "p1", "player": "Mine"},
                    {"from_team_id": "other", "to_team_id": "mine", "draft_pick": {"round": 1}},
                ],
            }]},
        }
        with patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot):
            offer = self.client.get("/api/trades/incoming").json()["offers"][0]

        self.assertFalse(offer["gradeable"])
        self.assertIn("draft_pick", offer["blocked_reasons"])
        self.assertIn("missing_get_side", offer["blocked_reasons"])

    def test_accepted_or_multi_team_incoming_trade_cannot_be_graded(self):
        snapshot = {
            "id": 281, "taken_at": NOW, "team_id": "mine",
            "data": {"team_id": "mine", "pending_trades": [{
                "trade_id": "tx-multi", "proposed_by_id": "other", "accepted": "Jul 12",
                "moves": [
                    {"from_team_id": "mine", "to_team_id": "other", "player_id": "p1", "player": "Mine"},
                    {"from_team_id": "third", "to_team_id": "mine", "player_id": "p2", "player": "Theirs"},
                ],
            }]},
        }
        with patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot):
            offer = self.client.get("/api/trades/incoming").json()["offers"][0]

        self.assertFalse(offer["gradeable"])
        self.assertEqual(offer["status"], "awaiting_execution")
        self.assertIn("already_accepted", offer["blocked_reasons"])
        self.assertIn("multi_team_offer", offer["blocked_reasons"])

    def test_incoming_trade_grade_revalidates_snapshot_and_exact_sides(self):
        snapshot = {
            "id": 281, "taken_at": NOW, "team_id": "mine", "league_id": "league",
            "data": {"team_id": "mine", "pending_trades": [{
                "trade_id": "tx1", "proposed_by_id": "other", "moves": [
                    {"from_team_id": "mine", "to_team_id": "other", "player_id": "p1", "player": "Mine"},
                    {"from_team_id": "other", "to_team_id": "mine", "player_id": "p2", "player": "Theirs"},
                ],
            }]},
        }
        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot),
            patch("sandlot_api.sandlot_trades.offer_validation_error", return_value=None),
        ):
            stale = self.client.post("/api/trades/grade", json={
                "give": ["p1"], "get": ["p2"], "incoming_trade_id": "tx1", "incoming_snapshot_id": 280,
            })
            changed = self.client.post("/api/trades/grade", json={
                "give": ["wrong"], "get": ["p2"], "incoming_trade_id": "tx1", "incoming_snapshot_id": 281,
            })

        self.assertEqual(stale.status_code, 409)
        self.assertIn("snapshot changed", stale.json()["detail"])
        self.assertEqual(changed.status_code, 409)
        self.assertIn("changed or is no longer", changed.json()["detail"])

    def test_incoming_trade_grade_binds_exact_fantrax_origin_to_receipt(self):
        snapshot = {
            "id": 281, "taken_at": NOW, "team_id": "mine", "league_id": "league",
            "data": {"team_id": "mine", "pending_trades": [{
                "trade_id": "tx1", "proposed_by_id": "other", "proposed": "Jul 12", "executed": "Jul 13",
                "moves": [
                    {"from_team_id": "mine", "to_team_id": "other", "player_id": "p1", "player": "Mine"},
                    {"from_team_id": "other", "to_team_id": "mine", "player_id": "p2", "player": "Theirs"},
                ],
            }]},
        }
        stored = {
            "receipt_id": "trade-assessment:" + "a" * 64,
            "builder_version": "trade_assessment_v4",
            "source": "trade_cockpit", "action_type": "trade_assessment",
            "recommendation": {
                "offer": {"give": [{"player_id": "p1"}], "get": [{"player_id": "p2"}]},
                "origin": {
                    "kind": "incoming_fantrax_offer",
                    "fantrax_trade_id": "tx1",
                    "snapshot_id": 281,
                    "proposed_by_team_id": "other",
                    "proposed_at_label": "Jul 12",
                    "scheduled_execution_at_label": "Jul 13",
                    "source_status": "pending",
                    "execution_verification": "unverified",
                    "private_raw": "do not expose",
                },
                "guardrails": {"manual_execution_only": True, "fantrax_write_authorized": False},
            },
        }
        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot),
            patch("sandlot_api.sandlot_trades.offer_validation_error", return_value=None),
            patch("sandlot_api.sandlot_trades.grade_offer", return_value={"snapshot_id": 281}) as grade,
            patch("sandlot_api.sandlot_receipts.build_trade_assessment_receipt", return_value=stored) as build,
            patch("sandlot_api.sandlot_db.record_recommendation_receipt", return_value=(stored, True)),
        ):
            response = self.client.post("/api/trades/grade", json={
                "give": ["p1"], "get": ["p2"], "incoming_trade_id": "tx1", "incoming_snapshot_id": 281,
            })

        self.assertEqual(response.status_code, 200)
        public_receipt = response.json()["receipt"]
        self.assertEqual(public_receipt["trade"]["origin"], {
            "kind": "incoming_fantrax_offer",
            "fantrax_trade_id": "tx1",
            "snapshot_id": 281,
            "proposed_by_team_id": "other",
            "proposed_at_label": "Jul 12",
            "scheduled_execution_at_label": "Jul 13",
            "source_status": "pending",
            "execution_verification": "unverified",
        })
        self.assertTrue(public_receipt["trade"]["guardrails"]["manual_execution_only"])
        self.assertFalse(public_receipt["fantrax_changed"])
        self.assertFalse(public_receipt["writes_enabled"])
        self.assertNotIn("private_raw", response.text)
        self.assertNotIn('"executes_at"', response.text)
        grade.assert_called_once_with(snapshot, ["p1"], ["p2"])
        self.assertEqual(build.call_args.kwargs["origin"], {
            "trade_id": "tx1",
            "snapshot_id": 281,
            "proposed_by_team_id": "other",
            "proposed_at_label": "Jul 12",
            "scheduled_execution_at_label": "Jul 13",
        })

    def test_incoming_trade_surfaces_participant_policy_before_review(self):
        mine = {"id": "p1", "name": "Mine", "slot": "1B", "positions": "1B", "fppg": 4.1, "age": 31}
        young = {"id": "p2", "name": "Young Player", "slot": "OF", "positions": "OF", "fppg": 3.8, "age": 24}
        snapshot = {
            "id": 281, "taken_at": NOW, "team_id": "mine",
            "data": {
                "team_id": "mine",
                "roster": {"rows": [mine]},
                "all_team_rosters": {
                    "mine": {"team_id": "mine", "is_me": True, "rows": [mine]},
                    "other": {"team_id": "other", "rows": [young]},
                },
                "pending_trades": [{
                    "trade_id": "tx-young", "proposed_by_id": "other", "moves": [
                        {"from_team_id": "mine", "to_team_id": "other", "player_id": "p1", "player": "Mine"},
                        {"from_team_id": "other", "to_team_id": "mine", "player_id": "p2", "player": "Young Player"},
                    ],
                }],
            },
        }
        reason = "get player Young Player is age 24 and requires manual dynasty review"
        with patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot):
            offer = self.client.get("/api/trades/incoming").json()["offers"][0]

        self.assertFalse(offer["gradeable"])
        self.assertIn("participant_policy", offer["blocked_reasons"])
        self.assertEqual(offer["manual_review_reason"], reason)
        self.assertEqual(offer["manual_review"]["recommendation"]["title"], "Hold this offer for now")
        self.assertEqual(offer["manual_review"]["horizons"][2]["key"], "dynasty")
        self.assertEqual(offer["manual_review"]["horizons"][2]["status"], "manual_review")
        self.assertTrue(offer["manual_review"]["manual_only"])
        self.assertTrue(offer["manual_review"]["read_only"])
        self.assertFalse(offer["manual_review"]["fantrax_changed"])
        self.assertFalse(offer["manual_review"]["writes_enabled"])

    def test_incoming_trade_excludes_ambiguous_proposer_and_blocks_duplicate_players(self):
        base_moves = [
            {"from_team_id": "mine", "to_team_id": "other", "player_id": "p1", "player": "Mine"},
            {"from_team_id": "other", "to_team_id": "mine", "player_id": "p2", "player": "Theirs"},
        ]
        snapshot = {
            "id": 281, "taken_at": NOW, "team_id": "mine",
            "data": {"team_id": "mine", "pending_trades": [
                {"trade_id": "ambiguous", "proposed_by_id": None, "moves": base_moves},
                {"trade_id": "duplicate", "proposed_by_id": "other", "moves": [base_moves[0], base_moves[0], base_moves[1]]},
            ]},
        }
        with patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot):
            offers = self.client.get("/api/trades/incoming").json()["offers"]

        self.assertEqual([offer["trade_id"] for offer in offers], ["duplicate"])
        self.assertFalse(offers[0]["gradeable"])
        self.assertIn("duplicate_player_identity", offers[0]["blocked_reasons"])

    def test_latest_receipt_returns_no_content_when_none_is_active(self):
        with patch("sandlot_api.sandlot_db.latest_active_recommendation_receipt", return_value=None):
            response = self.client.get("/api/recommendation-receipts/latest")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_recent_outcomes_are_honestly_labeled_and_hide_counterfactual_gain(self):
        scored = {
            **self.receipt,
            "outcome_state": "scored",
            "scoring_version": "team_result_v1",
            "actual_value": 188.5,
            "actual_baseline": None,
            "actual_gain": None,
            "evaluated_at": "2026-07-20T12:00:00Z",
            "outcome_evidence": {
                "measurement_scope": "observed_team_total",
                "team_total_residual": -12.7,
                "absolute_error": 12.7,
                "adherence_state": "unverified",
                "counterfactual_state": "unavailable",
                "counterfactual_reason": "per_player_period_scoring_and_lineup_participation_not_ingested",
                "counterfactual_reason": "per_player_period_scoring_and_lineup_participation_not_ingested",
            },
        }
        with patch(
            "sandlot_api.sandlot_db.recent_scored_recommendation_receipts",
            return_value=[scored],
        ) as recent:
            response = self.client.get("/api/recommendation-outcomes/recent?limit=5")

        self.assertEqual(response.status_code, 200)
        recent.assert_called_once_with(source="monday_lineup", limit=5)
        payload = response.json()
        self.assertFalse(payload["counterfactual_gain_available"])
        self.assertFalse(payload["autopilot_eligible"])
        outcome = payload["items"][0]["outcome"]
        self.assertEqual(outcome["actual_team_total"], 188.5)
        self.assertIsNone(outcome["actual_baseline"])
        self.assertIsNone(outcome["actual_gain"])
        self.assertEqual(outcome["adherence_state"], "unverified")
        self.assertFalse(outcome["autopilot_eligible"])

    def test_recommendation_learning_reports_counterfactuals_without_unlocking_autopilot(self):
        report = {
            "summary": {
                "evaluated": 3, "scored": 2, "unavailable": 1,
                "accepted_and_observed": 1,
                "proposed_matches": 1, "baseline_matches": 1, "other_matches": 0,
                "average_counterfactual_gain": 5.5,
                "positive_counterfactual_gain_rate": 0.5,
            },
            "items": [{
                "period_start": date(2026, 6, 29),
                "period_end": date(2026, 7, 5), "state": "scored",
                "decision_state": "accepted", "evaluated_at": "2026-07-06T12:00:00Z",
                "counterfactual_baseline_total": 21.0,
                "counterfactual_proposed_total": 34.0,
                "counterfactual_gain": 13.0,
                "observed_team_total": 34.0,
                "actual_assignment_match": "proposed",
                "decision_alignment": "accepted_proposal_observed",
            },
            {
                "period_start": date(2026, 6, 22),
                "period_end": date(2026, 6, 28), "state": "scored",
                "decision_state": "rejected", "evaluated_at": "2026-06-29T12:00:00Z",
                "counterfactual_baseline_total": 40.0,
                "counterfactual_proposed_total": 38.0,
                "counterfactual_gain": -2.0,
                "observed_team_total": 40.0,
                "actual_assignment_match": "baseline",
                "decision_alignment": "not_established",
            },
            {
                "period_start": date(2026, 6, 15),
                "period_end": date(2026, 6, 21), "state": "unavailable",
                "decision_state": "pending", "evaluated_at": "2026-06-22T12:00:00Z",
            }],
        }
        with patch(
            "sandlot_api.sandlot_db.recommendation_outcome_evaluation_report",
            return_value=report,
        ) as recent:
            response = self.client.get("/api/recommendation-learning")

        self.assertEqual(response.status_code, 200)
        recent.assert_called_once_with(
            source="monday_lineup", scoring_version="counterfactual_lineup_v1", detail_limit=8
        )
        payload = response.json()
        self.assertEqual(payload["summary"]["evaluated"], 3)
        self.assertEqual(payload["summary"]["scored"], 2)
        self.assertEqual(payload["summary"]["unavailable"], 1)
        self.assertEqual(payload["summary"]["accepted_and_observed"], 1)
        self.assertEqual(payload["summary"]["actual_assignment_matches"], {
            "proposed": 1, "baseline": 1, "other": 0,
        })
        self.assertEqual(payload["summary"]["average_counterfactual_gain"], 5.5)
        self.assertEqual(payload["summary"]["positive_counterfactual_gain_rate"], 0.5)
        self.assertEqual(
            payload["sample_definition"], "one_latest_active_receipt_per_league_team_period"
        )
        self.assertEqual(payload["evidence_checkpoint"]["state"], "collecting")
        self.assertEqual(payload["autopilot"]["state"], "locked")
        self.assertFalse(payload["autopilot"]["eligible"])
        self.assertFalse(payload["autopilot_eligible"])
        self.assertNotIn("evidence_hash", json.dumps(payload))
        self.assertNotIn("judge", json.dumps(payload).lower())

    def test_recommendation_learning_empty_state_is_explicitly_collecting(self):
        with patch(
            "sandlot_api.sandlot_db.recommendation_outcome_evaluation_report",
            return_value={"summary": {}, "items": []},
        ):
            response = self.client.get("/api/recommendation-learning")

        payload = response.json()
        self.assertEqual(payload["sample_state"], "collecting")
        self.assertEqual(payload["summary"]["scored"], 0)
        self.assertIsNone(payload["summary"]["average_counterfactual_gain"])
        self.assertEqual(payload["items"], [])
        self.assertFalse(payload["autopilot_eligible"])

    def test_recommendation_learning_failure_does_not_expose_database_details(self):
        with patch(
            "sandlot_api.sandlot_db.recommendation_outcome_evaluation_report",
            side_effect=RuntimeError("postgres secret host internal.example"),
        ):
            response = self.client.get("/api/recommendation-learning")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Recommendation learning is temporarily unavailable")
        self.assertNotIn("internal.example", response.text)

    def test_recommendation_learning_filters_nonfinite_metrics_and_never_quantity_unlocks(self):
        report = {
            "summary": {
                "evaluated": 8, "scored": 8, "unavailable": 0,
                "accepted_and_observed": 4,
                "proposed_matches": 4, "baseline_matches": 4, "other_matches": 0,
                "average_counterfactual_gain": float("nan"),
                "positive_counterfactual_gain_rate": float("inf"),
            },
            "items": [{
                "period_start": date(2026, 6, 29), "period_end": date(2026, 7, 5),
                "state": "scored", "counterfactual_gain": float("inf"),
                "observed_team_total": float("nan"),
            }],
        }
        payload = sandlot_api._public_recommendation_learning(report)

        self.assertTrue(payload["evidence_checkpoint"]["minimum_sample_reached"])
        self.assertFalse(payload["autopilot"]["eligible"])
        self.assertFalse(payload["autopilot_eligible"])
        self.assertFalse(payload["counterfactual_gain_available"])
        self.assertIsNone(payload["summary"]["average_counterfactual_gain"])
        self.assertIsNone(payload["summary"]["positive_counterfactual_gain_rate"])
        self.assertIsNone(payload["items"][0]["counterfactual"]["gain"])
        self.assertIsNone(payload["items"][0]["observed_team_total"])

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
    def test_two_outcome_workers_commit_one_identical_result(self):
        database_url = os.environ["SANDLOT_TEST_DATABASE_URL"]
        receipt = receipt_fixture(week_start=date(2099, 1, 5))
        results = []
        errors = []
        start = threading.Barrier(2)
        snapshot_id = None
        outcome = {
            "scoring_version": "team_result_v1",
            "actual_value": 188.5,
            "actual_baseline": None,
            "actual_gain": None,
            "outcome_evidence": {
                "receipt_id": receipt["receipt_id"],
                "input_hash": receipt["input_hash"],
                "league_id": receipt["league_id"],
                "team_id": receipt["team_id"],
                "measurement_scope": "observed_team_total",
                "adherence_state": "unverified",
                "counterfactual_state": "unavailable",
                "counterfactual_reason": "per_player_period_scoring_and_lineup_participation_not_ingested",
                "period": {
                    "start": str(receipt["period_start"]),
                    "end": str(receipt["period_end"]),
                },
                "projected_team_total": receipt["projected_value"],
            },
        }
        outcome["outcome_evidence"]["evidence_hash"] = sandlot_receipts.team_result_evidence_hash(
            outcome["outcome_evidence"]
        )

        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            sandlot_db.init_schema()
            with sandlot_db.connect() as setup:
                row = setup.execute(
                    """
                    INSERT INTO snapshots (taken_at, source, status, league_id, team_id, errors, data)
                    VALUES (clock_timestamp(), 'outcome_test', 'success', 'league', 'team', '[]'::jsonb, '{}'::jsonb)
                    RETURNING id
                    """
                ).fetchone()
                snapshot_id = int(row["id"])
            receipt["snapshot_id"] = snapshot_id
            sandlot_db.record_recommendation_receipt(receipt)

            def score():
                try:
                    start.wait(timeout=2)
                    _row, changed = sandlot_db.score_recommendation_receipt_team_result(
                        receipt_id=receipt["receipt_id"], outcome=outcome
                    )
                    results.append(changed)
                except Exception as exc:
                    errors.append(exc)

            workers = [threading.Thread(target=score) for _ in range(2)]
            try:
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(timeout=4)
                self.assertFalse(any(worker.is_alive() for worker in workers))
                self.assertEqual(errors, [])
                self.assertEqual(sorted(results), [False, True])
            finally:
                with sandlot_db.connect() as cleanup:
                    cleanup.execute(
                        "DELETE FROM recommendation_receipts WHERE receipt_id = %s",
                        (receipt["receipt_id"],),
                    )
                    if snapshot_id is not None:
                        cleanup.execute("DELETE FROM snapshots WHERE id = %s", (snapshot_id,))

    def test_waiting_decision_rechecks_wall_clock_expiry_after_row_lock(self):
        database_url = os.environ["SANDLOT_TEST_DATABASE_URL"]
        receipt = receipt_fixture(week_start=date(2099, 1, 5))
        generated_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        receipt["generated_at"] = generated_at
        receipt["expires_at"] = generated_at + timedelta(seconds=5)
        outcome = []
        waiting_transaction_started = threading.Event()
        snapshot_id = None

        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            sandlot_db.init_schema()
            with sandlot_db.connect() as setup:
                snapshot_row = setup.execute(
                    """
                    INSERT INTO snapshots (taken_at, source, status, league_id, team_id, errors, data)
                    VALUES (clock_timestamp(), 'concurrency_test', 'success', 'league', 'team', '[]'::jsonb, '{}'::jsonb)
                    RETURNING id
                    """
                ).fetchone()
                snapshot_id = int(snapshot_row["id"])
            receipt["snapshot_id"] = snapshot_id
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
                    if snapshot_id is not None:
                        cleanup.execute("DELETE FROM snapshots WHERE id = %s", (snapshot_id,))


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
