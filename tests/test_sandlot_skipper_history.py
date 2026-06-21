import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import sandlot_api


class SkipperHistoryTests(unittest.TestCase):
    def test_history_is_scoped_to_latest_snapshot(self):
        latest = {"id": 123}
        rows = [
            {
                "id": 9,
                "snapshot_id": 123,
                "role": "assistant",
                "content": "Current matchup read",
                "tier": 2,
                "model": "deterministic",
                "created_at": datetime(2026, 6, 21, tzinfo=timezone.utc),
            }
        ]

        with patch.object(sandlot_api.sandlot_db, "get_or_create_default_session", return_value=7), patch.object(
            sandlot_api.sandlot_db, "latest_successful_snapshot", return_value=latest
        ), patch.object(sandlot_api.sandlot_db, "list_chat_messages", return_value=rows) as list_messages:
            payload = sandlot_api.skipper_history()

        list_messages.assert_called_once_with(7, snapshot_id=123)
        self.assertEqual(payload["snapshot_id"], 123)
        self.assertEqual(payload["messages"][0]["snapshot_id"], 123)
        self.assertEqual(payload["messages"][0]["content"], "Current matchup read")


if __name__ == "__main__":
    unittest.main()
