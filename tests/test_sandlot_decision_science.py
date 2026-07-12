import copy
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import sandlot_decision_science as science
import sandlot_db


UTC = timezone.utc


def row_fixture(index: int, *, projected_gain: float | None = None, actual_gain: float | None = None):
    generated = datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=index * 14)
    period_start = generated.date() + timedelta(days=1)
    period_end = period_start + timedelta(days=6)
    projected = float(index + 1 if projected_gain is None else projected_gain)
    actual = float(2 * projected + 1 if actual_gain is None else actual_gain)
    return {
        "receipt_id": f"receipt-{index}",
        "builder_version": "monday_lineup_v2",
        "input_hash": "a" * 64,
        "generated_at": generated,
        "period_start": period_start,
        "period_end": period_end,
        "baseline_value": 100.0,
        "projected_value": 100.0 + projected,
        "projected_gain": projected,
        "recommendation": {
            "snapshot": {"taken_at": (generated - timedelta(hours=1)).isoformat()},
            "period": {
                "decision_deadline_at": (generated + timedelta(days=1, hours=20)).isoformat(),
                "deadline_source": "mlb_schedule_first_game_v1",
            },
            "baseline_assignment": [{"player_id": "a"}, {"player_id": "b"}],
            "proposed_assignment": [{"player_id": "a"}, {"player_id": f"new-{index}"}],
            "unfilled_slots": [],
        },
        "state": "scored",
        "scoring_version": "counterfactual_lineup_v1",
        "metrics": {"counterfactual_gain": actual},
        "evaluated_at": generated + timedelta(days=9),
        "source_evidence_version": "fantrax_period_lineup_v2",
        "source_evidence_hash": "b" * 64,
        "evaluation_evidence_hash": f"{index:064x}",
    }


