from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import mlb_stats
import fantrax_data
import sandlot_api
import sandlot_receipts
import sandlot_trade_evidence as evidence


LEAGUE = "lydahdo6mhcvnob7"
CAPTURED = datetime(2026, 7, 12, 20, 0, tzinfo=timezone.utc)
SNAPSHOT_AT = datetime(2026, 7, 12, 20, 1, tzinfo=timezone.utc)


def periods():
    return [
        {"period_number": "17", "period_name": "Period 17", "start": "2026-07-13", "end": "2026-07-26", "regular_season": True},
        {"period_number": "18", "period_name": "Period 18", "start": "2026-07-27", "end": "2026-08-02", "regular_season": True},
    ]


def schedule():
    return {"dates": [
        {"games": [
            {"gamePk": 2, "gameDate": "2026-07-13T23:10:00Z", "status": {"detailedState": "Scheduled"}},
            {"gamePk": 1, "gameDate": "2026-07-13T17:05:00Z", "status": {"detailedState": "Scheduled"}},
        ]},
        {"games": [
            {"gamePk": 3, "gameDate": "2026-07-27T23:05:00Z", "status": {"detailedState": "Scheduled"}},
        ]},
    ]}


def player_snapshot():
    return {
        "roster": {"rows": [{
            "id": "give", "name": "Give Player", "team": "NYY", "positions": "OF", "all_positions": ["OF"],
            "slot": "OF", "slot_source": "fantrax.raw.position",
        }]},
        "all_team_rosters": {"other": {"rows": [{
            "id": "get", "name": "Get Player", "team": "SEA", "positions": "SP/RP", "all_positions": ["SP", "RP"],
            "slot": "RES", "slot_source": "fantrax.raw.position",
        }]}},
    }


def identity_index():
    ids = {"Give Player": 1, "Get Player": None}
    return evidence.build_player_identity_index(
        snapshot=player_snapshot(), observed_at=CAPTURED,
        resolver=lambda name, _team, _season: {
            "status": "resolved_unique_name" if ids[name] else "not_found",
            "mlb_id": ids[name], "source": "mlb_stats_active_players_v1",
        },
        season=2026,
    )


def outcome_contract(*, cutoff="2026-07-12T20:02:00Z", calendar=None, identities=None):
    return evidence.build_trade_outcome_contract(
        league_id=LEAGUE, team_id="team", snapshot_id=281,
        snapshot_taken_at=SNAPSHOT_AT, generated_at=cutoff,
        give_ids=["give"], get_ids=["get"],
        origin={"kind": "manual_entry", "fantrax_trade_id": None},
        calendar=calendar or evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=schedule(), captured_at=CAPTURED,
        ),
        identity_index=identities or identity_index(),
    )


def grade_result():
    return {
        "snapshot_id": 281,
        "letter_grade": "B+", "fairness": 0.8, "my_delta": 1.5, "their_delta": -1.5,
        "age_delta": -2.0, "my_give_fppg": 4.0, "my_get_fppg": 5.5,
        "value_basis": "current_snapshot_fppg", "grade_scope": "current_rate_only",
        "dynasty_complete": False,
        "my_give": [{"id": "give", "name": "Give Player", "team": "NYY", "positions": "OF", "fppg": 4.0, "age": 30}],
        "my_get": [{"id": "get", "name": "Get Player", "team": "SEA", "positions": "SP/RP", "fppg": 5.5, "age": 28}],
        "analysis": {"horizons": [{"key": "current_rate", "status": "modeled", "value": 1.5}]},
        "eligibility_evidence": {
            "policy_version": "trade_eligibility_v1", "all_checks_passed": True,
            "participants": [
                {"side": "give", "player_id": "give", "slot": "OF", "age": 30, "age_source": "fantrax", "protected_trade_player": False, "requires_manual_dynasty_review": False, "fppg_valid": True},
                {"side": "get", "player_id": "get", "slot": "RES", "age": 28, "age_source": "fantrax", "protected_trade_player": False, "requires_manual_dynasty_review": False, "fppg_valid": True},
            ],
        },
    }


