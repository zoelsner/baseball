import copy
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import fantrax_data
import sandlot_db
import sandlot_receipts
import sandlot_refresh
import sandlot_trade_evidence
import sandlot_trade_outcomes as outcomes


LEAGUE = "lydahdo6mhcvnob7"
MATURE = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 7, 28, 4, 0, 1, tzinfo=timezone.utc)
LATE = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def real_built_receipt():
    give = {"id": "give", "name": "Give Player", "slot": "OF", "slot_source": "raw.posId", "positions": "OF", "team": "NYY", "fppg": 4.0, "age": 30, "age_source": "fantrax"}
    get = {"id": "get", "name": "Get Player", "slot": "SP", "slot_source": "raw.posId", "positions": "SP", "team": "SEA", "fppg": 5.5, "age": 28, "age_source": "fantrax"}
    player_snapshot = {
        "team_id": "team", "roster": {"rows": [give]},
        "all_team_rosters": {
            "team": {"is_me": True, "rows": [give]},
            "other": {"is_me": False, "rows": [get]},
        },
    }
    calendar = sandlot_trade_evidence.build_period_calendar(
        league_id=LEAGUE,
        periods=[
            {"period_number": "17", "start": "2026-07-13", "end": "2026-07-26", "regular_season": True},
            {"period_number": "18", "start": "2026-07-27", "end": "2026-08-02", "regular_season": True},
        ],
        schedule_payload={"dates": [
            {"games": [{"gamePk": 1, "gameDate": "2026-07-13T23:10:00Z", "status": {"detailedState": "Scheduled"}}]},
            {"games": [{"gamePk": 2, "gameDate": "2026-07-27T23:10:00Z", "status": {"detailedState": "Scheduled"}}]},
        ]},
        captured_at="2026-07-12T20:00:00Z",
    )
    identities = sandlot_trade_evidence.build_player_identity_index(
        snapshot=player_snapshot,
        observed_at="2026-07-12T20:00:00Z",
        resolver=lambda _name, _team, _season: {"status": "not_found", "mlb_id": None, "source": "test"},
        season=2026,
    )
    snapshot = {
        "id": 281, "taken_at": "2026-07-12T20:01:00Z",
        "league_id": LEAGUE, "team_id": "team",
        "data": {**player_snapshot, "trade_horizon_calendar": calendar, "trade_player_identities": identities},
    }
    result = {
        "snapshot_id": 281,
        "letter_grade": "B+", "fairness": 0.8, "my_delta": 1.5, "their_delta": -1.5,
        "age_delta": -2.0, "my_give_fppg": 4.0, "my_get_fppg": 5.5,
        "value_basis": "current_snapshot_fppg", "grade_scope": "current_rate_only",
        "dynasty_complete": False,
        "my_give": [give], "my_get": [get],
        "analysis": {"horizons": [{"key": "current_rate", "status": "modeled", "value": 1.5}]},
        "eligibility_evidence": {
            "policy_version": "trade_eligibility_v2", "all_checks_passed": True,
            "participants": [
                {"side": "give", "player_id": "give", "slot": "OF", "age": 30, "age_source": "fantrax", "protected_trade_player": False, "available_for_current_rate_grade": True, "requires_manual_dynasty_review": False, "fppg_valid": True},
                {"side": "get", "player_id": "get", "slot": "SP", "age": 28, "age_source": "fantrax", "protected_trade_player": False, "available_for_current_rate_grade": True, "requires_manual_dynasty_review": False, "fppg_valid": True},
            ],
        },
    }
    return sandlot_receipts.build_trade_assessment_receipt(
        snapshot=snapshot, result=result,
        generated_at=datetime(2026, 7, 12, 20, 2, tzinfo=timezone.utc),
    )


