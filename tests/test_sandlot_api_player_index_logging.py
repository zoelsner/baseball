"""Drop-logging + drop-counting regression tests for _player_index (#38).

`_player_index` silently dropped malformed rows (non-dict rows, missing
id/name, non-dict team buckets). With no observability, a bad
scrape that returned nulls or partial rows would propagate as a quiet
player-missing bug through trade picker, Skipper, waivers, and #14's
data-quality gates. These tests lock in: malformed rows emit WARN logs, and
callers can opt into a `drops` counter for #14 to consume. Duplicate ids are
counted but not warning-logged because they are expected when Fantrax exposes
the same player through multiple snapshot sections.
"""

import logging
import unittest
from contextlib import contextmanager

from sandlot_api import _player_index


@contextmanager
def assert_no_warning_logs(test_case: unittest.TestCase, logger_name: str):
    if hasattr(test_case, "assertNoLogs"):
        with test_case.assertNoLogs(logger_name, level=logging.WARNING):
            yield
        return

    logger = logging.getLogger(logger_name)
    records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = CaptureHandler(level=logging.WARNING)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    if records:
        rendered = "\n".join(handler.format(record) for record in records)
        test_case.fail(f"Unexpected warning logs on {logger_name}:\n{rendered}")


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

    def test_counts_fully_blank_row_without_warning_log(self):
        data = {
            "team_id": "team-me",
            "all_team_rosters": {
                "team-opp": {"rows": [{"id": None, "name": None}, {"id": "p1", "name": "OK"}]},
            },
        }
        drops: dict[str, int] = {}
        with assert_no_warning_logs(self, "sandlot_api"):
            _player_index(data, drops=drops)
        self.assertEqual(drops.get("missing_id_or_name"), 1)

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

    def test_counts_duplicate_id_without_warning_log(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": [{"id": "p1", "name": "First"}]},
            "free_agents": {"players": [{"id": "p1", "name": "Duplicate"}]},
        }
        drops: dict[str, int] = {}
        with assert_no_warning_logs(self, "sandlot_api"):
            _player_index(data, drops=drops)
        self.assertEqual(drops.get("duplicate"), 1)


class PlayerIndexDropCounterTests(unittest.TestCase):
    """Drops counter is opt-in via a `drops` kwarg so existing callers stay unchanged."""

    def test_drops_counter_is_populated_for_all_reasons(self):
        data = {
            "team_id": "team-me",
            "roster": {"rows": [None, {"id": "p1", "name": "OK"}, {"name": "No ID"}]},
            "all_team_rosters": {
                "team-bad": "not a dict",
            },
            "free_agents": {"players": [{"id": "p1", "name": "Duplicate"}]},
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
