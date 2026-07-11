import unittest
from unittest.mock import Mock, patch

import fantrax_data


class FantraxLineupPolicyObservationTests(unittest.TestCase):
    def test_captures_only_sanitized_lineup_policy_candidates(self):
        raw = {
            "fantasySettings": {
                "leagueName": "Private League Name",
                "rosterSettings": {
                    "lineupChangePeriod": "WEEKLY",
                    "lineupLockScope": "INDIVIDUAL_GAME",
                    "lineupChangeOwnerEmail": "owner@example.com",
                    "lineupChangeDocs": "https://example.com/private",
                },
            },
            "ownerEmail": "owner@example.com",
        }

        policy = fantrax_data._lineup_change_policy_observation(raw, method="getLeagueRules")

        self.assertEqual(policy["state"], "observed_unclassified")
        self.assertIsNone(policy["cadence"])
        self.assertIsNone(policy["lock_scope"])
        self.assertEqual(policy["source"], "fantrax.getLeagueRules.raw")
        self.assertEqual(policy["candidates"], [
            {
                "path": "fantasySettings.rosterSettings.lineupChangePeriod",
                "value_type": "str",
                "hint": "weekly",
            },
            {
                "path": "fantasySettings.rosterSettings.lineupLockScope",
                "value_type": "str",
                "hint": "player_game",
            },
        ])
        self.assertNotIn("owner@example.com", str(policy))
        self.assertNotIn("Private League Name", str(policy))

    def test_does_not_export_unrelated_descendants_of_policy_named_container(self):
        raw = {
            "fantasySettings": {
                "rosterLock": {
                    "apiToken": "SECRET123",
                    "privateNote": "keep this private",
                    "lineupChangePeriod": "WEEKLY",
                },
            },
        }

        policy = fantrax_data._lineup_change_policy_observation(raw, method="getLeagueRules")

        self.assertEqual(policy["candidates"], [{
            "path": "fantasySettings.rosterLock.lineupChangePeriod",
            "value_type": "str",
            "hint": "weekly",
        }])
        self.assertNotIn("SECRET123", str(policy))
        self.assertNotIn("keep this private", str(policy))

    def test_missing_candidate_fields_fail_closed(self):
        policy = fantrax_data._lineup_change_policy_observation(
            {"fantasySettings": {"leagueName": "Example"}},
            method="getLeagueInfo",
        )

        self.assertEqual(policy["state"], "missing")
        self.assertEqual(policy["candidates"], [])
        self.assertIn("did not expose", policy["reason"])

    def test_dictionary_traversal_stops_at_the_node_budget(self):
        raw = {f"noise_{index}": index for index in range(6000)}
        raw["lineupChangePeriod"] = "WEEKLY"

        policy = fantrax_data._lineup_change_policy_observation(raw, method="getLeagueRules")

        self.assertEqual(policy["state"], "missing")
        self.assertEqual(policy["candidates"], [])


class FantraxRosterPolicyAcquisitionTests(unittest.TestCase):
    def test_captures_roster_policy_fields_without_unrelated_values(self):
        raw = {
            "displayedSelections": {"lineupChangeSystem": "CLASSIC"},
            "params": {"daily": False, "origDaily": False},
            "miscData": {
                "autoSubmitLineupChanges": True,
                "applyToFuturePeriods": True,
                "ownerEmail": "owner@example.com",
            },
        }

        policy = fantrax_data._lineup_change_policy_observation(
            raw,
            method="getTeamRosterInfo",
        )

        self.assertEqual(policy["state"], "observed_unclassified")
        self.assertEqual(policy["candidates"], [
            {"path": "displayedSelections.lineupChangeSystem", "value_type": "str", "hint": "classic"},
            {"path": "params.daily", "value_type": "bool", "hint": "not_daily"},
            {"path": "params.origDaily", "value_type": "bool", "hint": "not_daily"},
            {"path": "miscData.autoSubmitLineupChanges", "value_type": "bool", "hint": None},
            {"path": "miscData.applyToFuturePeriods", "value_type": "bool", "hint": None},
        ])
        self.assertNotIn("owner@example.com", str(policy))

    def test_roster_capture_is_opt_in_and_promoted_without_private_duplicate(self):
        api = Mock()
        raw = {
            "displayedSelections": {"lineupChangeSystem": "CLASSIC"},
            "miscData": {"statusTotals": []},
            "tables": [],
        }
        roster = fantrax_data._RawRoster(api, "team-1", raw)

        with patch.object(fantrax_data, "_team_roster", return_value=roster):
            normal = fantrax_data.extract_roster(api, "team-1")
            captured = fantrax_data.extract_roster(
                api,
                "team-1",
                capture_lineup_policy=True,
            )

        self.assertNotIn("_lineup_change_policy", normal)
        snapshot = {"roster": captured, "league_rules": None}
        fantrax_data._promote_roster_lineup_policy(snapshot)

        self.assertNotIn("_lineup_change_policy", snapshot["roster"])
        self.assertEqual(snapshot["league_rules"]["method"], "getTeamRosterInfo")
        self.assertEqual(
            snapshot["league_rules"]["lineup_change_policy"]["successful_methods"],
            ["getTeamRosterInfo"],
        )


if __name__ == "__main__":
    unittest.main()
