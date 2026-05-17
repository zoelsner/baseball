"""Drop-logging + drop-counting regression tests for _player_index (#38).

`_player_index` silently dropped malformed rows (non-dict rows, missing
id/name, duplicates, non-dict team buckets). With no observability, a bad
scrape that returned nulls or partial rows would propagate as a quiet
player-missing bug through trade picker, Skipper, waivers, and #14's
data-quality gates. These tests lock in: every drop emits a WARN log, and
callers can opt into a `drops` counter for #14 to consume.
"""

import logging
import unittest

from sandlot_api import _player_index


class PlayerIndexDropLoggingTests(unittest.TestCase):
    def test_logs_warning_when_row_is_not_a_dict(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": [None, {"id": "p1", "name": "OK"}]},
        }
        with self.assertLogs("sandlot_api", level=logging.WARNING) as cm:
            _player_index(data)
        self.assertTrue(
            any("non_dict_row" in msg for msg in cm.output),
            f"Expected a 'non_dict_row' WARN log; got {cm.output}",
        )

    def test_logs_warning_when_row_is_missing_id_or_name(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": [{"id": "p1"}, {"name": "No ID"}, {"id": "p2", "name": "OK"}]},
        }
        with self.assertLogs("sandlot_api", level=logging.WARNING) as cm:
            _player_index(data)
        msgs = cm.output
        self.assertTrue(
            any("missing_id_or_name" in msg for msg in msgs),
            f"Expected at least one 'missing_id_or_name' WARN log; got {msgs}",
        )

    def test_logs_warning_when_team_bucket_is_not_a_dict(self):
        data = {
            "team_id": "team-me",
            "all_team_rosters": {
                "team-bad": "not a dict",
                "team-me": {"is_me": True, "rows": [{"id": "p1", "name": "OK"}]},
            },
        }
        with self.assertLogs("sandlot_api", level=logging.WARNING) as cm:
            _player_index(data)
        self.assertTrue(
            any("non_dict_team" in msg for msg in cm.output),
            f"Expected a 'non_dict_team' WARN log; got {cm.output}",
        )

    def test_logs_warning_when_duplicate_id_is_dropped(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": [{"id": "p1", "name": "First"}]},
            "free_agents": {"players": [{"id": "p1", "name": "Duplicate"}]},
        }
        with self.assertLogs("sandlot_api", level=logging.WARNING) as cm:
            _player_index(data)
        self.assertTrue(
            any("duplicate" in msg for msg in cm.output),
            f"Expected a 'duplicate' WARN log; got {cm.output}",
        )


class PlayerIndexDropCounterTests(unittest.TestCase):
    """Drops counter is opt-in via a `drops` kwarg so existing callers stay unchanged."""

    def test_drops_counter_is_populated_for_all_reasons(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": [None, {"id": "p1", "name": "OK"}, {"name": "No ID"}]},
            "all_team_rosters": {
                "team-bad": "not a dict",
                "team-me": {"is_me": True, "rows": [{"id": "p1", "name": "OK"}]},  # duplicate of roster p1
            },
        }
        drops: dict[str, int] = {}
        _player_index(data, drops=drops)
        self.assertEqual(drops.get("non_dict_row"), 1)
        self.assertEqual(drops.get("missing_id_or_name"), 1)
        self.assertEqual(drops.get("non_dict_team"), 1)
        self.assertEqual(drops.get("duplicate"), 1)

    def test_drops_counter_is_zero_when_input_is_clean(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": [{"id": "p1", "name": "Player One"}]},
            "all_team_rosters": {
                "team-opp": {"rows": [{"id": "p2", "name": "Player Two"}]},
            },
            "free_agents": {"players": [{"id": "p3", "name": "Player Three"}]},
        }
        drops: dict[str, int] = {}
        rows = _player_index(data, drops=drops)
        self.assertEqual(len(rows), 3)
        self.assertEqual(drops.get("non_dict_row", 0), 0)
        self.assertEqual(drops.get("missing_id_or_name", 0), 0)
        self.assertEqual(drops.get("non_dict_team", 0), 0)
        self.assertEqual(drops.get("duplicate", 0), 0)

    def test_drops_counter_is_optional_and_existing_callers_unaffected(self):
        """Calls without `drops` kwarg still return the rows list (back-compat)."""
        data = {
            "team_id": "team-me",
            "roster": {"rows": [{"id": "p1", "name": "OK"}]},
        }
        rows = _player_index(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "OK")


if __name__ == "__main__":
    unittest.main()
