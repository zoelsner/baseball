"""Regression tests for sandlot_waivers warm-path hash gating (#34)."""

import os
import unittest
from unittest.mock import Mock, patch

import sandlot_db
import sandlot_skipper
import sandlot_waivers


def _fake_snapshot_row():
    return {
        "id": 42,
        "taken_at": "2026-05-17T00:00:00Z",
        "data": {"team_name": "My Team"},
    }


def _fake_payload():
    return {
        "cards": [
            {
                "id": "card1",
                "rank": 1,
                "add": {"name": "Add Player"},
                "move_out": {"name": "Drop Player"},
                "net_delta": 1.5,
                "confidence": "High",
                "fit": "good",
                "fills_position": "2B",
                "evidence_chips": [],
                "dynasty_note": "",
                "why": "deterministic why",
                "risk": "deterministic risk",
            },
        ],
        "freshness": {"state": "fresh"},
        "data_quality": None,
        "diagnostics": {"weak_positions": ["2B"]},
        "message": None,
    }


class WaiverWarmHashGateTests(unittest.TestCase):
    """Warm path must compare input_hash to decide regeneration, not just existence.

    Bug context (#34): `sandlot_waivers.warm_latest_waiver_ai` checked
    `if get_ai_brief(...)` and skipped whenever any row existed, never comparing
    `input_hash`. Stale briefs persisted forever. Read-path at :768 and :793
    already gates on hash; warm-path must match.
    """

    def setUp(self):
        self.row = _fake_snapshot_row()
        self.payload = _fake_payload()
        self.card = self.payload["cards"][0]
        self.fresh_swap_hash = sandlot_waivers._hash_context(
            sandlot_waivers._swap_prompt_context(self.row, self.card)
        )
        self.fresh_refresh_hash = sandlot_waivers._hash_context(
            sandlot_waivers._refresh_prompt_context(self.row, self.payload)
        )

    def _run_warm(self, brief_results):
        """Mock all DB/network calls; return (api_result, list of set_ai_brief invocations)."""
        set_calls = []

        def fake_get(sid, brief_type, subject):
            return brief_results.get((brief_type, subject))

        def fake_set(sid, brief_type, subject, text, model, input_hash):
            set_calls.append({
                "brief_type": brief_type,
                "subject": subject,
                "input_hash": input_hash,
            })

        client = Mock()
        client.complete = Mock(return_value=('{"why":"x","risk":"y"}', "kimi"))

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), \
             patch.object(sandlot_db, "init_schema"), \
             patch.object(sandlot_db, "snapshot_by_id", return_value=None), \
             patch.object(sandlot_db, "latest_successful_snapshot", return_value=self.row), \
             patch.object(sandlot_waivers, "payload_for_snapshot", return_value=self.payload), \
             patch.object(sandlot_skipper, "SkipperClient", return_value=client), \
             patch.object(sandlot_db, "get_ai_brief", side_effect=fake_get), \
             patch.object(sandlot_db, "set_ai_brief", side_effect=fake_set):
            result = sandlot_waivers.warm_latest_waiver_ai()
        return result, set_calls

    def test_swap_warm_regenerates_when_input_hash_differs(self):
        stale_cached = {
            "input_hash": "STALE_HASH_DOES_NOT_MATCH",
            "text": '{"why":"old","risk":"old"}',
            "model": "kimi",
        }
        _, set_calls = self._run_warm({
            (sandlot_waivers.BRIEF_TYPE_SWAP, "card1"): stale_cached,
            (sandlot_waivers.BRIEF_TYPE_REFRESH, sandlot_waivers.REFRESH_SUBJECT): None,
        })
        swap_writes = [c for c in set_calls if c["brief_type"] == sandlot_waivers.BRIEF_TYPE_SWAP and c["subject"] == "card1"]
        self.assertEqual(
            len(swap_writes), 1,
            f"Expected swap brief to be regenerated when cached hash is stale; set_calls={set_calls}",
        )
        self.assertEqual(swap_writes[0]["input_hash"], self.fresh_swap_hash)

    def test_swap_warm_skips_when_input_hash_matches(self):
        fresh_cached = {
            "input_hash": self.fresh_swap_hash,
            "text": '{"why":"ok","risk":"ok"}',
            "model": "kimi",
        }
        fresh_refresh_cached = {
            "input_hash": self.fresh_refresh_hash,
            "text": "rb",
            "model": "kimi",
        }
        _, set_calls = self._run_warm({
            (sandlot_waivers.BRIEF_TYPE_SWAP, "card1"): fresh_cached,
            (sandlot_waivers.BRIEF_TYPE_REFRESH, sandlot_waivers.REFRESH_SUBJECT): fresh_refresh_cached,
        })
        swap_writes = [c for c in set_calls if c["brief_type"] == sandlot_waivers.BRIEF_TYPE_SWAP]
        self.assertEqual(
            len(swap_writes), 0,
            f"Expected no swap regeneration when cached hash matches; set_calls={set_calls}",
        )

    def test_refresh_warm_regenerates_when_input_hash_differs(self):
        stale_cached = {
            "input_hash": "STALE",
            "text": "old refresh brief",
            "model": "kimi",
        }
        _, set_calls = self._run_warm({
            (sandlot_waivers.BRIEF_TYPE_SWAP, "card1"): None,
            (sandlot_waivers.BRIEF_TYPE_REFRESH, sandlot_waivers.REFRESH_SUBJECT): stale_cached,
        })
        refresh_writes = [c for c in set_calls if c["brief_type"] == sandlot_waivers.BRIEF_TYPE_REFRESH]
        self.assertEqual(
            len(refresh_writes), 1,
            f"Expected refresh brief to be regenerated when cached hash is stale; set_calls={set_calls}",
        )
        self.assertEqual(refresh_writes[0]["input_hash"], self.fresh_refresh_hash)

    def test_refresh_warm_skips_when_input_hash_matches(self):
        fresh_cached = {
            "input_hash": self.fresh_refresh_hash,
            "text": "fresh refresh brief",
            "model": "kimi",
        }
        fresh_swap_cached = {
            "input_hash": self.fresh_swap_hash,
            "text": '{"why":"ok","risk":"ok"}',
            "model": "kimi",
        }
        _, set_calls = self._run_warm({
            (sandlot_waivers.BRIEF_TYPE_SWAP, "card1"): fresh_swap_cached,
            (sandlot_waivers.BRIEF_TYPE_REFRESH, sandlot_waivers.REFRESH_SUBJECT): fresh_cached,
        })
        refresh_writes = [c for c in set_calls if c["brief_type"] == sandlot_waivers.BRIEF_TYPE_REFRESH]
        self.assertEqual(
            len(refresh_writes), 0,
            f"Expected no refresh regeneration when cached hash matches; set_calls={set_calls}",
        )


if __name__ == "__main__":
    unittest.main()
