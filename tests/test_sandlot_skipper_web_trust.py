import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import sandlot_api
import sandlot_skipper


def snapshot():
    return {
        "snapshot_id": "trust-test",
        "taken_at": "2026-06-21T12:00:00Z",
        "team_id": "me",
        "team_name": "Zach Sandlot",
        "roster": [
            {"id": "hudson", "name": "Bryan Hudson", "team": "CHW", "positions": "SP/RP", "slot": "RES"},
        ],
        "player_index": [
            {"id": "hudson", "name": "Bryan Hudson", "source": "mine"},
        ],
        "standings": [],
        "data_quality": {"projection_ready": True, "recommendations_ready": True},
    }


class SkipperWebTrustTests(unittest.TestCase):
    def test_web_search_decision_allows_missing_named_player_with_verify_intent(self):
        decision = sandlot_skipper.web_search_decision(
            "Can web verify Martin Perez against Bryan Hudson?",
            snapshot(),
            requested=True,
        )

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "missing_named_player")
        self.assertEqual(decision["missing_players"], ["Martin Perez"])

    def test_web_search_decision_does_not_allow_generic_stats_for_known_player(self):
        decision = sandlot_skipper.web_search_decision(
            "Compare Bryan Hudson ERA, WHIP, role, and strikeout stats.",
            snapshot(),
            requested=True,
        )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "snapshot_sufficient")

    def test_web_search_decision_missing_player_index_does_not_blanket_allow_web(self):
        payload = snapshot()
        payload["player_index"] = []

        decision = sandlot_skipper.web_search_decision(
            "Where am I weakest?",
            payload,
            requested=True,
        )

        self.assertFalse(decision["allowed"])
        self.assertIn("player_index_missing", decision["signals"])

    def test_web_search_decision_disabled_user_still_reports_missing_data_signals(self):
        decision = sandlot_skipper.web_search_decision(
            "Can web verify Martin Perez?",
            snapshot(),
            requested=False,
        )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "disabled_by_user")
        self.assertIn("named_player_missing_from_snapshot", decision["signals"])
        self.assertEqual(decision["missing_players"], ["Martin Perez"])

    def test_source_classification_marks_trusted_domains_and_trims_content(self):
        trusted = sandlot_skipper.classify_source({
            "url": "https://m.espn.com/mlb/player/_/id/31098/martin-perez",
            "title": "Martin Perez",
            "content": "x" * 400,
        })
        supplemental = sandlot_skipper.classify_source({"url": "https://notmlb.com/player"})

        self.assertEqual(trusted["domain"], "espn.com")
        self.assertEqual(trusted["trust"], "trusted")
        self.assertEqual(trusted["source_name"], "ESPN")
        self.assertEqual(len(trusted["content"]), 280)
        self.assertEqual(supplemental["trust"], "supplemental")

    def test_source_summary_counts_distinct_domains(self):
        summary = sandlot_skipper.source_summary([
            {"url": "https://www.espn.com/mlb/player/1"},
            {"url": "https://espn.com/mlb/player/2"},
            {"url": "https://fantasyteamadvice.com/mlb/player/3"},
        ])

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["trusted"], 1)
        self.assertEqual(summary["supplemental"], 1)
        self.assertEqual(summary["trusted_domains"], ["espn.com"])

    def test_quality_uses_actual_web_usage_not_permission(self):
        quality = sandlot_skipper.assess_reply_quality(
            "Hudson is on your reserve slot.",
            web_decision={"allowed": True, "reason": "missing_named_player"},
            sources=[],
            web_search_requests=0,
        )

        self.assertEqual(quality["level"], "good")
        self.assertEqual(quality["label"], "Snapshot read")

    def test_quality_marks_supplemental_only_sources_as_caution(self):
        quality = sandlot_skipper.assess_reply_quality(
            "Public web says something.",
            web_decision={"allowed": True, "reason": "missing_named_player"},
            sources=[{"url": "https://example.com/player"}],
            web_search_requests=1,
        )

        self.assertEqual(quality["level"], "mixed")
        self.assertEqual(quality["label"], "Supplemental sources")

    def test_quality_flags_web_usage_without_captured_sources(self):
        quality = sandlot_skipper.assess_reply_quality(
            "Public web says something.",
            web_decision={"allowed": True, "reason": "missing_named_player"},
            sources=[],
            web_search_requests=1,
        )

        self.assertEqual(quality["level"], "risky")
        self.assertEqual(quality["label"], "Thin sourcing")

    def test_quality_marks_trusted_web_context_as_verify_first(self):
        quality = sandlot_skipper.assess_reply_quality(
            "MLB.com supports the public context.",
            web_decision={"allowed": True, "reason": "missing_named_player"},
            sources=[{"url": "https://www.mlb.com/player/martin-perez-527048"}],
            web_search_requests=1,
        )

        self.assertEqual(quality["level"], "mixed")
        self.assertEqual(quality["sources"]["trusted"], 1)

    def test_deterministic_quality_respects_incomplete_projection_data(self):
        quality = sandlot_skipper.assess_reply_quality(
            "Data incomplete — score-based view only.",
            deterministic=True,
            data_quality={"projection_ready": False},
            web_decision={"allowed": False, "reason": "deterministic_snapshot_reply"},
        )

        self.assertEqual(quality["level"], "mixed")
        self.assertEqual(quality["label"], "Limited snapshot")

    def test_quality_disabled_web_does_not_downgrade_snapshot_sufficient_answer(self):
        quality = sandlot_skipper.assess_reply_quality(
            "Your record is 6-4.",
            web_decision={"allowed": False, "reason": "disabled_by_user", "signals": []},
            sources=[],
            web_search_requests=0,
        )

        self.assertEqual(quality["level"], "good")
        self.assertEqual(quality["label"], "Snapshot read")

    def test_quality_disabled_web_cautions_when_external_context_was_requested(self):
        quality = sandlot_skipper.assess_reply_quality(
            "I cannot verify Martin Perez with web fallback off.",
            web_decision={
                "allowed": False,
                "reason": "disabled_by_user",
                "signals": ["public_context_requested", "named_player_missing_from_snapshot"],
            },
            sources=[],
            web_search_requests=0,
        )

        self.assertEqual(quality["level"], "mixed")
        self.assertEqual(quality["label"], "Snapshot only")

    def test_skipper_sse_persists_metadata_and_done_confidence(self):
        class FakeSkipperClient:
            def stream(self, *args, **kwargs):
                yield ("source", {
                    "url": "https://www.mlb.com/player/martin-perez-527048",
                    "title": "Martin Perez Stats",
                    "content": "Pitcher profile excerpt",
                })
                yield ("web_search_requests", 1)
                yield ("token", "Perez needs Fantrax verification.")
                yield ("model", "test/model")

        appended = []

        def append_message(session_id, role, content, **kwargs):
            appended.append({"role": role, "content": content, **kwargs})
            return len(appended)

        with patch.object(sandlot_api.sandlot_db, "get_or_create_default_session", return_value=1), \
             patch.object(sandlot_api.sandlot_db, "latest_successful_snapshot", return_value={"id": 10}), \
             patch.object(sandlot_api, "_snapshot_payload", return_value=snapshot()), \
             patch.object(sandlot_api.sandlot_db, "list_chat_messages", return_value=[]), \
             patch.object(sandlot_api.sandlot_db, "append_chat_message", side_effect=append_message), \
             patch.object(sandlot_api, "_log_skipper_projection_surfaces"), \
             patch.object(sandlot_api.sandlot_skipper, "SkipperClient", return_value=FakeSkipperClient()):
            response = TestClient(sandlot_api.app).post(
                "/api/skipper/messages",
                json={"content": "Can web verify Martin Perez?", "web_search": True},
            )

        self.assertEqual(response.status_code, 200)
        frames = [
            json.loads(line.removeprefix("data: ").strip())
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        done = [frame for frame in frames if frame.get("type") == "done"][0]
        sources = [frame for frame in frames if frame.get("type") == "sources"][0]["sources"]
        assistant = [row for row in appended if row["role"] == "assistant"][0]

        self.assertTrue(done["web_search_allowed"])
        self.assertTrue(done["web_search"])
        self.assertEqual(done["confidence"]["level"], "mixed")
        self.assertEqual(sources[0]["trust"], "trusted")
        self.assertEqual(assistant["metadata"]["confidence"]["level"], "mixed")
        self.assertEqual(assistant["metadata"]["sources"][0]["source_name"], "MLB.com")


if __name__ == "__main__":
    unittest.main()
