import unittest
from datetime import datetime, timedelta, timezone

from sandlot_api import _freshness


class SandlotFreshnessTests(unittest.TestCase):
    def test_expected_cron_gap_stays_fresh(self):
        taken_at = datetime.now(timezone.utc) - timedelta(hours=16, minutes=30)

        self.assertEqual(_freshness(taken_at)["state"], "fresh")

    def test_missed_cron_window_is_stale(self):
        taken_at = datetime.now(timezone.utc) - timedelta(hours=20)

        self.assertEqual(_freshness(taken_at)["state"], "stale")

    def test_more_than_day_and_half_is_old(self):
        taken_at = datetime.now(timezone.utc) - timedelta(hours=40)

        self.assertEqual(_freshness(taken_at)["state"], "old")


if __name__ == "__main__":
    unittest.main()
