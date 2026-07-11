import unittest
from unittest.mock import Mock

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


class FantraxLeagueRulesAcquisitionTests(unittest.TestCase):
    @staticmethod
    def _response(*payloads):
        response = Mock(status_code=200)
        response.json.return_value = {"responses": list(payloads)}
        return response

    def test_later_policy_response_is_not_masked_by_first_success(self):
        session = Mock()
        session.post.return_value = self._response(
            {"leagueName": "Private"},
            {"settings": {"lineupChangePeriod": "WEEKLY"}},
            *[{"error": "unsupported"} for _ in range(4)],
        )

        rules = fantrax_data.extract_league_rules(session, "league-1")

        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(session.post.call_args.kwargs["timeout"], 15)
        self.assertEqual(len(session.post.call_args.kwargs["json"]["msgs"]), 6)
        self.assertEqual(rules["method"], "getLeagueRules")
        self.assertEqual(rules["policy_method"], "getLeagueInfo")
        self.assertEqual(rules["lineup_change_policy"]["state"], "observed_unclassified")
        self.assertEqual(rules["lineup_change_policy"]["candidates"][0]["hint"], "weekly")
        self.assertEqual(
            rules["lineup_change_policy"]["successful_methods"],
            ["getLeagueRules", "getLeagueInfo"],
        )
        self.assertNotIn("Private", str(rules["lineup_change_policy"]))

    def test_scoring_and_policy_can_come_from_different_methods(self):
        session = Mock()
        session.post.return_value = self._response(
            {"categories": [{"name": "HR", "points": 4}]},
            {"settings": {"lineupLockScope": "INDIVIDUAL_GAME"}},
            *[{"errorMsg": "unsupported"} for _ in range(4)],
        )

        rules = fantrax_data.extract_league_rules(session, "league-1")

        self.assertEqual(rules["method"], "getLeagueRules")
        self.assertEqual(rules["policy_method"], "getLeagueInfo")
        self.assertEqual(rules["scoring_method"], "getLeagueRules")
        self.assertEqual(rules["scoring_categories"], [{"name": "HR", "points": 4}])

    def test_all_successful_methods_remain_fail_closed_without_policy_fields(self):
        session = Mock()
        session.post.return_value = self._response(*[{"ok": True} for _ in range(6)])

        rules = fantrax_data.extract_league_rules(session, "league-1")

        policy = rules["lineup_change_policy"]
        self.assertEqual(rules["method"], "getLeagueRules")
        self.assertEqual(policy["state"], "missing")
        self.assertEqual(policy["candidates"], [])
        self.assertEqual(len(policy["methods_checked"]), 6)
        self.assertEqual(len(policy["successful_methods"]), 6)

    def test_richest_policy_response_wins_without_persisting_its_raw_payload(self):
        session = Mock()
        session.post.return_value = self._response(
            {"settings": {"lineupChangePeriod": "WEEKLY"}, "private": "first raw"},
            {
                "settings": {
                    "lineupChangePeriod": "WEEKLY",
                    "lineupLockScope": "INDIVIDUAL_GAME",
                },
                "private": "later raw",
            },
            *[{"error": "unsupported"} for _ in range(4)],
        )

        rules = fantrax_data.extract_league_rules(session, "league-1")

        self.assertEqual(rules["method"], "getLeagueRules")
        self.assertEqual(rules["policy_method"], "getLeagueInfo")
        self.assertEqual(
            rules["lineup_change_policy"]["candidates"],
            [
                {"path": "settings.lineupChangePeriod", "value_type": "str", "hint": "weekly"},
                {"path": "settings.lineupLockScope", "value_type": "str", "hint": "player_game"},
            ],
        )
        self.assertEqual(rules["raw"]["private"], "first raw")


if __name__ == "__main__":
    unittest.main()