def receipt(*, give=None, get=None):
    give = give or [("give", "Give Player", "hitting")]
    get = get or [("get", "Get Player", "pitching")]
    assets = []
    for side, rows in (("give", give), ("get", get)):
        for player_id, _name, role in rows:
            roles = role if isinstance(role, list) else [role]
            assets.append({
                "side": side,
                "fantrax_id": player_id,
                "scoring_role": {
                    "status": "resolved",
                    "version": sandlot_trade_evidence.ROLE_POLICY_VERSION,
                    "scoring_entities": [
                        {"fantrax_scorer_id": player_id, "scoring_role": item}
                        for item in roles
                    ],
                },
            })
    return {
        "receipt_id": f"trade-assessment:{'a' * 64}",
        "builder_version": "trade_assessment_v4",
        "action_type": "trade_assessment",
        "league_id": LEAGUE,
        "team_id": "team",
        "input_hash": "a" * 64,
        "decision_state": "pending",
        "recommendation": {
            "offer": {
                "give": [{"player_id": pid, "player_name": name} for pid, name, _role in give],
                "get": [{"player_id": pid, "player_name": name} for pid, name, _role in get],
            },
            "outcome_contract": {
                "version": sandlot_trade_evidence.OUTCOME_CONTRACT_VERSION,
                "eligible": True,
                "target_metric": "static_package_asset_points_delta",
                "metric_unit": "league_fantasy_points",
                "target_period": {
                    "season": 2026,
                    "period_number": "17",
                    "start": "2026-07-13",
                    "end": "2026-07-26",
                    "period_close_at": "2026-07-27T04:00:00+00:00",
                    "maturity_at": MATURE.isoformat(),
                },
                "scoring_basis": {
                    "status": "verified",
                    "fantrax_points_source_version": sandlot_trade_evidence.SCORING_SOURCE_VERSION,
                    "rules_hash": "b" * 64,
                },
                "offer_cluster_key": "trade-opportunity:" + "c" * 64,
                "assets": assets,
                "causal_lift_claimed": False,
                "execution_claimed": False,
                "lineup_lift_claimed": False,
                "ros_claimed": False,
                "dynasty_claimed": False,
                "autopilot_eligible": False,
            },
        },
    }


def source_query(requirement):
    content = {
        "version": outcomes.TRADE_PLAYER_PERIOD_QUERY_VERSION,
        "method": "getPlayerStats",
        "request_identity": {
            "league_id": requirement["league_id"], "period": requirement["period_number"],
            "period_start": requirement["period_start"], "period_end": requirement["period_end"],
            "fantrax_scorer_id": requirement["fantrax_scorer_id"],
            "scoring_role": requirement["scoring_role"],
            "season_or_projection": "SEASON_147_BY_PERIOD",
            "timeframe_type_code": "BY_PERIOD", "time_start_type": "PERIOD_ONLY",
            "population": "ALL", "role_filter": requirement["role_filter"],
            "search_name": requirement["player_name"], "page": 1, "page_size": 50,
        },
    }
    return {**content, "query_hash": outcomes._sha256(content)}


def source_response(requirement, points):
    points = outcomes._decimal_text(outcomes._decimal(points, "points"))
    period_fpts_source = "SCORE:fpts:cells[3].content"
    source_slice = {
        "fantrax_scorer_id": requirement["fantrax_scorer_id"],
        "player_name": requirement["player_name"],
        "scoring_role": requirement["scoring_role"],
        "period_fpts": points,
        "period_fpts_source": period_fpts_source,
    }
    content = {
        "displayed_period": requirement["period_number"],
        "displayed_period_start": requirement["period_start"],
        "displayed_period_end": requirement["period_end"],
        "displayed_season_or_projection": "SEASON_147_BY_PERIOD",
        "displayed_timeframe_type_code": "BY_PERIOD", "displayed_time_start_type": "PERIOD_ONLY",
        "displayed_population": "ALL", "displayed_role_filter": requirement["role_filter"],
        "page_number": 1, "total_pages": 1, "total_results": 1,
        "matched_scorer_id": requirement["fantrax_scorer_id"],
        "matched_scorer_name": requirement["player_name"],
        "matched_scoring_role": requirement["scoring_role"],
        "matched_period_fpts": points,
        "period_fpts_source": period_fpts_source,
        "matched_source_slice": source_slice,
        "matched_source_slice_hash": outcomes._sha256(source_slice),
        "header_hash": "d" * 64,
        "raw_matched_row_hash": "e" * 64,
        "raw_response_hash": "f" * 64,
    }
    return {**content, "response_hash": outcomes._sha256(content)}


def missing_source_response(requirement, returned_ids=None):
    returned_ids = returned_ids or ["different"]
    content = {
        "displayed_period": requirement["period_number"],
        "displayed_period_start": requirement["period_start"],
        "displayed_period_end": requirement["period_end"],
        "displayed_season_or_projection": "SEASON_147_BY_PERIOD",
        "displayed_timeframe_type_code": "BY_PERIOD", "displayed_time_start_type": "PERIOD_ONLY",
        "displayed_population": "ALL", "displayed_role_filter": requirement["role_filter"],
        "page_number": 1, "total_pages": 1, "total_results": len(returned_ids),
        "exact_scorer_present": False, "returned_scorer_ids": returned_ids,
        "header_hash": "d" * 64, "raw_response_hash": "e" * 64,
    }
    return {**content, "response_hash": outcomes._sha256(content)}


