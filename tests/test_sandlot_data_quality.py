import unittest

import sandlot_data_quality
import sandlot_waivers
from sandlot_api import _snapshot_payload


def future_game(day=14):
    return {"date": f"2026-05-{day:02d}"}


def player(pid, *, slot="2B", fppg=2.0, future=True, positions="2B"):
    row = {
        "id": pid,
        "name": f"Player {pid}",
        "slot": slot,
        "positions": positions,
    }
    if fppg is not None:
        row["fppg"] = fppg
    if future:
        row["future_games"] = [future_game()]
    return row


def good_snapshot():
    return {
        "team_id": "me",
        "matchup": {
            "my_score": 10,
            "opponent_score": 8,
            "opponent_team_id": "opp",
            "end": "2026-05-20",
            "complete": False,
        },
        "roster": {"rows": [player("mine", slot="2B", positions="2B")]},
        "all_team_rosters": {
            "opp": {"rows": [player("opp-player", slot="SS", positions="SS", fppg=1.5)]},
        },
    }


class SnapshotDataQualityTests(unittest.TestCase):
    def test_good_snapshot_is_projection_and_recommendation_ready(self):
        quality = sandlot_data_quality.snapshot_data_quality(good_snapshot())

        self.assertTrue(quality["projection_ready"])
        self.assertTrue(quality["recommendations_ready"])
        self.assertEqual(quality["future_games"]["covered_players"], 2)
        self.assertEqual(quality["fppg"]["covered_players"], 2)

    def test_missing_roster_marks_projection_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"] = {"rows": []}

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["my_roster"]["state"], "missing")
        self.assertFalse(quality["projection_ready"])
        self.assertIn("No my-roster rows in snapshot", quality["projection_reasons"])

    def test_missing_opponent_roster_marks_projection_not_ready(self):
        snapshot = good_snapshot()
        snapshot["all_team_rosters"] = {}

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["opponent_roster"]["state"], "missing")
        self.assertFalse(quality["projection_ready"])
        self.assertIn("No all-team rosters to find opponent", quality["projection_reasons"])

    def test_missing_future_games_marks_projection_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0].pop("future_games")
        snapshot["all_team_rosters"]["opp"]["rows"][0].pop("future_games")

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["future_games"]["state"], "missing")
        self.assertEqual(quality["future_games"]["covered_players"], 0)
        self.assertFalse(quality["projection_ready"])

    def test_empty_future_games_marks_projection_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0]["future_games"] = []
        snapshot["all_team_rosters"]["opp"]["rows"][0]["future_games"] = []

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["future_games"]["state"], "missing")
        self.assertEqual(quality["future_games"]["remaining_game_count"], 0)
        self.assertFalse(quality["projection_ready"])

    def test_missing_fppg_marks_projection_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0].pop("fppg")

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["fppg"]["state"], "partial")
        self.assertEqual(quality["fppg"]["covered_players"], 1)
        self.assertFalse(quality["projection_ready"])

    def test_missing_position_marks_recommendations_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0].pop("positions")
        snapshot["roster"]["rows"][0].pop("slot")

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["eligibility"]["state"], "partial")
        self.assertFalse(quality["recommendations_ready"])

    def test_snapshot_payload_surfaces_quality_and_suppresses_projection(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0].pop("future_games")
        snapshot["all_team_rosters"]["opp"]["rows"][0].pop("future_games")

        payload = _snapshot_payload({"id": 123, "data": snapshot})

        self.assertFalse(payload["data_quality"]["projection_ready"])
        self.assertIsNone(payload["matchup"]["projection"])

    def test_waiver_payload_pauses_cards_when_recommendation_data_incomplete(self):
        snapshot = good_snapshot()
        snapshot["free_agents"] = {"players": [player("fa", slot="BN", positions="2B", fppg=4.0)]}
        snapshot["roster"]["rows"][0].pop("future_games")

        payload = sandlot_waivers.payload_for_snapshot(
            {"id": 123, "data": snapshot},
            overlay_cached_ai=False,
        )

        self.assertEqual(payload["cards"], [])
        self.assertFalse(payload["data_quality"]["recommendations_ready"])
        self.assertIn("paused", payload["message"])


if __name__ == "__main__":
    unittest.main()
