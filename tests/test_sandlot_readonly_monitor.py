import copy
import unittest
from datetime import datetime, timezone

from scripts import sandlot_readonly_monitor as monitor


NOW = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)


def healthy_lineup_contract(snapshot_id):
    slot_moves = [
        {"order": 1, "player_id": "mine-in", "from_slot": "RES", "to_slot": "OF"},
        {"order": 2, "player_id": "mine-out", "from_slot": "OF", "to_slot": "RES"},
    ]
    return {
        "version": 2,
        "proposal_id": "lineup:test",
        "snapshot_id": snapshot_id,
        "slot_moves": slot_moves,
        "input_hash": "a" * 64,
        "projected_benefit": {
            "points": 2.0,
            "win_probability_delta": None,
            "probability_calibrated": False,
        },
        "freshness_policy": {"requires_live_preflight": True},
        "post_write_verification": {"required": True},
        "confirmation": {
            "mode": "exact_contract_match",
            "expected": {
                "proposal_id": "lineup:test",
                "snapshot_id": snapshot_id,
                "slot_moves": slot_moves,
                "input_hash": "a" * 64,
            },
        },
    }


def healthy_payloads():
    snapshot_id = 264
    quality = {
        "my_roster": {"state": "ok"},
        "lineup_slots": {"state": "ok"},
        "add_drop_recommendations_ready": True,
    }
    win_plan = {
        "model_version": "win_this_week_v1",
        "state": "ready",
        "snapshot_id": snapshot_id,
        "read_only": True,
        "writes_enabled": False,
        "matchup": {"complete": False},
        "current_period": {
            "state": "ok",
            "editable_period": 16,
            "editable_start": "2026-07-06",
            "editable_end": "2026-07-12",
            "matchup_period": 16,
            "matchup_start": "2026-07-06",
            "matchup_end": "2026-07-12",
        },
        "handoffs": {
            "lineup": {
                "label": "Open Fantrax lineup",
                "url": "https://www.fantrax.com/fantasy/league/league/team/roster;teamId=me",
                "method": "GET",
                "read_only": True,
                "writes_enabled": False,
            },
        },
        "primary_action_id": "lineup:test",
        "actions": [{
            "id": "lineup:test",
            "rank": 1,
            "kind": "lineup",
            "state": "act_now",
            "steps": [],
            "expected_points": {"estimate": 2.0, "comparable": True},
            "win_probability_delta": None,
            "deadline": {"state": "known", "at": "2026-07-10T23:05:00+00:00"},
            "dynasty_cost": {"level": "none"},
            "legality": {"state": "snapshot_verified"},
            "writes_enabled": False,
        }],
        "summary": {
            "headline": "Best move adds 2.0 projected points.",
            "outlook": "After this move, the remaining-week estimate puts you 6.0 points ahead.",
            "projected_margin_before_action": 4.0,
            "projected_margin_after_action": 6.0,
        },
        "monitoring_actions": [],
        "diagnostics": {"probability_calibrated": False},
    }
    payloads = {
        "/api/health": {
            "ok": True,
            "database": "ok",
            "freshness": {"state": "fresh", "age_minutes": 30},
            "latest_refresh_run": {"status": "success"},
        },
        "/api/snapshot/latest": {
            "snapshot_id": snapshot_id,
            "taken_at": "2026-07-10T15:30:00Z",
            "freshness": {"state": "fresh", "age_minutes": 30},
            "roster": [
                {"id": "mine-out", "name": "Roster Player", "age": 29, "age_source": "raw.scorer.playerAge", "fppg": 2.0},
                {"id": "mine-in", "name": "Bench Player", "age": 27, "age_source": "raw.scorer.playerAge", "fppg": 4.0},
            ],
            "player_index": [
                {"id": "mine-out", "name": "Roster Player", "source": "mine", "age": 29, "age_source": "raw.scorer.playerAge", "fppg": 2.0},
                {"id": "mine-in", "name": "Bench Player", "source": "mine", "age": 27, "age_source": "raw.scorer.playerAge", "fppg": 4.0},
                {"id": "league-1", "name": "League Player", "source": "league", "age": 31, "age_source": "raw.scorer.playerAge", "fppg": 3.0},
            ],
            "matchup": {
                "projection": {
                    "model_version": "matchup_projection_v4",
                    "scoring_basis": "current_snapshot_fppg_x_remaining_games",
                    "probability_calibrated": False,
                    "projected_my": 100.0,
                    "projected_opp": 99.0,
                    "my_remaining_games": 10,
                    "opp_remaining_games": 10,
                    "win_probability": 0.55,
                    "complete": False,
                },
                "recommendations": {
                    "model_version": "matchup_projection_v4",
                    "base_projection": {"projected_my": 100.0, "probability_calibrated": False},
                    "thresholds": {
                        "points_delta": 1.0,
                        "win_probability_delta": None,
                        "probability_calibrated": False,
                    },
                    "recommendations": [{
                        "rank": 1,
                        "points_delta": 2.0,
                        "win_probability_delta": None,
                        "probability_calibrated": False,
                        "confidence_basis": "projected_points_magnitude",
                        "replacement_card": {
                            "confidence_basis": "projected_points_magnitude",
                            "move_in": {"id": "mine-in"},
                            "move_out": {"id": "mine-out"},
                            "projected_benefit": {
                                "new_projected_my": 102.0,
                                "win_probability_delta": None,
                                "base_win_probability": None,
                                "new_win_probability": None,
                                "probability_calibrated": False,
                            },
                            "movability": {
                                "participants": {
                                    "move_in": {"id": "mine-in"},
                                    "move_out": {"id": "mine-out"},
                                },
                            },
                            "proposal": {
                                "type": "lineup_swap",
                                "status": "blocked",
                                "writes_enabled": False,
                                "executable": False,
                                "contract": healthy_lineup_contract(snapshot_id),
                            },
                        },
                    }],
                },
            },
            "win_this_week": copy.deepcopy(win_plan),
            "errors": [],
            "data_quality": quality,
        },
        "/api/attention": {
            "snapshot_id": snapshot_id,
            "items": [{
                "kind": "replacement",
                "proposal": {
                    "writes_enabled": False,
                    "executable": False,
                    "status": "blocked",
                    "contract": {"snapshot_id": snapshot_id},
                },
            }],
            "changes": [],
        },
        "/api/hot-swaps/latest": {
            "snapshot_id": snapshot_id,
            "state": "ready",
            "writes_enabled": False,
            "proposals": [{
                "proposal": {
                    "writes_enabled": False,
                    "executable": False,
                    "status": "blocked",
                    "contract": {"snapshot_id": snapshot_id},
                },
            }],
        },
        "/api/waiver-swaps/latest": {
            "snapshot_id": snapshot_id,
            "cards": [{
                "net_delta": 1.2,
                "confidence": "Medium",
                "add": {"age": 28, "age_source": "stats.Age", "score_source": "FP/G"},
                "move_out": {"age": 31, "age_source": "raw.scorer.playerAge"},
            }],
            "data_quality": quality,
        },
        "/api/win-this-week/latest": copy.deepcopy(win_plan),
    }
    return payloads