def archived(requirement, points):
    return outcomes.build_player_period_evidence(
        requirement=requirement,
        period_fpts=points,
        source_query=source_query(requirement),
        source_response=source_response(requirement, points),
        observed_at=AFTER,
    )


class TradeRequirementTests(unittest.TestCase):
    def test_real_receipt_builder_contract_enters_scorer(self):
        built = real_built_receipt()

        requirements = outcomes.receipt_requirements(built, as_of=AFTER)

        self.assertEqual(
            [(item["fantrax_scorer_id"], item["scoring_role"]) for item in requirements],
            [("get", "pitching"), ("give", "hitting")],
        )
        self.assertTrue(all(item["offer_cluster_key"].startswith("trade-opportunity:") for item in requirements))

    def test_maturity_boundary_and_exact_role_requirements(self):
        self.assertEqual(outcomes.receipt_requirements(receipt(), as_of=MATURE.replace(second=0)), [
            *outcomes.receipt_requirements(receipt(), as_of=AFTER)
        ])
        self.assertEqual(outcomes.receipt_requirements(receipt(), as_of="2026-07-28T03:59:59Z"), [])

        requirements = outcomes.receipt_requirements(receipt(), as_of=MATURE)
        self.assertEqual(
            [(item["side"], item["fantrax_scorer_id"], item["scoring_role"], item["role_filter"])
             for item in requirements],
            [
                ("get", "get", "pitching", "BASEBALL_PITCHING"),
                ("give", "give", "hitting", "BASEBALL_HITTING"),
            ],
        )

    def test_ineligible_legacy_and_unsupported_claims_do_not_leak_into_scoring(self):
        legacy = receipt()
        legacy["builder_version"] = "trade_assessment_v3"
        self.assertEqual(outcomes.receipt_requirements(legacy, as_of=AFTER), [])
        ineligible = receipt()
        ineligible["recommendation"]["outcome_contract"]["eligible"] = False
        self.assertEqual(outcomes.receipt_requirements(ineligible, as_of=AFTER), [])
        overclaim = receipt()
        overclaim["recommendation"]["outcome_contract"]["execution_claimed"] = True
        with self.assertRaisesRegex(ValueError, "unsupported claim"):
            outcomes.receipt_requirements(overclaim, as_of=AFTER)

        additive_two_way = receipt(get=[("two-way", "Two Way", ["hitting", "pitching"])])
        with self.assertRaisesRegex(ValueError, "exactly one supported scoring entity"):
            outcomes.receipt_requirements(additive_two_way, as_of=AFTER)

    def test_shared_requirements_dedupe_without_owner_intent_selection(self):
        accepted = receipt()
        accepted["decision_state"] = "accepted"
        rejected = copy.deepcopy(accepted)
        rejected["receipt_id"] = f"trade-assessment:{'f' * 64}"
        rejected["decision_state"] = "rejected"

        requirements = outcomes.dedupe_requirements([rejected, accepted], as_of=AFTER)

        self.assertEqual(len(requirements), 2)
        self.assertNotIn("decision_state", str(requirements))


