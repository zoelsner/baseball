import unittest
from unittest.mock import patch

import sandlot_calibration
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

    def test_calibration_report_groups_metrics_by_model_and_surface(self):
        rows = [
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
                "predicted_my": 110,
                "predicted_opp": 100,
                "predicted_margin": 10,
                "win_probability": 0.75,
                "actual_my": 100,
                "actual_opp": 100,
                "actual_winner": "tie",
                "drivers": {"game_volume_edge": 2},
            },
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
                "predicted_my": 108,
                "predicted_opp": 98,
                "predicted_margin": 10,
                "win_probability": 0.72,
                "actual_my": 100,
                "actual_opp": 100,
                "actual_winner": "tie",
                "drivers": {"game_volume_edge": 3},
            },
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
                "predicted_my": 106,
                "predicted_opp": 96,
                "predicted_margin": 10,
                "win_probability": 0.70,
                "actual_my": 100,
                "actual_opp": 100,
                "actual_winner": "tie",
                "drivers": {"game_volume_edge": 1},
            },
        ]

        report = sandlot_matchup.calibration_report(rows)

        self.assertEqual(report["sample_size"], 3)
        group = report["groups"][0]
        self.assertEqual(group["model_version"], sandlot_matchup.MODEL_VERSION)
        self.assertEqual(group["surface"], "api")
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["metrics"]["margin_bias"], 10.0)
        self.assertEqual(group["metrics"]["margin_mae"], 10.0)
        self.assertIn("positive_margin_bias", group["flags"])
        self.assertIn("game_volume_edge_may_be_overrated", group["flags"])

    def test_cli_report_reads_projection_logs(self):
        rows = [
            {
                "model_version": sandlot_matchup.MODEL_VERSION,
                "surface": "api",
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
            sandlot_calibration.sandlot_db, "list_projection_logs_for_evaluation", return_value=rows
        ) as list_logs:
            report = sandlot_calibration.build_report(limit=5)

        list_logs.assert_called_once_with(limit=5)
        self.assertEqual(report["sample_size"], 1)
        self.assertEqual(report["groups"][0]["flags"], ["insufficient_sample"])


if __name__ == "__main__":
    unittest.main()