class TradePeriodCalendarTests(unittest.TestCase):
    def test_calendar_is_canonical_and_freezes_earliest_exact_game(self):
        first = evidence.build_period_calendar(
            league_id=LEAGUE, periods=list(reversed(periods())), schedule_payload=schedule(), captured_at=CAPTURED,
        )
        reversed_schedule = {"dates": list(reversed(schedule()["dates"]))}
        reversed_schedule["dates"][-1]["games"] = list(reversed(reversed_schedule["dates"][-1]["games"]))
        second = evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=reversed_schedule,
            captured_at="2026-07-12T20:05:00Z",
        )

        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["periods"][0]["first_scoring_event_at"], "2026-07-13T17:05:00+00:00")
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertNotEqual(first["capture_hash"], second["capture_hash"])
        evidence.validate_period_calendar(first)

    def test_postponed_game_is_not_used_as_first_scoring_event(self):
        payload = schedule()
        payload["dates"][0]["games"][1]["status"]["detailedState"] = "Postponed"
        calendar = evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=payload, captured_at=CAPTURED,
        )
        self.assertEqual(calendar["periods"][0]["first_scoring_event_at"], "2026-07-13T23:10:00+00:00")

    def test_suspended_game_conservatively_remains_the_first_scoring_event(self):
        payload = schedule()
        payload["dates"][0]["games"][1]["status"]["detailedState"] = "Suspended"
        calendar = evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=payload, captured_at=CAPTURED,
        )
        self.assertEqual(calendar["periods"][0]["first_scoring_event_at"], "2026-07-13T17:05:00+00:00")

    def test_duplicate_or_overlapping_periods_fail_closed(self):
        invalid = periods() + [{
            "period_number": "18", "period_name": "Duplicate", "start": "2026-07-20", "end": "2026-07-28",
            "regular_season": True,
        }]
        calendar = evidence.build_period_calendar(
            league_id=LEAGUE, periods=invalid, schedule_payload=schedule(), captured_at=CAPTURED,
        )
        self.assertEqual(calendar["status"], "invalid")
        self.assertIn("duplicate_period_number", calendar["structural_reasons"])
        self.assertIn("overlapping_periods", calendar["structural_reasons"])

    def test_tampered_calendar_is_rejected(self):
        calendar = evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=schedule(), captured_at=CAPTURED,
        )
        calendar["periods"][0]["first_scoring_event_at"] = "2026-07-13T01:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "content hash"):
            evidence.validate_period_calendar(calendar)


class TradeIdentityTests(unittest.TestCase):
    def test_roles_freeze_one_or_multiple_exact_scoring_entities(self):
        hitter = evidence.scoring_entity_evidence({"id": "h", "positions": "OF/UT"})
        two_way = evidence.scoring_entity_evidence({"id": "tw", "positions": "UT/SP"})
        unknown = evidence.scoring_entity_evidence({"id": "x", "positions": "TWP", "slot": "RES", "slot_source": "raw"})

        self.assertEqual(hitter["scoring_entities"], [{"fantrax_scorer_id": "h", "scoring_role": "hitting"}])
        self.assertEqual(two_way["scoring_entities"], [
            {"fantrax_scorer_id": "tw", "scoring_role": "hitting"},
            {"fantrax_scorer_id": "tw", "scoring_role": "pitching"},
        ])
        self.assertEqual(two_way["status"], "ambiguous")
        self.assertEqual(unknown["status"], "ambiguous")

    def test_trusted_slot_selects_role_and_detects_eligibility_conflict(self):
        selected = evidence.scoring_entity_evidence({
            "id": "tw", "positions": "UT/SP", "slot": "OF", "slot_source": "fantrax.assigned_slot",
        })
        conflict = evidence.scoring_entity_evidence({
            "id": "bad", "positions": "OF", "slot": "SP", "slot_source": "fantrax.assigned_slot",
        })
        reserve = evidence.scoring_entity_evidence({
            "id": "res", "positions": "SP/RP", "slot": "RES", "slot_source": "fantrax.assigned_slot",
        })

        self.assertEqual(selected["status"], "resolved")
        self.assertEqual(selected["scoring_entities"], [{"fantrax_scorer_id": "tw", "scoring_role": "hitting"}])
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(conflict["scoring_entities"], [])
        self.assertEqual(reserve["status"], "resolved")
        self.assertEqual(reserve["scoring_entities"], [{"fantrax_scorer_id": "res", "scoring_role": "pitching"}])

    def test_fallback_and_unknown_slot_sources_never_resolve_dual_role(self):
        for source in ("fallback", "FALLBACK", "unknown", "position_fallback"):
            with self.subTest(source=source):
                role = evidence.scoring_entity_evidence({
                    "id": "tw", "positions": "UT/SP", "slot": "OF", "slot_source": source,
                })
                self.assertEqual(role["status"], "ambiguous")
                self.assertEqual(role["reason"], "multiple_eligible_roles")

    def test_missing_optional_mlb_id_does_not_block_exact_fantrax_role(self):
        contract = outcome_contract()
        self.assertTrue(contract["eligible"])
        get_asset = next(item for item in contract["assets"] if item["side"] == "get")
        self.assertEqual(get_asset["mlb_identity"]["status"], "not_found")
        self.assertIsNone(get_asset["mlb_identity"]["mlb_id"])

    def test_ambiguous_fantrax_role_blocks_contract_but_not_contract_creation(self):
        snapshot = player_snapshot()
        snapshot["all_team_rosters"]["other"]["rows"][0]["positions"] = "TWP"
        snapshot["all_team_rosters"]["other"]["rows"][0]["all_positions"] = []
        index = evidence.build_player_identity_index(
            snapshot=snapshot, observed_at=CAPTURED,
            resolver=lambda *_: {"status": "not_found", "mlb_id": None, "source": "test"},
            season=2026,
        )
        contract = outcome_contract(identities=index)
        self.assertFalse(contract["eligible"])
        self.assertIn(
            {"code": "fantrax_scoring_role_ambiguous", "fantrax_id": "get"},
            contract["blocking_reasons"],
        )


