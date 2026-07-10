import unittest
from datetime import datetime, timezone

from scripts import sandlot_readonly_monitor as monitor


NOW = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)


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
            "roster": [{"id": "mine-1", "name": "Roster Player"}],
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
        self.assertEqual(
            [item["code"] for item in report["failures"]].count("waivers_nonpositive_delta"),
            2,
        )

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


if __name__ == "__main__":
    unittest.main()
