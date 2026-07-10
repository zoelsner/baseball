import copy
import unittest
from datetime import datetime, timezone

from scripts import sandlot_readonly_monitor as monitor


NOW = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)


def healthy_lineup_contract(snapshot_id):
    slot_moves = [
        {"order": 1, "player_id": "mine-in", "from_slot": "RES", "to_slot": "OF"},
        {"order": 2, "player_id": "mine-out", "from_slot": "OF", "to_slot": "RES"},
    ]
    return {
        "version": 2,
        "proposal_id": "lineup:test",
        "snapshot_id": snapshot_id,
        "slot_moves": slot_moves,
        "input_hash": "a" * 64,
        "projected_benefit": {
            "points": 2.0,
            "win_probability_delta": None,
            "probability_calibrated": False,
        },
        "freshness_policy": {"requires_live_preflight": True},
        "post_write_verification": {"required": True},
        "confirmation": {
            "mode": "exact_contract_match",
            "expected": {
                "proposal_id": "lineup:test",
                "snapshot_id": snapshot_id,
                "slot_moves": slot_moves,
                "input_hash": "a" * 64,
            },
        },
    }


def healthy_payloads():
    snapshot_id = 264
    quality = {
        "my_roster": {"state": "ok"},
        "lineup_slots": {"state": "ok"},
        "add_drop_recommendations_ready": True,
    }
    return {
        "/api/health": {
            "ok": True,
            "database": "ok",
            "freshness": {"state": "fresh", "age_minutes": 30},
            "latest_refresh_run": {"status": "success"},
        },
        "/api/snapshot/latest": {
            "snapshot_id": snapshot_id,
            "taken_at": "2026-07-10T15:30:00Z",
            "freshness": {"state": "fresh", "age_minutes": 30},
            "roster": [
                {"id": "mine-out", "name": "Roster Player", "age": 29, "fppg": 2.0},
                {"id": "mine-in", "name": "Bench Player", "age": 27, "fppg": 4.0},
            ],
            "player_index": [
                {"id": "mine-out", "name": "Roster Player", "source": "mine", "age": 29, "fppg": 2.0},
                {"id": "mine-in", "name": "Bench Player", "source": "mine", "age": 27, "fppg": 4.0},
                {"id": "league-1", "name": "League Player", "source": "league", "age": 31, "fppg": 3.0},
            ],
            "matchup": {
                "projection": {
                    "model_version": "matchup_projection_v4",
                    "scoring_basis": "current_snapshot_fppg_x_remaining_games",
                    "probability_calibrated": False,
                    "projected_my": 100.0,
                    "projected_opp": 99.0,
                    "my_remaining_games": 10,
                    "opp_remaining_games": 10,
                    "win_probability": 0.55,
                    "complete": False,
                },
                "recommendations": {
                    "model_version": "matchup_projection_v4",
                    "base_projection": {"projected_my": 100.0, "probability_calibrated": False},
                    "thresholds": {
                        "points_delta": 1.0,
                        "win_probability_delta": None,
                        "probability_calibrated": False,
                    },
                    "recommendations": [{
                        "rank": 1,
                        "points_delta": 2.0,
                        "win_probability_delta": None,
                        "probability_calibrated": False,
                        "confidence_basis": "projected_points_magnitude",
                        "replacement_card": {
                            "confidence_basis": "projected_points_magnitude",
                            "move_in": {"id": "mine-in"},
                            "move_out": {"id": "mine-out"},
                            "projected_benefit": {
                                "new_projected_my": 102.0,
                                "win_probability_delta": None,
                                "base_win_probability": None,
                                "new_win_probability": None,
                                "probability_calibrated": False,
                            },
                            "movability": {
                                "participants": {
                                    "move_in": {"id": "mine-in"},
                                    "move_out": {"id": "mine-out"},
                                },
                            },
                            "proposal": {
                                "type": "lineup_swap",
                                "status": "blocked",
                                "writes_enabled": False,
                                "executable": False,
                                "contract": healthy_lineup_contract(snapshot_id),
                            },
                        },
                    }],
                },
            },
            "errors": [],
            "data_quality": quality,
        },
        "/api/attention": {
            "snapshot_id": snapshot_id,
            "items": [{
                "kind": "replacement",
                "proposal": {
                    "writes_enabled": False,
                    "executable": False,
                    "status": "blocked",
                    "contract": {"snapshot_id": snapshot_id},
                },
            }],
            "changes": [],
        },
        "/api/hot-swaps/latest": {
            "snapshot_id": snapshot_id,
            "state": "ready",
            "writes_enabled": False,
            "proposals": [{
                "proposal": {
                    "writes_enabled": False,
                    "executable": False,
                    "status": "blocked",
                    "contract": {"snapshot_id": snapshot_id},
                },
            }],
        },
        "/api/waiver-swaps/latest": {
            "snapshot_id": snapshot_id,
            "cards": [{
                "net_delta": 1.2,
                "confidence": "Medium",
                "add": {"age": 28, "score_source": "FP/G"},
                "move_out": {"age": 31},
            }],
            "data_quality": quality,
        },
    }