class DecisionScienceTests(unittest.TestCase):
    def test_db_query_keeps_unscored_periods_in_coverage_denominator(self):
        calls = []

        class Result:
            def fetchall(self):
                return [{"receipt_id": "one", "state": None}]

        @contextmanager
        def fake_connect():
            class Conn:
                def execute(self, sql, params):
                    calls.append((sql, params))
                    return Result()
            yield Conn()

        with patch.object(sandlot_db, "connect", fake_connect):
            rows = sandlot_db.list_lineup_decision_science_rows()

        self.assertEqual(rows, [{"receipt_id": "one", "state": None}])
        sql, params = calls[0]
        self.assertIn("LEFT JOIN recommendation_outcome_evaluations", sql)
        self.assertNotIn("e.state = 'scored'", sql)
        self.assertIn("PARTITION BY r.league_id, r.team_id, r.period_start, r.period_end", sql)
        self.assertEqual(params, (10000,))

    def test_dataset_separates_decision_features_from_later_label(self):
        raw = row_fixture(0)
        dataset = science.build_lineup_dataset([raw])

        self.assertEqual(len(dataset), 1)
        sample = dataset[0]
        self.assertEqual(sample["dataset_version"], "lineup_decision_features_v1")
        self.assertEqual(sample["features"]["projected_gain"], 1.0)
        self.assertEqual(sample["features"]["assignment_change_count"], 2)
        self.assertEqual(sample["label"], {"counterfactual_gain": 3.0})
        self.assertNotIn("counterfactual_gain", sample["features"])
        self.assertEqual(sample["lineage"]["source_evidence_version"], "fantrax_period_lineup_v2")

        changed_label = copy.deepcopy(raw)
        changed_label["metrics"]["counterfactual_gain"] = 999
        changed = science.build_lineup_dataset([changed_label])[0]
        self.assertEqual(changed["features"], sample["features"])
        self.assertNotEqual(changed["label"], sample["label"])

    def test_legacy_v1_is_reported_but_cannot_enter_v2_model_features(self):
        v2 = row_fixture(0)
        legacy = row_fixture(1)
        legacy["builder_version"] = "monday_lineup_v1"
        legacy["recommendation"]["period"].pop("decision_deadline_at")
        legacy["recommendation"]["period"].pop("deadline_source")

        dataset = science.build_lineup_dataset([legacy, v2])
        coverage = science.coverage_report(
            [legacy, v2], as_of="2100-01-01T00:00:00Z",
        )

        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset[0]["features"]["projected_gain"], v2["projected_gain"])
        self.assertEqual(coverage["legacy_deadline_unavailable_periods"], 1)
        self.assertEqual(coverage["compatible_v2_periods"], 1)
        self.assertEqual(coverage["label_coverage_rate"], 1.0)

    def test_dataset_rejects_label_available_before_feature_cutoff(self):
        raw = row_fixture(0)
        raw["evaluated_at"] = raw["generated_at"]
        with self.assertRaisesRegex(ValueError, "after decision features"):
            science.build_lineup_dataset([raw])

    def test_dataset_rejects_post_deadline_receipt_and_premature_label(self):
        late = row_fixture(0)
        late["generated_at"] = datetime.fromisoformat(late["recommendation"]["period"]["decision_deadline_at"]) + timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, "first scoring deadline"):
            science.build_lineup_dataset([late])

        premature = row_fixture(0)
        premature["evaluated_at"] = datetime.combine(premature["period_end"], datetime.min.time(), tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "after the target period closes"):
            science.build_lineup_dataset([premature])

    def test_real_monday_four_am_receipt_precedes_first_game_deadline(self):
        raw = row_fixture(0)
        raw["period_start"] = date(2026, 7, 13)
        raw["period_end"] = date(2026, 7, 19)
        raw["generated_at"] = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)  # 4am ET workflow
        raw["recommendation"]["snapshot"]["taken_at"] = "2026-07-13T07:55:00Z"
        raw["recommendation"]["period"] = {
            "decision_deadline_at": "2026-07-13T23:10:00Z",
            "deadline_source": "mlb_schedule_first_game_v1",
        }
        raw["evaluated_at"] = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)

        sample = science.build_lineup_dataset([raw])[0]

        self.assertEqual(sample["feature_cutoff_at"], "2026-07-13T08:00:00+00:00")
        self.assertEqual(sample["decision_deadline_at"], "2026-07-13T23:10:00+00:00")

    def test_dataset_rejects_snapshot_observed_after_receipt_generation(self):
        raw = row_fixture(0)
        raw["recommendation"]["snapshot"]["taken_at"] = (raw["generated_at"] + timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "observation time"):
            science.build_lineup_dataset([raw])

    def test_empty_and_small_samples_are_explicitly_insufficient(self):
        empty = science.evaluation_report([])
        small = science.evaluation_report(science.build_lineup_dataset([row_fixture(i) for i in range(5)]))

        self.assertEqual(empty["sample_state"], "insufficient_evidence")
        self.assertIsNone(empty["baseline"]["metrics"])
        self.assertEqual(small["sample_state"], "insufficient_evidence")
        self.assertFalse(small["candidate"]["eligible_for_product_use"])
        self.assertFalse(small["autopilot_eligible"])

    def test_rolling_candidate_uses_only_labels_available_at_test_cutoff(self):
        dataset = science.build_lineup_dataset([row_fixture(i) for i in range(14)])
        report = science.evaluation_report(dataset)

        self.assertEqual(report["sample_state"], "ready_for_candidate_evaluation")
        self.assertGreaterEqual(report["candidate"]["evaluation_samples"], 4)
        self.assertEqual(report["candidate"]["metrics"]["mae"], 0.0)
        self.assertTrue(report["candidate"]["beats_baseline"])
        self.assertFalse(report["candidate"]["eligible_for_product_use"])
        for prediction in report["candidate"]["predictions"]:
            self.assertGreaterEqual(prediction["training_samples"], 8)
            self.assertGreaterEqual(prediction["training_horizons"], 8)

    def test_direction_accuracy_treats_zero_as_neutral(self):
        metrics = science._metrics([(-1.0, 0.0), (0.0, 0.0), (1.0, 1.0)])
        self.assertEqual(metrics["direction_accuracy"], round(2 / 3, 6))

    def test_future_label_cannot_change_an_earlier_prediction(self):
        rows = [row_fixture(i) for i in range(14)]
        baseline = science.evaluation_report(science.build_lineup_dataset(rows))
        changed = copy.deepcopy(rows)
        changed[-1]["metrics"]["counterfactual_gain"] = -999
        mutated = science.evaluation_report(science.build_lineup_dataset(changed))

        baseline_predictions = baseline["candidate"]["predictions"]
        mutated_predictions = mutated["candidate"]["predictions"]
        self.assertEqual(
            [item["candidate_prediction"] for item in baseline_predictions[:-1]],
            [item["candidate_prediction"] for item in mutated_predictions[:-1]],
        )

    def test_thin_selected_label_coverage_cannot_make_candidate_ready(self):
        scored = [row_fixture(i) for i in range(14)]
        unscored = []
        for index in range(14, 34):
            row = row_fixture(index)
            row.update({"state": None, "scoring_version": None, "metrics": None, "evaluated_at": None})
            row["evaluation_evidence"] = None
            unscored.append(row)
        raw = scored + unscored
        coverage = science.coverage_report(raw, as_of="2100-01-01T00:00:00Z")
        report = science.evaluation_report(
            science.build_lineup_dataset(raw), coverage=coverage,
        )

        self.assertEqual(coverage["total_periods"], 34)
        self.assertEqual(coverage["scored_periods"], 14)
        self.assertLess(coverage["label_coverage_rate"], 0.75)
        self.assertFalse(coverage["coverage_ready"])
        self.assertEqual(report["sample_state"], "insufficient_evidence")
        self.assertFalse(report["candidate"]["beats_baseline"])

    def test_future_period_is_reported_but_not_counted_as_missing_label(self):
        completed = [row_fixture(i) for i in range(3)]
        future = row_fixture(10)
        future.update({"state": None, "scoring_version": None, "metrics": None, "evaluated_at": None})
        as_of = datetime.combine(completed[-1]["period_end"] + timedelta(days=1), datetime.min.time(), tzinfo=UTC) + timedelta(hours=6)

        coverage = science.coverage_report(completed + [future], as_of=as_of)

        self.assertEqual(coverage["all_observed_periods"], 4)
        self.assertEqual(coverage["total_periods"], 3)
        self.assertEqual(coverage["not_yet_due_periods"], 1)
        self.assertEqual(coverage["label_coverage_rate"], 1.0)
        self.assertTrue(coverage["coverage_ready"])

    def test_mature_unscored_periods_distinguish_ineligible_from_missing_evaluation(self):
        ineligible = row_fixture(0)
        ineligible.update({"state": None, "metrics": None, "evaluated_at": None})
        ineligible["counterfactual_capability"] = {"eligible": False, "reason": "multiple_lineup_windows"}
        eligible_missing = row_fixture(1)
        eligible_missing.update({"state": None, "metrics": None, "evaluated_at": None})
        eligible_missing["counterfactual_capability"] = {"eligible": True}

        coverage = science.coverage_report(
            [ineligible, eligible_missing], as_of="2100-01-01T00:00:00Z",
        )

        self.assertEqual(coverage["pending_or_ineligible_periods"], 2)
        self.assertEqual(coverage["unscored_reasons"], {
            "counterfactual_ineligible:multiple_lineup_windows": 1,
            "eligible_evaluation_missing": 1,
        })

    def test_cli_report_reads_internal_rows_and_never_enables_autopilot(self):
        rows = [row_fixture(i) for i in range(3)]
        with patch.object(science.sandlot_db, "init_schema"), patch.object(
            science.sandlot_db, "list_lineup_decision_science_rows", return_value=rows,
        ) as list_rows:
            report = science.build_report(limit=10, as_of="2100-01-01T00:00:00Z")

        list_rows.assert_called_once_with(limit=10)
        self.assertEqual(report["sample_size"], 3)
        self.assertFalse(report["autopilot_eligible"])
        self.assertFalse(report["source_query_truncated"])


if __name__ == "__main__":
    unittest.main()