class TradePlayerPeriodEvidenceTests(unittest.TestCase):
    def test_explicit_zero_is_distinct_and_hash_is_material(self):
        requirement = outcomes.receipt_requirements(receipt(), as_of=AFTER)[0]
        zero = archived(requirement, "0")
        positive = archived(requirement, "8.25")

        self.assertEqual(zero["source_status"], "explicit_zero")
        self.assertEqual(zero["league_fantasy_points"], "0")
        self.assertEqual(positive["source_status"], "observed")
        self.assertNotEqual(zero["evidence_hash"], positive["evidence_hash"])
        self.assertEqual(zero["evidence_hash"], outcomes.player_period_evidence_hash(zero))
        with self.assertRaisesRegex(ValueError, "before maturity"):
            outcomes.build_player_period_evidence(
                requirement=requirement, period_fpts=0,
                source_query=source_query(requirement),
                source_response=source_response(requirement, 0),
                observed_at="2026-07-28T03:59:59Z",
            )

    def test_static_package_scores_every_entity_with_exact_decimal_arithmetic(self):
        trade = receipt(
            give=[("g1", "Give One", "hitting"), ("g2", "Give Two", "pitching")],
            get=[("get", "Get One", "hitting")],
        )
        requirements = outcomes.receipt_requirements(trade, as_of=AFTER)
        points = {("g1", "hitting"): "4.25", ("g2", "pitching"): "-1.5", ("get", "hitting"): "11.75"}
        evidence = [archived(item, points[(item["fantrax_scorer_id"], item["scoring_role"])]) for item in requirements]

        result = outcomes.build_static_package_evaluation(
            receipt=trade, player_period_evidence=list(reversed(evidence)), as_of=AFTER,
        )

        self.assertEqual(result["metrics"], {
            "give_package_points": 2.75,
            "get_package_points": 11.75,
            "static_package_asset_points_delta": 9.0,
            "give_asset_count": 2,
            "get_asset_count": 1,
            "give_entity_count": 2,
            "get_entity_count": 1,
        })
        scored = result["evidence"]
        self.assertEqual(scored["package_shape"], "give_2_get_1")
        self.assertEqual(len(scored["contributions"]), 3)
        self.assertEqual(scored["execution_state"], "unknown")
        for key in ("causal_lift_claimed", "execution_claimed", "lineup_lift_claimed", "ros_claimed", "dynasty_claimed", "autopilot_eligible"):
            self.assertFalse(scored[key])
        self.assertNotIn("owner_intent_state", scored)

    def test_source_lineage_tampering_cannot_be_rehashed_into_valid_evidence(self):
        requirement = outcomes.receipt_requirements(receipt(), as_of=AFTER)[0]
        original = archived(requirement, "8.25")

        tampered_points = copy.deepcopy(original)
        tampered_points["league_fantasy_points"] = "99"
        tampered_points["evidence_hash"] = outcomes.player_period_evidence_hash(tampered_points)
        with self.assertRaisesRegex(ValueError, "source response contradicts"):
            outcomes.validate_player_period_evidence(tampered_points)

        tampered_query = copy.deepcopy(original)
        tampered_query["source_query"]["request_identity"]["period"] = "18"
        query_content = {key: value for key, value in tampered_query["source_query"].items() if key != "query_hash"}
        tampered_query["source_query"]["query_hash"] = outcomes._sha256(query_content)
        tampered_query["evidence_hash"] = outcomes.player_period_evidence_hash(tampered_query)
        with self.assertRaisesRegex(ValueError, "request identity contradicts"):
            outcomes.validate_player_period_evidence(tampered_query)

        tampered_response = copy.deepcopy(original)
        tampered_response["source_response"]["matched_period_fpts"] = "99"
        response_content = {key: value for key, value in tampered_response["source_response"].items() if key != "response_hash"}
        tampered_response["source_response"]["response_hash"] = outcomes._sha256(response_content)
        tampered_response["evidence_hash"] = outcomes.player_period_evidence_hash(tampered_response)
        with self.assertRaisesRegex(ValueError, "source response contradicts"):
            outcomes.validate_player_period_evidence(tampered_response)

    def test_missing_extra_duplicate_and_wrong_role_evidence_fail_closed(self):
        trade = receipt()
        requirements = outcomes.receipt_requirements(trade, as_of=AFTER)
        rows = [archived(item, 1) for item in requirements]
        cases = (
            rows[:-1],
            [*rows, rows[0]],
        )
        for value in cases:
            with self.subTest(count=len(value)):
                with self.assertRaises(ValueError):
                    outcomes.build_static_package_evaluation(
                        receipt=trade, player_period_evidence=value, as_of=AFTER,
                    )
        wrong = copy.deepcopy(rows)
        wrong[0]["entity"]["scoring_role"] = "hitting"
        wrong[0]["evidence_hash"] = outcomes.player_period_evidence_hash(wrong[0])
        with self.assertRaises(ValueError):
            outcomes.build_static_package_evaluation(
                receipt=trade, player_period_evidence=wrong, as_of=AFTER,
            )


def player_stats_response(*, player_id="041ma", name="Cole Ragans", role_filter="BASEBALL_PITCHING", points="0", period=17, total_pages=1):
    return {
        "displayedPeriod": period,
        "displayedPosOrGroup": role_filter,
        "displayedStatusOrTeam": "ALL",
        "displayedTimeStartType": "PERIOD_ONLY",
        "displayedSeasonOrProjection": {"code": "SEASON_147_BY_PERIOD", "timeframeTypeCode": "BY_PERIOD"},
        "periodList": ["17 (Jul 13 - Jul 26)"],
        "paginatedResultSet": {"pageNumber": 1, "totalNumPages": total_pages, "totalNumResults": 1, "maxResultsPerPage": 50},
        "tableHeader": {"cells": [
            {"key": "rankOv"}, {"key": "status"}, {"key": "age"},
            {"sortKey": "SCORE", "key": "fpts", "shortName": "FPts"},
        ]},
        "statsTable": [{
            "scorer": {"scorerId": player_id, "name": name, "posShortNames": "SP"},
            "cells": [{"content": "1"}, {"content": ""}, {"content": "29"}, {"content": points}],
        }],
    }


