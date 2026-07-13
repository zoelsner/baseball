import unittest
from unittest.mock import patch

import sandlot_calibration
import sandlot_api
import sandlot_matchup


class ProjectionCalibrationTests(unittest.TestCase):
    def test_actual_result_payload_requires_completed_matchup(self):
        snapshot = {
            "league_id": "league",
            "team_id": "me",
            "matchup": {
                "complete": True,
                "my_team_id": "me",
                "opponent_team_id": "opp",
                "period_number": 4,
                "my_score": 12,
                "opponent_score": 10,
            },
        }

        payload = sandlot_matchup.actual_result_payload(snapshot)

        self.assertEqual(payload["matchup_key"], "league:4:me:opp")
        self.assertEqual(payload["period_id"], "4")
        self.assertEqual(payload["actual_my"], 12.0)
        self.assertEqual(payload["actual_opp"], 10.0)
        self.assertEqual(payload["actual_winner"], "me")

    def test_actual_result_payload_uses_latest_completed_matchup(self):
        snapshot = {
            "league_id": "league",
            "team_id": "me",
            "matchup": {
                "complete": False,
                "period_number": 5,
                "my_score": 0,
                "opponent_score": 0,
                "latest_completed": {
                    "complete": True,
                    "period_number": 4,
                    "my_team_id": "me",
                    "opponent_team_id": "opp",
                    "my_score": 12,
                    "opponent_score": 10,
                },
            },
        }

        payload = sandlot_matchup.actual_result_payload(snapshot)

        self.assertEqual(payload["matchup_key"], "league:4:me:opp")
        self.assertEqual(payload["period_id"], "4")
        self.assertEqual(payload["actual_my"], 12.0)
        self.assertEqual(payload["actual_opp"], 10.0)
        self.assertEqual(payload["actual_winner"], "me")

    def test_calibration_report_groups_metrics_by_model_and_surface(self):
        rows = [
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
                "matchup_key": "league:1:me:opp1",
                "shown_date": "2026-05-01",
                "predicted_my": 110,
                "predicted_opp": 100,
                "predicted_margin": 10,
                "win_probability": 0.75,
                "actual_my": 100,
                "actual_opp": 100,
                "actual_winner": "tie",
                "drivers": {"game_volume_edge": 2, "opportunity_completeness": "complete"},
            },
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
                "matchup_key": "league:2:me:opp2",
                "shown_date": "2026-05-08",
                "predicted_my": 108,
                "predicted_opp": 98,
                "predicted_margin": 10,
                "win_probability": 0.72,
                "actual_my": 100,
                "actual_opp": 100,
                "actual_winner": "tie",
                "drivers": {"game_volume_edge": 3, "opportunity_completeness": "complete"},
            },
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
                "matchup_key": "league:3:me:opp3",
                "shown_date": "2026-05-15",
                "predicted_my": 106,
                "predicted_opp": 96,
                "predicted_margin": 10,
                "win_probability": 0.70,
                "actual_my": 100,
                "actual_opp": 100,
                "actual_winner": "tie",
                "drivers": {"game_volume_edge": 1, "opportunity_completeness": "complete"},
            },
        ]

        report = sandlot_matchup.calibration_report(rows)

        self.assertEqual(report["sample_size"], 3)
        group = report["groups"][0]
        self.assertEqual(group["model_version"], sandlot_matchup.MODEL_VERSION)
        self.assertEqual(group["surface"], "api")
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["independent_matchup_count"], 3)
        self.assertEqual(group["metric_checkpoint_count"], 3)
        self.assertEqual(group["release_readiness"]["state"], "collecting")
        self.assertFalse(group["release_readiness"]["allowed_outputs"]["precise_probability"])
        self.assertFalse(group["release_readiness"]["allowed_outputs"]["action_probability_delta"])
        self.assertEqual(group["metrics"]["margin_bias"], 10.0)
        self.assertEqual(group["metrics"]["margin_mae"], 10.0)
        self.assertIn("positive_margin_bias", group["flags"])
        self.assertIn("game_volume_edge_may_be_overrated", group["flags"])

    def test_cli_report_reads_projection_logs(self):
        rows = [
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
                "matchup_key": "league:1:me:opp",
                "shown_date": "2026-05-01",
                "predicted_my": 10,
                "predicted_opp": 8,
                "predicted_margin": 2,
                "win_probability": 0.8,
                "actual_my": 12,
                "actual_opp": 8,
                "actual_winner": "me",
                "drivers": {},
            },
        ]
        with patch.object(sandlot_calibration.sandlot_db, "init_schema"), patch.object(
            sandlot_calibration.sandlot_db, "list_projection_logs_for_calibration", return_value=rows
        ) as list_logs:
            report = sandlot_calibration.build_report(limit=5)

        list_logs.assert_called_once_with(limit=5)
        self.assertEqual(report["sample_size"], 1)
        self.assertEqual(report["groups"][0]["flags"], ["insufficient_sample"])

    def test_daily_and_surface_duplicates_do_not_inflate_independent_matchups(self):
        rows = []
        for surface in ("api", "skipper_card"):
            for day, probability in (("2026-05-01", 0.55), ("2026-05-02", 0.75)):
                rows.append({
                    "id": len(rows) + 1,
                    "model_version": sandlot_matchup.MODEL_VERSION,
                    "surface": surface,
                    "matchup_key": "league:1:me:opp",
                    "shown_date": day,
                    "predicted_my": 100,
                    "predicted_opp": 95,
                    "predicted_margin": 5,
                    "win_probability": probability,
                    "actual_my": 110,
                    "actual_opp": 100,
                    "actual_winner": "me",
                    "drivers": {"opportunity_completeness": "complete"},
                })

        report = sandlot_matchup.calibration_report(rows)

        self.assertEqual(report["forecast_row_count"], 4)
        self.assertEqual(report["labeled_row_count"], 4)
        self.assertEqual(report["independent_matchup_count"], 1)
        self.assertEqual([group["independent_matchup_count"] for group in report["groups"]], [1, 1])
        self.assertEqual([group["metric_checkpoint_count"] for group in report["groups"]], [1, 1])
        self.assertEqual(report["groups"][0]["metrics"]["brier_score"], 0.2025)

    def test_unlabeled_matchups_remain_in_coverage_denominator(self):
        labeled = {
            "model_version": sandlot_matchup.MODEL_VERSION, "surface": "api",
            "matchup_key": "league:1:me:opp1", "shown_date": "2026-05-01",
            "predicted_my": 100, "predicted_opp": 95, "predicted_margin": 5,
            "win_probability": 0.6, "actual_my": 101, "actual_opp": 99,
            "actual_winner": "me", "drivers": {"opportunity_completeness": "complete"},
        }
        unlabeled = {
            **labeled, "matchup_key": "league:2:me:opp2", "shown_date": "2026-05-08",
            "actual_my": None, "actual_opp": None, "actual_winner": None,
        }

        group = sandlot_matchup.calibration_report([labeled, unlabeled])["groups"][0]

        self.assertEqual(group["eligible_matchup_count"], 2)
        self.assertEqual(group["independent_matchup_count"], 1)
        self.assertEqual(group["actual_coverage"], 0.5)
        self.assertIn("insufficient_actual_coverage", group["release_readiness"]["reasons"])

    def test_later_label_cannot_replace_unlabeled_earliest_checkpoint(self):
        earliest = {
            "id": 1, "model_version": sandlot_matchup.MODEL_VERSION, "surface": "api",
            "matchup_key": "league:1:me:opp", "shown_date": "2026-05-01",
            "predicted_my": 100, "predicted_opp": 95, "predicted_margin": 5,
            "win_probability": 0.55, "actual_my": None, "actual_opp": None,
            "actual_winner": None, "drivers": {"opportunity_completeness": "complete"},
        }
        later = {
            **earliest, "id": 2, "shown_date": "2026-05-02", "win_probability": 0.95,
            "actual_my": 110, "actual_opp": 100, "actual_winner": "me",
        }

        group = sandlot_matchup.calibration_report([later, earliest])["groups"][0]

        self.assertEqual(group["labeled_row_count"], 1)
        self.assertEqual(group["independent_matchup_count"], 0)
        self.assertEqual(group["metric_checkpoint_count"], 0)
        self.assertEqual(group["actual_coverage"], 0.0)
        self.assertIsNone(group["metrics"]["brier_score"])

    def test_lower_bound_history_cannot_certify_probability(self):
        rows = []
        for index in range(100):
            win = index % 2 == 0
            rows.append({
                "id": index + 1, "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api", "matchup_key": f"league:{index}:me:opp{index}",
                "shown_date": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
                "predicted_my": 110 if win else 90, "predicted_opp": 100,
                "predicted_margin": 10 if win else -10,
                "win_probability": 0.9 if win else 0.1,
                "actual_my": 110 if win else 90, "actual_opp": 100,
                "actual_winner": "me" if win else f"opp{index}",
                "drivers": {
                    "forecast_provenance": sandlot_matchup.FORECAST_PROVENANCE_VERSION,
                    "opportunity_completeness": "known_opportunities_lower_bound",
                },
            })

        group = sandlot_matchup.calibration_report(rows)["groups"][0]

        self.assertEqual(group["independent_matchup_count"], 100)
        self.assertEqual(group["release_cohort"]["eligible_matchup_count"], 0)
        self.assertEqual(group["release_readiness"]["state"], "collecting")
        self.assertIn("incomplete_opportunity_scope", group["release_exclusions"])
        self.assertFalse(group["release_readiness"]["allowed_outputs"]["precise_probability"])

    def test_even_strong_complete_history_does_not_auto_certify_precise_probability(self):
        rows = []
        for index in range(100):
            win = index % 2 == 0
            rows.append({
                "id": index + 1, "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api", "matchup_key": f"league:{index}:me:opp{index}",
                "shown_date": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
                "predicted_my": 110 if win else 90, "predicted_opp": 100,
                "predicted_margin": 10 if win else -10,
                "win_probability": 0.9 if win else 0.1,
                "actual_my": 110 if win else 90, "actual_opp": 100,
                "actual_winner": "me" if win else f"opp{index}",
                "drivers": {
                    "forecast_provenance": sandlot_matchup.FORECAST_PROVENANCE_VERSION,
                    "opportunity_completeness": "complete",
                },
            })

        readiness = sandlot_matchup.calibration_report(rows)["release_readiness"]

        self.assertEqual(readiness["state"], "band_ready")
        self.assertIn("numeric_probability_not_certified", readiness["reasons"])
        self.assertFalse(readiness["allowed_outputs"]["precise_probability"])
        self.assertFalse(readiness["allowed_outputs"]["action_probability_delta"])

    def test_public_readiness_never_activates_probability_or_action_deltas(self):
        rows = [{
            "model_version": sandlot_matchup.MODEL_VERSION, "surface": "api",
            "matchup_key": "league:1:me:opp", "shown_date": "2026-05-01",
            "predicted_my": 100, "predicted_opp": 95, "predicted_margin": 5,
            "win_probability": 0.6, "actual_my": None, "actual_opp": None,
            "actual_winner": None, "drivers": {
                "opportunity_completeness": "known_opportunities_lower_bound"
            },
        }]
        with patch.object(
            sandlot_api.sandlot_db, "list_projection_logs_for_calibration", return_value=rows
        ), patch.object(
            sandlot_api.sandlot_db, "latest_successful_snapshot", return_value=None
        ):
            payload = sandlot_api.matchup_probability_readiness()

        self.assertEqual(payload["state"], "collecting")
        self.assertEqual(payload["forecast_row_count"], 1)
        self.assertEqual(payload["eligible_matchup_count"], 1)
        self.assertEqual(payload["independent_matchup_count"], 0)
        self.assertFalse(payload["probability_calibrated"])
        self.assertEqual(payload["product_activation"]["state"], "locked")
        self.assertFalse(payload["product_activation"]["precise_probability"])
        self.assertFalse(payload["product_activation"]["action_probability_delta"])
        self.assertFalse(payload["product_activation"]["autopilot_eligible"])
        self.assertEqual(payload["current_applicability"]["state"], "withheld")
        self.assertIn("current_forecast_unavailable", payload["current_applicability"]["reasons"])

    def test_current_lower_bound_forecast_stays_withheld_even_if_history_passes(self):
        readiness = {
            "state": "band_ready", "reasons": ["numeric_probability_not_certified"],
            "allowed_outputs": {"precise_probability": False, "action_probability_delta": False},
        }
        report = {"groups": [], "release_readiness": readiness}
        plan = {
            "planning_horizon": {"period_number": 17},
            "matchup": {
                "opportunity_completeness": "known_opportunities_lower_bound",
                "pitchers_without_probable_start": 16,
            },
        }
        with patch.object(
            sandlot_api.sandlot_db, "list_projection_logs_for_calibration", return_value=[]
        ), patch.object(
            sandlot_api.sandlot_matchup, "calibration_report", return_value=report
        ), patch.object(
            sandlot_api.sandlot_db, "latest_successful_snapshot", return_value={"id": 1, "data": {}}
        ), patch.object(
            sandlot_api, "_matchup_decisions", return_value={"win_this_week": plan}
        ):
            payload = sandlot_api.matchup_probability_readiness()

        self.assertEqual(payload["current_applicability"]["state"], "withheld")
        self.assertTrue(payload["current_applicability"]["evidence_band_ready"])
        self.assertFalse(payload["current_applicability"]["opportunity_complete"])
        self.assertEqual(payload["current_forecast"]["pitchers_without_probable_start"], 16)
        self.assertIn(
            "current_pitcher_opportunity_coverage_incomplete",
            payload["current_applicability"]["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
