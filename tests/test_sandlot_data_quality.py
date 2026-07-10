import unittest

import sandlot_data_quality
import sandlot_waivers
from sandlot_api import _snapshot_payload


def future_game(day=14):
    return {"date": f"2026-05-{day:02d}"}


def player(pid, *, slot="2B", fppg=2.0, future=True, positions="2B", slot_source=None, age=28):
    row = {
        "id": pid,
        "name": f"Player {pid}",
        "slot": slot,
        "positions": positions,
        "slot_source": slot_source or ("raw.statusId" if slot in {"BN", "IL", "IR", "RES", "MIN"} else "raw.lineupSlot"),
        "age": age,
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
        "free_agents": {
            "players": [
                {"id": "fa", "name": "Free Agent", "positions": "2B", "age": 29, "age_source": "raw.scorer.playerAge", "stats": {"FP/G": 4.0}},
            ],
        },
    }


class SnapshotDataQualityTests(unittest.TestCase):
    def test_good_snapshot_is_projection_and_recommendation_ready(self):
        quality = sandlot_data_quality.snapshot_data_quality(good_snapshot())

        self.assertTrue(quality["projection_ready"])
        self.assertTrue(quality["recommendations_ready"])
        self.assertTrue(quality["add_drop_recommendations_ready"])
        self.assertEqual(quality["future_games"]["covered_players"], 2)
        self.assertEqual(quality["fppg"]["covered_players"], 2)
        self.assertEqual(quality["free_agent_pool"]["usable_players"], 1)

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

    def test_schedule_backed_empty_future_games_are_real_coverage(self):
        snapshot = good_snapshot()
        for row in [snapshot["roster"]["rows"][0], snapshot["all_team_rosters"]["opp"]["rows"][0]]:
            row["future_games"] = []
            row["future_games_source"] = "mlb_schedule"
            row["future_games_status"] = "ok"
            row["future_games_scope"] = "team_games"

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["future_games"]["state"], "ok")
        self.assertEqual(quality["future_games"]["covered_players"], 2)
        self.assertEqual(quality["future_games"]["remaining_game_count"], 0)
        self.assertTrue(quality["future_games"]["zero_remaining_games"])

    def test_failed_schedule_mapping_does_not_count_as_future_game_coverage(self):
        snapshot = good_snapshot()
        for row in [snapshot["roster"]["rows"][0], snapshot["all_team_rosters"]["opp"]["rows"][0]]:
            row["future_games"] = []
            row["future_games_source"] = "mlb_schedule"
            row["future_games_status"] = "unresolved_team"
            row["future_games_reason"] = "could not resolve Fantrax team abbreviation XXX"

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["future_games"]["state"], "missing")
        self.assertEqual(quality["future_games"]["covered_players"], 0)
        self.assertEqual(quality["future_games"]["status_counts"], {"unresolved_team": 2})
        self.assertFalse(quality["projection_ready"])

    def test_missing_fppg_marks_projection_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0].pop("fppg")

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["fppg"]["state"], "partial")
        self.assertEqual(quality["fppg"]["covered_players"], 1)
        self.assertFalse(quality["projection_ready"])

    def test_nonfinite_or_absurd_fppg_marks_projection_not_ready(self):
        for value in (float("nan"), float("inf"), float("-inf"), 688.0):
            with self.subTest(value=value):
                snapshot = good_snapshot()
                snapshot["roster"]["rows"][0]["fppg"] = value

                quality = sandlot_data_quality.snapshot_data_quality(snapshot)

                self.assertEqual(quality["fppg"]["state"], "partial")
                self.assertFalse(quality["projection_ready"])

    def test_missing_position_marks_recommendations_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0].pop("positions")
        snapshot["roster"]["rows"][0].pop("slot")

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["eligibility"]["state"], "partial")
        self.assertFalse(quality["recommendations_ready"])

    def test_position_fallback_slot_source_marks_recommendations_not_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0]["slot_source"] = "position_fallback"

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["lineup_slots"]["state"], "missing")
        self.assertEqual(quality["lineup_slots"]["trusted_players"], 0)
        self.assertTrue(quality["recommendations_ready"])
        self.assertFalse(quality["lineup_recommendations_ready"])
        self.assertFalse(quality["add_drop_recommendations_ready"])
        self.assertFalse(quality["projection_ready"])
        self.assertEqual(quality["projection_slots"]["state"], "partial")
        self.assertIn(
            "Projection lineup-slot source usable for 1/2 active players",
            quality["projection_reasons"],
        )
        self.assertIn("Lineup-slot source trusted for 0/1 roster players", quality["lineup_recommendation_reasons"])

    def test_untrusted_opponent_slot_source_is_exposed_as_projection_diagnostic(self):
        snapshot = good_snapshot()
        snapshot["all_team_rosters"]["opp"]["rows"][0]["slot_source"] = "position_fallback"

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["lineup_slots"]["state"], "ok")
        self.assertEqual(quality["projection_slots"]["state"], "partial")
        self.assertFalse(quality["projection_ready"])

    def test_legacy_missing_slot_source_remains_projection_ready(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"][0].pop("slot_source")
        snapshot["all_team_rosters"]["opp"]["rows"][0].pop("slot_source")

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["projection_slots"]["state"], "ok")
        self.assertEqual(quality["projection_slots"]["usable_players"], 2)
        self.assertTrue(quality["projection_ready"])

    def test_untrusted_inactive_slot_does_not_block_projection(self):
        snapshot = good_snapshot()
        snapshot["roster"]["rows"].append(
            player("bench", slot="BN", positions="OF", slot_source="position_fallback")
        )

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["projection_slots"]["state"], "ok")
        self.assertEqual(quality["projection_slots"]["total_players"], 2)
        self.assertTrue(quality["projection_ready"])

    def test_missing_pitcher_probables_make_projection_partial(self):
        snapshot = good_snapshot()
        pitcher = snapshot["roster"]["rows"][0]
        pitcher.update({
            "slot": "SP",
            "positions": "SP",
            "slot_source": "raw.lineupSlot",
            "future_games": [],
            "future_games_source": "mlb_schedule",
            "future_games_status": "pitcher_probables_unavailable",
            "future_games_scope": "pitcher_probable_starts",
        })

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertEqual(quality["future_games"]["state"], "ok")
        self.assertEqual(quality["projection_future_games"]["state"], "partial")
        self.assertFalse(quality["projection_ready"])
        self.assertTrue(quality["recommendations_ready"])
        self.assertIn("Projection future-game coverage 1/2", quality["projection_reasons"])

    def test_short_reason_fails_closed_when_action_ready_flags_are_missing(self):
        legacy_quality = {
            "projection_ready": True,
            "recommendations_ready": True,
            "recommendation_reasons": [],
            "reasons": [],
        }

        self.assertEqual(
            sandlot_data_quality.short_reason(legacy_quality, purpose="lineup_recommendations"),
            "Lineup recommendation readiness is not explicitly trusted",
        )
        self.assertEqual(
            sandlot_data_quality.short_reason(legacy_quality, purpose="add_drop_recommendations"),
            "Add/drop recommendation readiness is not explicitly trusted",
        )

    def test_add_drop_pauses_when_free_agents_lack_trusted_value_or_age(self):
        snapshot = good_snapshot()
        snapshot["free_agents"] = {
            "players": [
                {
                    "id": "inferred",
                    "name": "Inferred Free Agent",
                    "positions": "OF",
                    "age": 27,
                    "stats": {"_cells": ["688", "", "27", "140.0", "12.1", "12%"]},
                },
                {
                    "id": "missing-age",
                    "name": "Missing Age",
                    "positions": "OF",
                    "stats": {"FP/G": 5.0},
                },
            ],
        }

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertTrue(quality["lineup_recommendations_ready"])
        self.assertFalse(quality["add_drop_recommendations_ready"])
        self.assertEqual(quality["free_agent_pool"]["state"], "missing")
        self.assertIn(
            "Dynasty-safe free-agent pool has 0/2 players with trusted per-game value and age",
            quality["add_drop_recommendation_reasons"],
        )

    def test_add_drop_pauses_when_numeric_age_has_no_provenance(self):
        snapshot = good_snapshot()
        snapshot["free_agents"] = {
            "players": [{
                "id": "untrusted-age",
                "name": "Untrusted Age",
                "positions": "OF",
                "age": 27,
                "stats": {"FP/G": 5.0},
            }],
        }

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertFalse(quality["add_drop_recommendations_ready"])
        self.assertEqual(quality["free_agent_pool"]["usable_players"], 0)

    def test_schema_checked_free_agent_cell_age_can_unlock_trusted_candidate(self):
        snapshot = good_snapshot()
        snapshot["free_agents"] = {
            "players": [
                {
                    "id": "cell-age",
                    "name": "Cell Age Free Agent",
                    "positions": "OF",
                    "stats": {
                        "FP/G": 5.0,
                        "_cells": ["688", "", "27", "140.0", "5.0", "12%"],
                    },
                },
            ],
        }

        quality = sandlot_data_quality.snapshot_data_quality(snapshot)

        self.assertTrue(quality["add_drop_recommendations_ready"])
        self.assertEqual(quality["free_agent_pool"]["usable_players"], 1)

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