class TradePlayerPeriodCollectorTests(unittest.TestCase):
    class Api:
        league_id = LEAGUE

    def requirement(self, role="pitching"):
        trade = receipt(get=[("041ma", "Cole Ragans", role)])
        return next(item for item in outcomes.receipt_requirements(trade, as_of=AFTER) if item["side"] == "get")

    def test_collector_uses_transaction_period_and_proves_explicit_zero(self):
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=player_stats_response()) as request:
            evidence = fantrax_data.extract_trade_player_period_evidence(
                self.Api(), self.requirement(), by_period_code="SEASON_147_BY_PERIOD", observed_at=AFTER,
            )

        self.assertEqual(evidence["source_status"], "explicit_zero")
        self.assertEqual(evidence["entity"]["fantrax_scorer_id"], "041ma")
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["transactionPeriod"], 17)
        self.assertNotIn("period", kwargs)
        self.assertEqual(kwargs["positionOrGroup"], "BASEBALL_PITCHING")
        self.assertEqual(kwargs["statusOrTeamFilter"], "ALL")

    def test_collector_fails_closed_on_wrong_period_or_incomplete_page(self):
        for response, message in (
            (player_stats_response(period=16), "identity"),
            (player_stats_response(total_pages=2), "incomplete"),
        ):
            with self.subTest(message=message), patch.object(fantrax_data, "_direct_fxpa_request", return_value=response):
                with self.assertRaisesRegex(ValueError, message):
                    fantrax_data.extract_trade_player_period_evidence(
                        self.Api(), self.requirement(), by_period_code="SEASON_147_BY_PERIOD", observed_at=AFTER,
                    )

    def test_exact_absence_is_pending_not_zero(self):
        response = player_stats_response(player_id="different", name="Different Player", points="0")
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=response):
            evidence = fantrax_data.extract_trade_player_period_evidence(
                self.Api(), self.requirement(), by_period_code="SEASON_147_BY_PERIOD", observed_at=AFTER,
            )
        self.assertEqual(evidence["observation_type"], "exact_scorer_absent")
        self.assertTrue(evidence["retryable"])
        self.assertNotIn("league_fantasy_points", evidence)
        outcomes.validate_missing_player_period_observation(evidence)

        tampered = copy.deepcopy(evidence)
        tampered["entity"]["player_name"] = "Unrelated Player"
        tampered["observed_at"] = LATE.isoformat()
        tampered["source_query"]["request_identity"]["search_name"] = "Unrelated Player"
        tampered["source_query"]["query_hash"] = outcomes._sha256({
            key: value for key, value in tampered["source_query"].items() if key != "query_hash"
        })
        tampered["observation_hash"] = outcomes.missing_observation_hash(tampered)
        with self.assertRaisesRegex(ValueError, "frozen player"):
            outcomes.build_static_package_unavailable(
                receipt=receipt(get=[("041ma", "Cole Ragans", "pitching")]),
                missing_observations=[tampered],
                snapshot={
                    "timestamp": LATE.isoformat(), "league_id": LEAGUE, "team_id": "team",
                    "matchup": {"latest_completed": {
                        "source": "fantrax_schedule", "score_state": "live_or_final", "complete": True,
                        "period_number": "18", "end": "2026-08-02", "matchup_key": "18:team:other",
                    }},
                }, snapshot_id=7, as_of=LATE,
            )

    def test_exact_absence_terminalizes_only_after_grace_and_newer_final_period(self):
        response = player_stats_response(player_id="different", name="Different Player", points="0")
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=response):
            observation = fantrax_data.extract_trade_player_period_evidence(
                self.Api(), self.requirement(), by_period_code="SEASON_147_BY_PERIOD", observed_at=LATE,
            )
        trade = receipt(get=[("041ma", "Cole Ragans", "pitching")])
        snapshot = {
            "timestamp": LATE.isoformat(), "league_id": LEAGUE, "team_id": "team",
            "matchup": {"latest_completed": {
                "source": "fantrax_schedule", "score_state": "live_or_final", "complete": True,
                "period_number": "18", "end": "2026-08-02", "matchup_key": "18:team:other",
            }},
        }

        with self.assertRaisesRegex(ValueError, "grace"):
            outcomes.build_static_package_unavailable(
                receipt=trade, missing_observations=[observation], snapshot=snapshot,
                snapshot_id=7, as_of="2026-08-04T00:00:00Z",
            )
        unavailable = outcomes.build_static_package_unavailable(
            receipt=trade, missing_observations=[observation], snapshot=snapshot,
            snapshot_id=7, as_of=LATE,
        )

        self.assertEqual(unavailable["state"], "unavailable")
        self.assertEqual(unavailable["metrics"], {})
        self.assertEqual(unavailable["evidence"]["reason"], "scoring_entity_missing_after_grace")
        self.assertFalse(unavailable["evidence"]["retryable"])
        outcomes.validate_static_package_unavailable(receipt=trade, evaluation=unavailable)

    def test_role_filter_separates_two_way_player_rows(self):
        hitter = self.requirement(role="hitting")
        pitcher = self.requirement(role="pitching")
        with patch.object(fantrax_data, "_direct_fxpa_request", side_effect=[
            player_stats_response(role_filter="BASEBALL_HITTING", points="13.5"),
            player_stats_response(role_filter="BASEBALL_PITCHING", points="28.5"),
        ]):
            hit = fantrax_data.extract_trade_player_period_evidence(
                self.Api(), hitter, by_period_code="SEASON_147_BY_PERIOD", observed_at=AFTER,
            )
            pitch = fantrax_data.extract_trade_player_period_evidence(
                self.Api(), pitcher, by_period_code="SEASON_147_BY_PERIOD", observed_at=AFTER,
            )
        self.assertEqual(hit["league_fantasy_points"], "13.5")
        self.assertEqual(pitch["league_fantasy_points"], "28.5")


