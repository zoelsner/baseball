import json
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import sandlot_api


class _UnsafeTradeClient:
    def stream(self, *args, **kwargs):
        yield (
            "token",
            """## Verdict: REJECT

### Weekly Impact — Uncertainty: Low
You lose 3.08 FP/G and gain zero this week.

### Rest-of-Season — Uncertainty: Medium
Net ROS impact: lose 150+ remaining FPts.

### Dynasty — Uncertainty: High
The long-term case depends on health and prospect risk.

### Replacement Value — Uncertainty: Low
Over nine games, the downgrade is 5.8–7.4 points.

### Do Nothing — Uncertainty: Low
Alonso gives you 3.08 FP/G for the rest of 2026.
""",
        )
        yield ("model", "test/model")


class _DelayedUnsafeTradeClient(_UnsafeTradeClient):
    def stream(self, *args, **kwargs):
        time.sleep(0.04)
        yield from super().stream(*args, **kwargs)


class TradeChatGuardTests(unittest.TestCase):
    def test_closing_trade_event_stream_stops_and_closes_the_model_iterator(self):
        closed = threading.Event()

        def model_events():
            try:
                while True:
                    yield ("token", "unsafe draft")
            finally:
                closed.set()

        event_stream = sandlot_api._trade_research_events(model_events())
        self.assertEqual(next(event_stream), ("model_event", ("token", "unsafe draft")))
        event_stream.close()

        self.assertTrue(closed.wait(timeout=1.0))

    def test_closing_trade_event_stream_cancels_a_blocked_provider_read(self):
        started = threading.Event()
        released = threading.Event()
        cancelled = threading.Event()

        def model_events():
            started.set()
            released.wait(timeout=2.0)
            yield ("token", "unsafe draft")

        def cancel() -> None:
            cancelled.set()
            released.set()

        with patch.object(sandlot_api, "TRADE_RESEARCH_HEARTBEAT_SECONDS", 0.005):
            event_stream = sandlot_api._trade_research_events(model_events(), on_cancel=cancel)
            self.assertEqual(
                next(event_stream),
                ("progress", {"type": "research_progress", "stage": "applying_guardrails"}),
            )
            self.assertTrue(started.is_set())
            event_stream.close()

        self.assertTrue(cancelled.wait(timeout=1.0))
        self.assertTrue(released.is_set())

    def test_stream_buffers_and_persists_only_the_guarded_trade_answer(self):
        prompt = (
            "Sandlot trade-analysis evidence: exact offer. "
            "The blocked evidence is: Cole Ragans: Currently on IR; return timing is not modeled.. "
            "The do-nothing alternative is to keep Pete Alonso at a verified current snapshot package rate of 3.08 FP/G. "
            "Roster consequence: moves out 1B. Internal replacement evidence: "
            "Best reserve cover: Andrew Vaughn (-0.78 FP/G vs outgoing). "
            "Current counter direction: ask for healthy value."
        )
        snapshot = {
            "id": 321,
            "taken_at": datetime.now(timezone.utc),
            "status": "success",
            "source": "test",
            "data": {
                "team_id": "me",
                "team_name": "Sandlot",
                "roster": {"rows": []},
                "standings": {"records": []},
            },
        }
        persisted = []

        def append_message(session_id, role, content, **kwargs):
            persisted.append({"role": role, "content": content, **kwargs})
            return len(persisted)

        with (
            patch.object(sandlot_api.sandlot_db, "get_or_create_default_session", return_value=1),
            patch.object(sandlot_api.sandlot_db, "latest_successful_snapshot", return_value=snapshot),
            patch.object(sandlot_api.sandlot_db, "list_chat_messages", return_value=[]),
            patch.object(sandlot_api.sandlot_db, "append_chat_message", side_effect=append_message),
            patch.object(sandlot_api, "_log_skipper_projection_surfaces"),
            patch.object(sandlot_api.sandlot_skipper, "SkipperClient", return_value=_UnsafeTradeClient()),
        ):
            response = TestClient(sandlot_api.app).post(
                "/api/skipper/messages",
                json={
                    "content": prompt,
                    "model": "deepseek/deepseek-v4-flash",
                    "reasoning": True,
                    "reasoning_effort": "high",
                    "web_search": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(frame.removeprefix("data: "))
            for frame in response.text.strip().split("\n\n")
            if frame.startswith("data: ")
        ]
        visible = "".join(event.get("text", "") for event in events if event.get("type") == "token")
        self.assertIn("Sandlot evidence guardrail applied", visible)
        self.assertNotIn("150+ remaining FPts", visible)
        self.assertNotIn("5.8–7.4 points", visible)
        self.assertFalse(any(event.get("type") == "replace" for event in events))

        assistant = next(row for row in persisted if row["role"] == "assistant")
        self.assertEqual(assistant["content"], visible)
        self.assertNotIn("for the rest of 2026", assistant["content"])

    def test_delayed_trade_stream_emits_safe_progress_before_guarded_answer(self):
        prompt = (
            "Sandlot trade-analysis evidence: exact offer. "
            "The blocked evidence is: health is unresolved.. "
            "The do-nothing alternative is to keep the outgoing player. "
            "Roster consequence: no verified roster improvement. "
            "Internal replacement evidence: No replacement is modeled. "
            "Current counter direction: ask for healthy value."
        )
        snapshot = {
            "id": 322,
            "taken_at": datetime.now(timezone.utc),
            "status": "success",
            "source": "test",
            "data": {"team_id": "me", "team_name": "Sandlot", "roster": {"rows": []}},
        }

        with (
            patch.object(sandlot_api, "TRADE_RESEARCH_HEARTBEAT_SECONDS", 0.005),
            patch.object(sandlot_api.sandlot_db, "get_or_create_default_session", return_value=1),
            patch.object(sandlot_api.sandlot_db, "latest_successful_snapshot", return_value=snapshot),
            patch.object(sandlot_api.sandlot_db, "list_chat_messages", return_value=[]),
            patch.object(sandlot_api.sandlot_db, "append_chat_message", return_value=1),
            patch.object(sandlot_api, "_log_skipper_projection_surfaces"),
            patch.object(sandlot_api.sandlot_skipper, "SkipperClient", return_value=_DelayedUnsafeTradeClient()),
        ):
            response = TestClient(sandlot_api.app).post(
                "/api/skipper/messages",
                json={"content": prompt, "reasoning": True, "web_search": True},
            )

        events = [
            json.loads(frame.removeprefix("data: "))
            for frame in response.text.strip().split("\n\n")
            if frame.startswith("data: ")
        ]
        token_index = next(i for i, event in enumerate(events) if event.get("type") == "token")
        self.assertEqual(events[0], {"type": "research_started", "stage": "researching"})
        self.assertTrue(any(event.get("type") == "research_progress" for event in events[:token_index]))
        pre_answer = json.dumps(events[:token_index])
        self.assertNotIn("150+ remaining FPts", pre_answer)
        self.assertNotIn("5.8–7.4 points", pre_answer)


if __name__ == "__main__":
    unittest.main()