class ReadOnlyMonitorTests(unittest.TestCase):
    def test_healthy_contract_passes_without_exposing_player_payloads(self):
        report = monitor.evaluate_payloads(healthy_payloads(), checked_at=NOW)

        self.assertTrue(report["ok"])
        self.assertEqual(report["failures"], [])
        rendered = monitor.render_markdown(report)
        self.assertIn("Sandlot read-only monitor: PASS", rendered)
        self.assertNotIn("Roster Player", rendered)

    def test_old_snapshot_fails(self):
        payloads = healthy_payloads()
        payloads["/api/snapshot/latest"]["freshness"] = {"state": "old", "age_minutes": 3000}

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("snapshot_too_old", codes)
        self.assertIn("snapshot_not_fresh_enough", codes)

    def test_cross_endpoint_snapshot_and_write_boundaries_fail_closed(self):
        payloads = healthy_payloads()
        payloads["/api/attention"]["snapshot_id"] = 263
        payloads["/api/hot-swaps/latest"]["writes_enabled"] = True
        proposal = payloads["/api/hot-swaps/latest"]["proposals"][0]["proposal"]
        proposal.update({"writes_enabled": True, "executable": True, "status": "executable"})
        proposal["contract"]["snapshot_id"] = 263

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("attention_snapshot_mismatch", codes)
        self.assertIn("hot_swaps_write_boundary", codes)
        self.assertIn("hot_swaps_executable_proposal", codes)
        self.assertIn("hot_swaps_contract_snapshot_mismatch", codes)

    def test_nonpositive_waiver_card_fails(self):
        payloads = healthy_payloads()
        payloads["/api/waiver-swaps/latest"]["cards"] = [
            {"net_delta": 0},
            {"net_delta": -1.0},
        ]

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        matching = [
            item for item in report["failures"]
            if item["code"] == "waivers_nonpositive_delta"
        ]
        self.assertEqual(len(matching), 1)
        self.assertIn("2 waiver card(s)", matching[0]["message"])

    def test_low_confidence_or_age_blind_waiver_card_fails(self):
        payloads = healthy_payloads()
        payloads["/api/waiver-swaps/latest"]["cards"] = [{
            "net_delta": 2.0,
            "confidence": "Low",
            "add": {"age": None, "score_source": "_cells inferred FP/G"},
            "move_out": {"age": None},
        }]

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        self.assertIn("waivers_untrusted_card", {item["code"] for item in report["failures"]})

    def test_owner_protected_anchor_waiver_card_fails(self):
        payloads = healthy_payloads()
        payloads["/api/waiver-swaps/latest"]["cards"][0]["net_delta"] = -1.0
        payloads["/api/waiver-swaps/latest"]["cards"][0]["move_out"] = {
            "name": "  AARON   JUDGE ",
            "age": 34,
        }

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("waivers_protected_anchor", codes)
        self.assertIn("waivers_nonpositive_delta", codes)

    def test_trade_index_requires_complete_age_and_value_provenance(self):
        payloads = healthy_payloads()
        payloads["/api/snapshot/latest"]["player_index"][1]["age"] = None
        payloads["/api/snapshot/latest"]["player_index"][2]["fppg"] = float("inf")

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("trade_index_age_coverage", codes)
        self.assertIn("trade_index_value_coverage", codes)

    def test_matchup_contract_rejects_legacy_claims_duplicates_and_unchecked_bridge(self):
        payloads = healthy_payloads()
        matchup = payloads["/api/snapshot/latest"]["matchup"]
        projection = matchup["projection"]
        projection.pop("scoring_basis")
        projection["probability_calibrated"] = True

        first = matchup["recommendations"]["recommendations"][0]
        first["replacement_card"]["proposal"]["contract"]["version"] = 1
        first["replacement_card"]["proposal"]["contract"]["slot_moves"].insert(
            1,
            {"order": 2, "player_id": "bridge", "from_slot": "UT", "to_slot": "OF"},
        )
        duplicate = copy.deepcopy(first)
        duplicate["rank"] = 2
        matchup["recommendations"]["recommendations"].append(duplicate)

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("matchup_scoring_basis", codes)
        self.assertIn("matchup_probability_claim", codes)
        self.assertIn("matchup_contract_version", codes)
        self.assertIn("matchup_dominated_duplicate", codes)
        self.assertIn("matchup_movability_coverage", codes)

    def test_uncalibrated_probability_cannot_drive_actionable_matchup_advice(self):
        payloads = healthy_payloads()
        matchup = payloads["/api/snapshot/latest"]["matchup"]
        recommendation = matchup["recommendations"]["recommendations"][0]
        recommendation["win_probability_delta"] = 0.02
        recommendation["replacement_card"]["projected_benefit"]["new_win_probability"] = 0.57

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        self.assertIn(
            "matchup_uncalibrated_action_claim",
            {item["code"] for item in report["failures"]},
        )

    def test_transport_failure_is_sanitized_and_fails(self):
        payloads = healthy_payloads()
        payloads.pop("/api/attention")

        report = monitor.evaluate_payloads(
            payloads,
            transport_errors={"/api/attention": "HTTP 503"},
            checked_at=NOW,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["failures"][0]["code"], "endpoint_unavailable")
        self.assertNotIn("Roster Player", monitor.render_markdown(report))

    def test_degraded_advice_readiness_warns_without_triggering_repair(self):
        payloads = healthy_payloads()
        payloads["/api/snapshot/latest"]["data_quality"]["lineup_slots"] = {"state": "partial"}
        payloads["/api/waiver-swaps/latest"]["data_quality"]["add_drop_recommendations_ready"] = False

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertTrue(report["ok"])
        codes = {item["code"] for item in report["warnings"]}
        self.assertEqual(codes, {"lineup_advice_paused", "waiver_advice_paused"})

    def test_explicitly_paused_projection_does_not_fail_monitor(self):
        payloads = healthy_payloads()
        snapshot = payloads["/api/snapshot/latest"]
        snapshot["data_quality"]["projection_ready"] = False
        snapshot["matchup"]["projection"] = None
        snapshot["matchup"]["recommendations"] = {
            "model_version": "matchup_projection_v4",
            "base_projection": None,
            "recommendations": [],
            "no_action": {"reason": "Projection data incomplete"},
        }

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertTrue(report["ok"])
        matchup_check = next(check for check in report["checks"] if check["name"] == "matchup")
        self.assertEqual(matchup_check["state"], "paused")


if __name__ == "__main__":
    unittest.main()