class Result:
    def __init__(self, *, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


@contextmanager
def connection(conn):
    yield conn


class TradePlayerPeriodPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.trade = receipt()
        self.requirements = outcomes.receipt_requirements(self.trade, as_of=AFTER)
        self.rows = [archived(item, index + 1) for index, item in enumerate(self.requirements)]

    def test_archive_insert_and_identical_replay_are_idempotent(self):
        evidence = self.rows[0]

        class Conn:
            def __init__(self, replay=False):
                self.replay = replay

            def execute(self, sql, _params):
                if "INSERT INTO trade_player_period_evidence" in sql:
                    return Result(one=None if self.replay else {"evidence_hash": evidence["evidence_hash"], "evidence": evidence})
                if "SELECT * FROM trade_player_period_evidence" in sql:
                    return Result(one={"evidence_hash": evidence["evidence_hash"], "evidence": evidence})
                raise AssertionError(sql)

        with patch.object(sandlot_db, "connect", return_value=connection(Conn())):
            _row, created = sandlot_db.archive_trade_player_period_evidence(evidence=evidence, snapshot_id=7)
        self.assertTrue(created)
        with patch.object(sandlot_db, "connect", return_value=connection(Conn(replay=True))):
            _row, created = sandlot_db.archive_trade_player_period_evidence(evidence=evidence, snapshot_id=7)
        self.assertFalse(created)

    def test_archive_conflict_rejects_changed_immutable_points(self):
        original = self.rows[0]
        changed = archived(self.requirements[0], 99)

        class Conn:
            def execute(self, sql, _params):
                if "INSERT INTO trade_player_period_evidence" in sql:
                    return Result(one=None)
                if "SELECT * FROM trade_player_period_evidence" in sql:
                    return Result(one={"evidence_hash": original["evidence_hash"], "evidence": original})
                raise AssertionError(sql)

        with patch.object(sandlot_db, "connect", return_value=connection(Conn())):
            with self.assertRaisesRegex(ValueError, "different immutable evidence"):
                sandlot_db.archive_trade_player_period_evidence(evidence=changed, snapshot_id=7)

    def test_trade_recorder_reverifies_receipt_and_every_source_archive(self):
        evaluation = outcomes.build_static_package_evaluation(
            receipt=self.trade, player_period_evidence=self.rows, as_of=AFTER,
        )
        hashes = {item["hash"] for item in evaluation["evidence"]["source_evidence"]["rows"]}

        class Conn:
            def execute(self, sql, params):
                if "FROM recommendation_receipts WHERE receipt_id" in sql:
                    return Result(one=self_receipt)
                if "SELECT evidence_hash FROM trade_player_period_evidence" in sql:
                    requested = params[-1]
                    self_test.assertEqual(requested, outcomes.TRADE_PLAYER_PERIOD_EVIDENCE_VERSION)
                    return Result(one={"evidence_hash": next(iter(hashes - getattr(self, "seen", set())))})
                if "INSERT INTO recommendation_outcome_evaluations" in sql:
                    return Result(one={"receipt_id": self_receipt["receipt_id"], "evidence_hash": evaluation["evidence"]["evidence_hash"]})
                raise AssertionError(sql)

        self_receipt = self.trade
        self_test = self
        conn = Conn()
        # Return the source hash matching each query's exact scorer/role rather than query order.
        source_by_entity = {
            (row["fantrax_scorer_id"], row["scoring_role"]): (
                row["hash"],
                next(item for item in self.rows if item["evidence_hash"] == row["hash"]),
            )
            for row in evaluation["evidence"]["source_evidence"]["rows"]
        }

        def execute(sql, params):
            if "FROM recommendation_receipts WHERE receipt_id" in sql:
                return Result(one=self_receipt)
            if "FROM trade_player_period_evidence" in sql:
                evidence_hash, evidence = source_by_entity[(params[5], params[6])]
                return Result(one={"evidence_hash": evidence_hash, "evidence": evidence})
            if "INSERT INTO recommendation_outcome_evaluations" in sql:
                return Result(one={"receipt_id": self_receipt["receipt_id"], "evidence_hash": evaluation["evidence"]["evidence_hash"]})
            raise AssertionError(sql)

        conn.execute = execute
        with patch.object(sandlot_db, "connect", return_value=connection(conn)):
            row, created = sandlot_db.record_trade_static_package_evaluation(
                receipt_id=self.trade["receipt_id"], evaluation=evaluation,
            )
        self.assertTrue(created)
        self.assertEqual(row["receipt_id"], self.trade["receipt_id"])

    def test_trade_recorder_accepts_only_semantically_valid_terminal_unavailable(self):
        requirement = next(item for item in self.requirements if item["side"] == "get")
        response = player_stats_response(player_id="different", name="Different Player", points="0")
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=response):
            observation = fantrax_data.extract_trade_player_period_evidence(
                TradePlayerPeriodCollectorTests.Api(), requirement,
                by_period_code="SEASON_147_BY_PERIOD", observed_at=LATE,
            )
        snapshot = {
            "timestamp": LATE.isoformat(), "league_id": LEAGUE, "team_id": "team",
            "matchup": {"latest_completed": {
                "source": "fantrax_schedule", "score_state": "live_or_final", "complete": True,
                "period_number": "18", "end": "2026-08-02", "matchup_key": "18:team:other",
            }},
        }
        unavailable = outcomes.build_static_package_unavailable(
            receipt=self.trade, missing_observations=[observation], snapshot=snapshot,
            snapshot_id=7, as_of=LATE,
        )

        class Conn:
            def execute(_self, sql, _params):
                if "FROM recommendation_receipts WHERE receipt_id" in sql:
                    return Result(one=self.trade)
                if "FROM snapshots WHERE id" in sql:
                    return Result(one={
                        "id": 7, "taken_at": LATE, "league_id": LEAGUE,
                        "team_id": "team", "data": snapshot,
                    })
                if "INSERT INTO recommendation_outcome_evaluations" in sql:
                    return Result(one={"receipt_id": self.trade["receipt_id"], "state": "unavailable"})
                if "trade_player_period_evidence" in sql:
                    raise AssertionError("unavailable evaluation must not fabricate an archive row")
                raise AssertionError(sql)

        with patch.object(sandlot_db, "connect", return_value=connection(Conn())):
            row, created = sandlot_db.record_trade_static_package_evaluation(
                receipt_id=self.trade["receipt_id"], evaluation=unavailable,
            )
        self.assertTrue(created)
        self.assertEqual(row["state"], "unavailable")

        class MissingSnapshotConn(Conn):
            def execute(_self, sql, params):
                if "FROM snapshots WHERE id" in sql:
                    return Result(one=None)
                return super().execute(sql, params)

        with patch.object(sandlot_db, "connect", return_value=connection(MissingSnapshotConn())):
            with self.assertRaisesRegex(ValueError, "snapshot was not found"):
                sandlot_db.record_trade_static_package_evaluation(
                    receipt_id=self.trade["receipt_id"], evaluation=unavailable,
                )