class ReadOnlyMonitorTests(unittest.TestCase):
    def test_healthy_contract_passes_without_exposing_player_payloads(self):
        report = monitor.evaluate_payloads(healthy_payloads(), checked_at=NOW)

        self.assertTrue(report["ok"])
        self.assertEqual(report["failures"], [])
        rendered = monitor.render_markdown(report)
        self.assertIn("Sandlot read-only monitor: PASS", rendered)
        self.assertNotIn("Roster Player", rendered)

    def test_old_snapshot_fails(self):
        payloads = healthy_payloads()
        payloads["/api/snapshot/latest"]["freshness"] = {"state": "old", "age_minutes": 3000}

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("snapshot_too_old", codes)
        self.assertIn("snapshot_not_fresh_enough", codes)

    def test_win_this_week_cannot_enable_writes_or_move_out_aaron_judge(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan["writes_enabled"] = True
            plan["actions"][0]["steps"] = [{
                "action": "move_out",
                "player_id": "judge",
                "player_name": "Aaron Judge",
            }]

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_write_boundary", codes)
        self.assertIn("win_this_week_protected_anchor", codes)
        self.assertIn("win_this_week_embedded_write_boundary", codes)
        self.assertIn("win_this_week_embedded_protected_anchor", codes)

    def test_win_this_week_lower_bound_requires_visible_caveat(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan["matchup"] = {
                "opportunity_completeness": "known_opportunities_lower_bound",
                "pitchers_without_probable_start": 3,
                "probability_calibrated": False,
            }
            plan["summary"] = {"projection_caveat": None}

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_opportunity_scope", codes)
        self.assertIn("win_this_week_embedded_opportunity_scope", codes)

    def test_win_this_week_rejects_expired_action_deadlines(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan["actions"][0]["deadline"]["at"] = "2026-07-10T15:59:59Z"

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_deadline_expired", codes)
        self.assertIn("win_this_week_embedded_deadline_expired", codes)

    def test_win_this_week_requires_cross_endpoint_action_parity(self):
        payloads = healthy_payloads()
        payloads["/api/win-this-week/latest"]["actions"][0]["expected_points"]["estimate"] = 3.0

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_cross_endpoint_drift", codes)

    def test_win_this_week_rejects_incorrect_post_action_outlook_math(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan["summary"]["projected_margin_after_action"] = 9.0

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_outlook_math", codes)
        self.assertIn("win_this_week_embedded_outlook_math", codes)

    def test_win_this_week_lineup_handoff_must_remain_read_only_fantrax_get(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan["handoffs"]["lineup"].update({"method": "POST", "writes_enabled": True})

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_lineup_handoff", codes)
        self.assertIn("win_this_week_embedded_lineup_handoff", codes)

    def test_win_this_week_cannot_act_when_editable_period_is_mismatched(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan["current_period"] = {
                "state": "mismatch",
                "editable_period": 17,
                "editable_start": "2026-07-13",
                "editable_end": "2026-07-26",
                "matchup_period": 16,
                "matchup_start": "2026-07-06",
                "matchup_end": "2026-07-12",
            }

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_period_alignment", codes)
        self.assertIn("win_this_week_embedded_period_alignment", codes)

    def test_win_this_week_missing_period_requires_pause_and_refresh_monitor(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan.update({
                "state": "no_action",
                "primary_action_id": None,
                "current_period": {"state": "missing"},
                "actions": [],
                "handoffs": {},
                "monitoring_actions": [],
                "no_action": {
                    "reason": "Fantrax's editable roster period cannot be matched.",
                    "alternatives": [],
                },
            })
        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_period_alignment", codes)
        self.assertIn("win_this_week_embedded_period_alignment", codes)

    def test_completed_win_this_week_does_not_require_period_refresh(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan.update({
                "state": "complete",
                "primary_action_id": None,
                "current_period": {"state": "missing"},
                "actions": [],
                "handoffs": {},
                "monitoring_actions": [],
                "no_action": {
                    "reason": "The matchup is complete.",
                    "alternatives": [],
                },
            })
            plan["matchup"]["complete"] = True

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        period_failures = {
            item["code"] for item in report["failures"]
            if item["code"].endswith("period_alignment")
        }
        self.assertEqual(period_failures, set())

    def test_complete_state_cannot_bypass_missing_period_gate(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan.update({
                "state": "complete",
                "primary_action_id": None,
                "current_period": {"state": "missing"},
                "actions": [],
                "handoffs": {},
                "monitoring_actions": [],
                "no_action": {
                    "reason": "The matchup is complete.",
                    "alternatives": [],
                },
            })
            plan["matchup"]["complete"] = False

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_matchup_state", codes)
        self.assertIn("win_this_week_period_alignment", codes)

    def test_future_period_plan_requires_lineup_only_period_bound_actions(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan["planning_horizon"] = {"mode": "editable_period", "period_number": 17}
            plan["actions"][0]["kind"] = "waiver"
            plan["actions"][0]["target_period"] = {"period_number": 16}
            plan["handoffs"]["lineup"]["target_period"] = {"period_number": 16}
            plan["monitoring_actions"] = []

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_planning_horizon", codes)
        self.assertIn("win_this_week_embedded_planning_horizon", codes)

    def test_win_this_week_no_action_requires_reasoned_alternatives(self):
        payloads = healthy_payloads()
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan.update({
                "state": "no_action",
                "primary_action_id": None,
                "actions": [],
                "no_action": {"reason": "No legal move clears the value threshold."},
            })

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_no_action_alternatives", codes)
        self.assertIn("win_this_week_embedded_no_action_alternatives", codes)

    def test_win_this_week_no_action_cannot_expose_aaron_judge_move_out(self):
        payloads = healthy_payloads()
        alternative = {
            "id": "rejected-waiver:judge",
            "kind": "waiver",
            "title": "Rejected protected move",
            "status": "rejected",
            "reason": "This move is protected.",
            "expected_points": {"estimate": None, "comparable": False},
            "steps": [{"action": "move_out", "player_name": "Aaron Judge"}],
        }
        for plan in (
            payloads["/api/snapshot/latest"]["win_this_week"],
            payloads["/api/win-this-week/latest"],
        ):
            plan.update({
                "state": "no_action",
                "primary_action_id": None,
                "actions": [],
                "no_action": {
                    "reason": "No legal move clears the value threshold.",
                    "alternatives": [copy.deepcopy(alternative)],
                },
            })

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("win_this_week_protected_anchor", codes)
        self.assertIn("win_this_week_embedded_protected_anchor", codes)

    def test_cross_endpoint_snapshot_and_write_boundaries_fail_closed(self):
        payloads = healthy_payloads()
        payloads["/api/attention"]["snapshot_id"] = 263
        payloads["/api/hot-swaps/latest"]["writes_enabled"] = True
        proposal = payloads["/api/hot-swaps/latest"]["proposals"][0]["proposal"]
        proposal.update({"writes_enabled": True, "executable": True, "status": "executable"})
        proposal["contract"]["snapshot_id"] = 263

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("attention_snapshot_mismatch", codes)
        self.assertIn("hot_swaps_write_boundary", codes)
        self.assertIn("hot_swaps_executable_proposal", codes)
        self.assertIn("hot_swaps_contract_snapshot_mismatch", codes)

    def test_nonpositive_waiver_card_fails(self):
        payloads = healthy_payloads()
        payloads["/api/waiver-swaps/latest"]["cards"] = [
            {"net_delta": 0},
            {"net_delta": -1.0},
        ]

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        matching = [
            item for item in report["failures"]
            if item["code"] == "waivers_nonpositive_delta"
        ]
        self.assertEqual(len(matching), 1)
        self.assertIn("2 waiver card(s)", matching[0]["message"])

    def test_low_confidence_or_age_blind_waiver_card_fails(self):
        payloads = healthy_payloads()
        payloads["/api/waiver-swaps/latest"]["cards"] = [{
            "net_delta": 2.0,
            "confidence": "Low",
            "add": {"age": None, "score_source": "_cells inferred FP/G"},
            "move_out": {"age": None},
        }]

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        self.assertIn("waivers_untrusted_card", {item["code"] for item in report["failures"]})

    def test_owner_protected_anchor_waiver_card_fails(self):
        payloads = healthy_payloads()
        payloads["/api/waiver-swaps/latest"]["cards"][0]["net_delta"] = -1.0
        payloads["/api/waiver-swaps/latest"]["cards"][0]["move_out"] = {
            "name": "  AARON   JUDGE ",
            "age": 34,
        }

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("waivers_protected_anchor", codes)
        self.assertIn("waivers_nonpositive_delta", codes)

    def test_trade_index_requires_complete_age_and_value_provenance(self):
        payloads = healthy_payloads()
        payloads["/api/snapshot/latest"]["player_index"][1]["age"] = None
        payloads["/api/snapshot/latest"]["player_index"][2]["fppg"] = float("inf")

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("trade_index_age_coverage", codes)
        self.assertIn("trade_index_value_coverage", codes)

    def test_numeric_age_without_trusted_source_fails_trade_and_waiver_contracts(self):
        payloads = healthy_payloads()
        payloads["/api/snapshot/latest"]["player_index"][1]["age_source"] = "inferred"
        payloads["/api/waiver-swaps/latest"]["cards"][0]["move_out"]["age_source"] = None

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("trade_index_age_coverage", codes)
        self.assertIn("waivers_untrusted_card", codes)

    def test_matchup_contract_rejects_legacy_claims_duplicates_and_unchecked_bridge(self):
        payloads = healthy_payloads()
        matchup = payloads["/api/snapshot/latest"]["matchup"]
        projection = matchup["projection"]
        projection.pop("scoring_basis")
        projection["probability_calibrated"] = True

        first = matchup["recommendations"]["recommendations"][0]
        first["replacement_card"]["proposal"]["contract"]["version"] = 1
        first["replacement_card"]["proposal"]["contract"]["slot_moves"].insert(
            1,
            {"order": 2, "player_id": "bridge", "from_slot": "UT", "to_slot": "OF"},
        )
        duplicate = copy.deepcopy(first)
        duplicate["rank"] = 2
        matchup["recommendations"]["recommendations"].append(duplicate)

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["failures"]}
        self.assertIn("matchup_scoring_basis", codes)
        self.assertIn("matchup_probability_claim", codes)
        self.assertIn("matchup_contract_version", codes)
        self.assertIn("matchup_dominated_duplicate", codes)
        self.assertIn("matchup_movability_coverage", codes)

    def test_uncalibrated_probability_cannot_drive_actionable_matchup_advice(self):
        payloads = healthy_payloads()
        matchup = payloads["/api/snapshot/latest"]["matchup"]
        recommendation = matchup["recommendations"]["recommendations"][0]
        recommendation["win_probability_delta"] = 0.02
        recommendation["replacement_card"]["projected_benefit"]["new_win_probability"] = 0.57

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        self.assertIn(
            "matchup_uncalibrated_action_claim",
            {item["code"] for item in report["failures"]},
        )

    def test_matchup_recommendation_cannot_promote_suspended_player(self):
        payloads = healthy_payloads()
        recommendation = payloads["/api/snapshot/latest"]["matchup"]["recommendations"]["recommendations"][0]
        recommendation["replacement_card"]["move_in"]["injury"] = "SUSP"

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        self.assertIn(
            "matchup_unavailable_move_in",
            {item["code"] for item in report["failures"]},
        )

    def test_matchup_recommendation_honors_explicit_unavailable_contract(self):
        payloads = healthy_payloads()
        recommendation = payloads["/api/snapshot/latest"]["matchup"]["recommendations"]["recommendations"][0]
        recommendation["replacement_card"]["move_in"]["unavailable"] = True

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        self.assertIn(
            "matchup_unavailable_move_in",
            {item["code"] for item in report["failures"]},
        )

    def test_transport_failure_is_sanitized_and_fails(self):
        payloads = healthy_payloads()
        payloads.pop("/api/attention")

        report = monitor.evaluate_payloads(
            payloads,
            transport_errors={"/api/attention": "HTTP 503"},
            checked_at=NOW,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["failures"][0]["code"], "endpoint_unavailable")
        self.assertNotIn("Roster Player", monitor.render_markdown(report))

    def test_degraded_advice_readiness_warns_without_triggering_repair(self):
        payloads = healthy_payloads()
        payloads["/api/snapshot/latest"]["data_quality"]["lineup_slots"] = {"state": "partial"}
        payloads["/api/waiver-swaps/latest"]["data_quality"]["add_drop_recommendations_ready"] = False

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertTrue(report["ok"])
        codes = {item["code"] for item in report["warnings"]}
        self.assertEqual(codes, {"lineup_advice_paused", "waiver_advice_paused"})

    def test_explicitly_paused_projection_does_not_fail_monitor(self):
        payloads = healthy_payloads()
        snapshot = payloads["/api/snapshot/latest"]
        snapshot["data_quality"]["projection_ready"] = False
        snapshot["matchup"]["projection"] = None
        snapshot["matchup"]["recommendations"] = {
            "model_version": "matchup_projection_v4",
            "base_projection": None,
            "recommendations": [],
            "no_action": {"reason": "Projection data incomplete"},
        }

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertTrue(report["ok"])
        matchup_check = next(check for check in report["checks"] if check["name"] == "matchup")
        self.assertEqual(matchup_check["state"], "paused")

    def test_shifted_editable_period_plan_explains_missing_current_projection(self):
        payloads = healthy_payloads()
        snapshot = payloads["/api/snapshot/latest"]
        snapshot["data_quality"].update({
            "projection_ready": True,
            "lineup_recommendations_ready": False,
            "current_period": {
                "state": "mismatch",
                "editable_period": 17,
                "matchup_period": 16,
            },
        })
        snapshot["matchup"].update({
            "period_number": 16,
            "projection": None,
            "recommendations": {
                "model_version": "matchup_projection_v4",
                "base_projection": None,
                "recommendations": [],
                "no_action": {"reason": "Current-period lineup slots are not editable."},
            },
        })
        snapshot["win_this_week"]["planning_horizon"] = {
            "mode": "editable_period",
            "period_number": 17,
            "shifted_from_period": 16,
        }
        snapshot["win_this_week"]["matchup"] = {
            "projected_my": 253.5,
            "projected_opponent": 256.0,
            "projected_margin": -2.5,
        }

        failures = []
        matchup_check = monitor._validate_matchup_surface(
            snapshot,
            str(snapshot["snapshot_id"]),
            lambda code, message: failures.append({"code": code, "message": message}),
        )

        self.assertEqual(failures, [])
        self.assertEqual(matchup_check["state"], "paused")

    def test_inconsistent_shifted_period_metadata_does_not_hide_missing_projection(self):
        payloads = healthy_payloads()
        snapshot = payloads["/api/snapshot/latest"]
        snapshot["data_quality"].update({
            "projection_ready": True,
            "lineup_recommendations_ready": False,
            "current_period": {
                "state": "mismatch",
                "editable_period": 18,
                "matchup_period": 16,
            },
        })
        snapshot["matchup"].update({
            "period_number": 16,
            "projection": None,
            "recommendations": {
                "model_version": "matchup_projection_v4",
                "base_projection": None,
                "recommendations": [],
                "no_action": {"reason": "Current-period lineup slots are not editable."},
            },
        })
        snapshot["win_this_week"].update({
            "state": "ready",
            "planning_horizon": {
                "mode": "editable_period",
                "period_number": 17,
                "shifted_from_period": 16,
            },
            "matchup": {},
        })

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertFalse(report["ok"])
        self.assertIn(
            "matchup_projection_missing",
            {item["code"] for item in report["failures"]},
        )

    def test_shifted_no_action_plan_with_projection_passes_full_monitor(self):
        payloads = healthy_payloads()
        snapshot = payloads["/api/snapshot/latest"]
        snapshot["data_quality"].update({
            "projection_ready": True,
            "lineup_recommendations_ready": False,
            "current_period": {
                "state": "mismatch",
                "editable_period": 17,
                "matchup_period": 16,
            },
        })
        snapshot["matchup"].update({
            "period_number": 16,
            "projection": None,
            "recommendations": {
                "model_version": "matchup_projection_v4",
                "base_projection": None,
                "recommendations": [],
                "no_action": {"reason": "Current-period lineup slots are not editable."},
            },
        })
        target = {
            "period_number": 17,
            "start": "2026-07-13",
            "end": "2026-07-26",
            "matchup_key": 6,
        }
        for plan in (snapshot["win_this_week"], payloads["/api/win-this-week/latest"]):
            plan.update({
                "state": "no_action",
                "actions": [],
                "primary_action_id": None,
                "planning_horizon": {
                    "mode": "editable_period",
                    "shifted_from_period": 16,
                    **target,
                },
                "current_period": {
                    "state": "ok",
                    "editable_period": 17,
                    "matchup_period": 17,
                },
                "matchup": {
                    "complete": False,
                    "projected_my": 253.5,
                    "projected_opponent": 256.0,
                    "projected_margin": -2.5,
                },
                "handoffs": {},
                "monitoring_actions": [{
                    "id": "monitor:future-period-waiver-boundary",
                    "state": "blocked",
                }],
                "no_action": {
                    "reason": "No legal lineup change clears the meaningful-gain threshold.",
                    "alternatives": [],
                },
            })

        report = monitor.evaluate_payloads(payloads, checked_at=NOW)

        self.assertTrue(report["ok"])
        self.assertNotIn(
            "matchup_projection_missing",
            {item["code"] for item in report["failures"]},
        )


if __name__ == "__main__":
    unittest.main()