class TradeOutcomeContractTests(unittest.TestCase):
    def test_trade_receipt_v3_hashes_frozen_horizon_and_identity_contract(self):
        calendar = evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=schedule(), captured_at=CAPTURED,
        )
        snapshot = {
            "id": 281, "taken_at": None, "timestamp": None, "league_id": LEAGUE, "team_id": "team",
            "data": {"trade_horizon_calendar": calendar, "trade_player_identities": identity_index()},
        }
        fantrax_data._complete_snapshot_observation(snapshot, completed_at=SNAPSHOT_AT)
        snapshot["taken_at"] = snapshot["timestamp"]
        receipt = sandlot_receipts.build_trade_assessment_receipt(
            snapshot=snapshot, result=grade_result(), generated_at=datetime(2026, 7, 12, 20, 2, tzinfo=timezone.utc),
        )
        contract = receipt["recommendation"]["outcome_contract"]

        self.assertEqual(receipt["builder_version"], "trade_assessment_v3")
        self.assertTrue(contract["eligible"])
        self.assertEqual(contract["target_period"]["period_number"], "17")
        self.assertEqual(contract["target_period"]["candidate_game_count"], 2)
        self.assertEqual(contract["target_period"]["first_game"]["game_pk"], 1)
        self.assertEqual(contract["target_period"]["period_close_at"], "2026-07-27T04:00:00+00:00")
        self.assertEqual(contract["target_period"]["maturity_at"], "2026-07-28T04:00:00+00:00")
        public_target = sandlot_api._public_trade_target_period(contract["target_period"])
        self.assertNotIn("candidate_games", public_target)
        self.assertNotIn("candidate_games_hash", public_target)
        changed = copy.deepcopy(calendar)
        changed["periods"][0]["first_scoring_event_at"] = "2026-07-13T16:05:00+00:00"
        changed["periods"][0]["first_game"]["game_at"] = "2026-07-13T16:05:00+00:00"
        changed["periods"][0]["candidate_games"][0]["game_at"] = "2026-07-13T16:05:00+00:00"
        changed["periods"][0]["candidate_games_hash"] = evidence._sha256({
            "games": changed["periods"][0]["candidate_games"],
        })
        content = {key: value for key, value in changed.items() if key not in {"captured_at", "content_hash", "capture_hash"}}
        changed["content_hash"] = evidence._sha256(content)
        changed["capture_hash"] = evidence._sha256({**content, "captured_at": changed["captured_at"]})
        changed_snapshot = copy.deepcopy(snapshot)
        changed_snapshot["data"]["trade_horizon_calendar"] = changed
        changed_receipt = sandlot_receipts.build_trade_assessment_receipt(
            snapshot=changed_snapshot, result=grade_result(), generated_at=datetime(2026, 7, 12, 20, 2, tzinfo=timezone.utc),
        )
        self.assertNotEqual(receipt["receipt_id"], changed_receipt["receipt_id"])

    def test_cutoff_before_equal_and_after_first_event_selects_safely(self):
        before = outcome_contract(cutoff="2026-07-13T17:04:59Z")
        equal = outcome_contract(cutoff="2026-07-13T17:05:00Z")
        after = outcome_contract(cutoff="2026-07-13T18:00:00Z")

        self.assertEqual(before["target_period"]["period_number"], "17")
        self.assertEqual(equal["target_period"]["period_number"], "18")
        self.assertEqual(after["target_period"]["period_number"], "18")

    def test_missing_first_future_deadline_makes_contract_ineligible_without_exception(self):
        payload = {"dates": [{"games": [{
            "gamePk": 3, "gameDate": "2026-07-27T23:05:00Z", "status": {"detailedState": "Scheduled"},
        }]}]}
        calendar = evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=payload, captured_at=CAPTURED,
        )
        contract = outcome_contract(calendar=calendar)
        self.assertFalse(contract["eligible"])
        self.assertIn({"code": "next_period_first_scoring_event_missing"}, contract["blocking_reasons"])

    def test_observation_after_snapshot_is_rejected_as_ineligible(self):
        late = evidence.build_period_calendar(
            league_id=LEAGUE, periods=periods(), schedule_payload=schedule(), captured_at="2026-07-12T20:02:00Z",
        )
        contract = evidence.build_trade_outcome_contract(
            league_id=LEAGUE, team_id="team", snapshot_id=281,
            snapshot_taken_at="2026-07-12T20:01:00Z", generated_at="2026-07-12T20:03:00Z",
            give_ids=["give"], get_ids=["get"], origin={"kind": "manual_entry"},
            calendar=late, identity_index=identity_index(),
        )
        self.assertFalse(contract["eligible"])
        self.assertIn({"code": "period_calendar_invalid"}, contract["blocking_reasons"])

    def test_mlb_identity_season_mismatch_is_downgraded_without_corrupting_fantrax_label(self):
        index = identity_index()
        index["season"] = 2025
        content = {key: value for key, value in index.items() if key not in {"observed_at", "content_hash", "capture_hash"}}
        index["content_hash"] = evidence._sha256(content)
        index["capture_hash"] = evidence._sha256({**content, "observed_at": index["observed_at"]})
        contract = outcome_contract(identities=index)

        self.assertTrue(contract["eligible"])
        self.assertEqual(contract["limitations"], [{"code": "mlb_identity_season_mismatch"}])
        self.assertFalse(contract["identity_index"]["calendar_season_matches"])
        self.assertTrue(all(asset["mlb_identity"]["mlb_id"] is None for asset in contract["assets"]))
        self.assertTrue(all(asset["mlb_identity"]["status"] == "season_mismatch" for asset in contract["assets"]))

    def test_no_remaining_period_is_explicitly_ineligible(self):
        contract = outcome_contract(cutoff="2026-08-03T04:00:00Z")
        self.assertFalse(contract["eligible"])
        self.assertIn({"code": "no_complete_regular_season_period"}, contract["blocking_reasons"])
        self.assertFalse(contract["execution_claimed"])
        self.assertFalse(contract["dynasty_claimed"])
        self.assertFalse(contract["autopilot_eligible"])

    def test_manual_offer_cluster_is_stable_within_declared_week(self):
        first = outcome_contract(cutoff="2026-07-12T20:02:00Z")
        second = outcome_contract(cutoff="2026-07-12T21:02:00Z")
        self.assertEqual(first["offer_cluster_key"], second["offer_cluster_key"])


class MlbIdentityResolutionTests(unittest.TestCase):
    def test_resolver_does_not_choose_ambiguous_name_without_unique_team(self):
        people = [
            {"id": 1, "fullName": "Same Name", "currentTeam": {"abbreviation": "NYY"}},
            {"id": 2, "fullName": "Same Name", "currentTeam": {"abbreviation": "SEA"}},
        ]
        with patch.object(mlb_stats, "_get_active_players", return_value=people):
            ambiguous = mlb_stats.resolve_player_identity("Same Name")
            resolved = mlb_stats.resolve_player_identity("Same Name", "SEA")

        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertIsNone(ambiguous["mlb_id"])
        self.assertEqual(resolved["status"], "resolved_name_team")
        self.assertEqual(resolved["mlb_id"], 2)


if __name__ == "__main__":
    unittest.main()