class TradeRefreshLoopTests(unittest.TestCase):
    def test_refresh_collects_only_missing_entities_then_scores_complete_receipt(self):
        trade = receipt()
        trade["recommendation"]["outcome_contract"]["target_period"].update({
            "period_close_at": "2026-07-11T04:00:00+00:00",
            "maturity_at": "2026-07-12T04:00:00+00:00",
        })
        stored = []
        recorded = []

        def load(*, requirements):
            keys = {outcomes.requirement_key(item) for item in requirements}
            return [
                row for row in stored
                if outcomes.requirement_key({
                    "league_id": row["league_id"], "season": row["season"],
                    "period_number": row["period"]["number"], "period_start": row["period"]["start"],
                    "period_end": row["period"]["end"], "fantrax_scorer_id": row["entity"]["fantrax_scorer_id"],
                    "scoring_role": row["entity"]["scoring_role"], "evidence_version": row["evidence_version"],
                }) in keys
            ]

        def collect(_api, requirement, **_kwargs):
            return outcomes.build_player_period_evidence(
                requirement=requirement,
                period_fpts=4 if requirement["fantrax_scorer_id"] == "give" else 9,
                source_query=source_query(requirement),
                source_response=source_response(requirement, 4 if requirement["fantrax_scorer_id"] == "give" else 9),
                observed_at=AFTER,
            )

        def archive_row(*, evidence, snapshot_id):
            self.assertEqual(snapshot_id, 7)
            stored.append(evidence)
            return {"evidence": evidence}, True

        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}),
            patch.object(sandlot_db, "trade_receipts_missing_static_package_evaluation", return_value=[trade]),
            patch.object(sandlot_db, "get_trade_player_period_evidence", side_effect=load),
            patch.object(sandlot_db, "archive_trade_player_period_evidence", side_effect=archive_row),
            patch.object(sandlot_db, "record_trade_static_package_evaluation", side_effect=lambda **kwargs: recorded.append(kwargs)),
            patch.object(fantrax_data, "FantraxAPI", return_value=object()),
            patch.object(fantrax_data, "_raw_team_roster", return_value={"selector": True}),
            patch.object(fantrax_data, "_by_period_season_code", return_value="SEASON_147_BY_PERIOD"),
            patch.object(fantrax_data, "extract_trade_player_period_evidence", side_effect=collect) as fetch,
        ):
            sandlot_refresh._persist_trade_period_outcomes(
                7, {}, session=object(), league_id=LEAGUE, team_id="team",
            )

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(stored), 2)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["evaluation"]["metrics"]["static_package_asset_points_delta"], 5.0)

    def test_optional_trade_evidence_failure_never_breaks_healthy_refresh(self):
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}),
            patch.object(
                sandlot_db, "trade_receipts_missing_static_package_evaluation",
                side_effect=RuntimeError("database temporarily unavailable"),
            ),
        ):
            sandlot_refresh._persist_trade_period_outcomes(
                7, {}, session=object(), league_id=LEAGUE, team_id="team",
            )

    def test_refresh_terminalizes_post_grace_complete_absence_without_zero_metrics(self):
        trade = receipt()
        target = trade["recommendation"]["outcome_contract"]["target_period"]
        target.update({
            "period_number": "13", "start": "2026-06-15", "end": "2026-06-21",
            "period_close_at": "2026-06-22T04:00:00+00:00",
            "maturity_at": "2026-06-23T04:00:00+00:00",
        })
        recorded = []

        def collect(_api, requirement, **_kwargs):
            return outcomes.build_missing_player_period_observation(
                requirement=requirement,
                source_query=source_query(requirement),
                source_response=missing_source_response(requirement),
                observed_at="2026-07-12T20:00:01Z",
            )

        snapshot = {
            "timestamp": "2026-07-12T20:00:00Z", "league_id": LEAGUE, "team_id": "team",
            "matchup": {"latest_completed": {
                "source": "fantrax_schedule", "score_state": "live_or_final", "complete": True,
                "period_number": "15", "end": "2026-07-05", "matchup_key": "15:team:other",
            }},
        }
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}),
            patch.object(sandlot_db, "trade_receipts_missing_static_package_evaluation", return_value=[trade]),
            patch.object(sandlot_db, "get_trade_player_period_evidence", return_value=[]),
            patch.object(sandlot_db, "record_trade_static_package_evaluation", side_effect=lambda **kwargs: recorded.append(kwargs)),
            patch.object(fantrax_data, "FantraxAPI", return_value=object()),
            patch.object(fantrax_data, "_raw_team_roster", return_value={"selector": True}),
            patch.object(fantrax_data, "_by_period_season_code", return_value="SEASON_147_BY_PERIOD"),
            patch.object(fantrax_data, "extract_trade_player_period_evidence", side_effect=collect),
        ):
            sandlot_refresh._persist_trade_period_outcomes(
                7, snapshot, session=object(), league_id=LEAGUE, team_id="team",
            )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["evaluation"]["state"], "unavailable")
        self.assertEqual(recorded[0]["evaluation"]["metrics"], {})


if __name__ == "__main__":
    unittest.main()
