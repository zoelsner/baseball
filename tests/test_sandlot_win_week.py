import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import sandlot_win_week
from fastapi import HTTPException

from sandlot_api import _snapshot_payload, latest_action_proposal, latest_win_this_week


NOW = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)


def game(day, hour=23):
    return {
        "date": f"2026-05-{day:02d}",
        "gameDate": f"2026-05-{day:02d}T{hour:02d}:05:00Z",
        "game_pk": day * 100 + hour,
        "source": "mlb_schedule",
    }


def roster_player(pid, name, *, slot, positions, fppg, games=1, age=29):
    return {
        "id": pid,
        "name": name,
        "team": "NYY",
        "slot": slot,
        "slot_source": "raw.statusId" if slot == "BN" else "raw.lineupSlot",
        "positions": positions,
        "all_positions": [positions],
        "fppg": fppg,
        "age": age,
        "age_source": "raw.scorer.playerAge",
        "future_games": [game(14 + index) for index in range(games)],
        "future_games_source": "mlb_schedule",
        "future_games_status": "ok",
        "future_games_scope": "team_games",
        "raw": {"scorer": {"disableLineupChange": False}},
        "transaction_eligibility": {
            "source": "fantrax.raw.actions.typeId",
            "action_type_ids": ["3", "4"],
            "drop_available": True,
            "trade_available": True,
        },
    }


def free_agent(pid="fa", name="Impact Add", *, fppg=5.0, games=2, positions="2B", age=30):
    return {
        "id": pid,
        "name": name,
        "team": "BOS",
        "positions": positions,
        "multi_positions": [positions],
        "age": age,
        "age_source": "raw.scorer.playerAge",
        "stats": {"FP/G": fppg},
        "future_games": [game(14 + index, 22) for index in range(games)],
        "future_games_source": "mlb_schedule",
        "future_games_status": "ok",
        "future_games_scope": "team_games",
    }


def snapshot_row(*, roster=None, free_agents=None):
    my_rows = roster or [
        roster_player("weak", "Weak Starter", slot="2B", positions="2B", fppg=1.0),
        roster_player("bench", "Bench Upgrade", slot="BN", positions="2B", fppg=4.0),
    ]
    return {
        "id": 501,
        "taken_at": "2026-05-14T12:00:00Z",
        "data": {
            "league_id": "league",
            "team_id": "me",
            "matchup": {
                "my_score": 40,
                "opponent_score": 50,
                "opponent_team_id": "opp",
                "period_number": 5,
                "start": "2026-05-11",
                "end": "2026-05-17",
                "complete": False,
            },
            "roster": {
                "rows": my_rows,
                "period_number": 5,
                "period_start": "2026-05-11",
                "period_end": "2026-05-17",
                "period_source": "fantrax.getTeamRosterInfo.displayedSelections",
            },
            "all_team_rosters": {
                "opp": {
                    "rows": [
                        roster_player("opp", "Opponent", slot="SS", positions="SS", fppg=2.0),
                    ],
                },
            },
            "free_agents": {
                "method": "getPlayerStats",
                "players": [free_agent()] if free_agents is None else free_agents,
            },
        },
    }


class WinThisWeekTests(unittest.TestCase):
    def test_plans_future_editable_period_lineup_without_enabling_waivers(self):
        row = snapshot_row()
        row["data"]["roster"].update({
            "period_number": 6,
            "period_start": None,
            "period_end": None,
        })
        row["data"]["editable_matchup"] = {
            "source": "fantrax_schedule",
            "period_number": 6,
            "start": "2026-05-18",
            "end": "2026-05-24",
            "complete": False,
            "score_state": "not_started",
            "matchup_key": "period-6",
            "my_score": 0,
            "opponent_score": 0,
            "opponent_team_id": "opp",
            "opponent_team_name": "Next Opponent",
        }
        row["data"]["all_team_rosters"]["opp"].update({
            "period_number": 6,
            "period_source": "fantrax.getTeamRosterInfo.displayedSelections",
        })
        for player in row["data"]["roster"]["rows"]:
            player["future_games"] = [game(18)]
        row["data"]["all_team_rosters"]["opp"]["rows"][0]["future_games"] = [game(18)]

        with patch("sandlot_win_week.sandlot_waivers.payload_for_snapshot") as waiver_planner:
            plan = sandlot_win_week.build_plan(row, now=NOW)

        waiver_planner.assert_not_called()
        self.assertEqual(plan["planning_horizon"]["mode"], "editable_period")
        self.assertEqual(plan["planning_horizon"]["period_number"], 6)
        self.assertEqual(plan["state"], "ready")
        self.assertTrue(plan["actions"])
        self.assertTrue(all(action["kind"] in {"lineup", "lineup_plan"} for action in plan["actions"]))
        self.assertTrue(all(action["target_period"]["period_number"] == 6 for action in plan["actions"]))
        self.assertEqual(plan["current_matchup"]["my_score"], 40.0)
        self.assertEqual(plan["matchup"]["my_score"], 0.0)
        self.assertIn("Planning Period 6", plan["summary"]["headline"])
        self.assertIn("research-only", plan["monitoring_actions"][0]["title"])
        self.assertEqual(plan["handoffs"]["lineup"]["target_period"]["period_number"], 6)
        review = plan["actions"][0]["review"]
        self.assertEqual(review["state"], "reviewable")
        self.assertEqual(review["snapshot_id"], 501)
        self.assertEqual(review["target_period"]["period_number"], 6)
        self.assertEqual(review["slot_moves"], review["contract"]["slot_moves"])
        self.assertEqual(review["executor"]["state"], "offline")
        self.assertFalse(review["writes_enabled"])

    def test_future_period_plan_requires_matching_opponent_roster_period(self):
        row = snapshot_row()
        row["data"]["roster"]["period_number"] = 6
        row["data"]["editable_matchup"] = {
            "source": "fantrax_schedule",
            "period_number": 6,
            "start": "2026-05-18",
            "end": "2026-05-24",
            "complete": False,
            "score_state": "not_started",
            "matchup_key": "period-6",
            "my_score": 0,
            "opponent_score": 0,
            "opponent_team_id": "opp",
        }

        plan = sandlot_win_week.build_plan(row, now=NOW)

        self.assertEqual(plan["state"], "paused")
        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["planning_horizon"]["blocked_editable_period"], 6)
        self.assertIn("opponent roster", plan["planning_horizon"]["blocked_reason"])

    def test_future_period_plan_rejects_unproven_started_score(self):
        row = snapshot_row()
        row["data"]["roster"]["period_number"] = 6
        row["data"]["editable_matchup"] = {
            "source": "fantrax_schedule",
            "period_number": 6,
            "start": "2026-05-18",
            "end": "2026-05-24",
            "complete": False,
            "score_state": "invalid_future_score",
            "matchup_key": "period-6",
            "my_score": 1,
            "opponent_score": 0,
            "opponent_team_id": "opp",
        }

        plan = sandlot_win_week.build_plan(row, now=NOW)

        self.assertEqual(plan["state"], "paused")
        self.assertEqual(plan["actions"], [])
        self.assertIn("not proven", plan["planning_horizon"]["blocked_reason"])

    def test_latest_action_proposal_rederives_exact_review_from_latest_snapshot(self):
        row = snapshot_row()
        plan = sandlot_win_week.build_plan(row, now=NOW)
        reviewed_action = next(action for action in plan["actions"] if (action.get("review") or {}).get("state") == "reviewable")
        proposal_id = reviewed_action["review"]["proposal_id"]

        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=row),
            patch("sandlot_win_week._aware_now", return_value=NOW),
            patch("sandlot_matchup._movability_now", return_value=NOW),
        ):
            payload = latest_action_proposal(
                proposal_id,
                snapshot_id=reviewed_action["review"]["snapshot_id"],
                input_hash=reviewed_action["review"]["input_hash"],
            )

        self.assertTrue(payload["is_current"])
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["writes_enabled"])
        self.assertEqual(payload["review"]["proposal_id"], proposal_id)
        self.assertEqual(payload["review"]["input_hash"], payload["review"]["contract"]["input_hash"])
        self.assertEqual(payload["execution"]["state"], "offline")
        self.assertFalse(payload["execution"]["request_enabled"])

    def test_latest_action_proposal_rejects_stale_or_replaced_id(self):
        row = snapshot_row()

        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=row),
            patch("sandlot_win_week._aware_now", return_value=NOW),
            patch("sandlot_matchup._movability_now", return_value=NOW),
        ):
            with self.assertRaises(HTTPException) as raised:
                latest_action_proposal(
                    "lineup-swap:stale:proposal",
                    snapshot_id=501,
                    input_hash="a" * 64,
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertIn("latest actionable plan", str(raised.exception.detail))

    def test_latest_action_proposal_rejects_recurring_id_with_stale_hash(self):
        row = snapshot_row()
        plan = sandlot_win_week.build_plan(row, now=NOW)
        reviewed_action = next(action for action in plan["actions"] if (action.get("review") or {}).get("state") == "reviewable")
        review = reviewed_action["review"]

        with (
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=row),
            patch("sandlot_win_week._aware_now", return_value=NOW),
            patch("sandlot_matchup._movability_now", return_value=NOW),
        ):
            with self.assertRaises(HTTPException) as raised:
                latest_action_proposal(
                    review["proposal_id"],
                    snapshot_id=review["snapshot_id"],
                    input_hash="f" * 64,
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("stale or replaced", str(raised.exception.detail))

    def test_mismatched_editable_period_pauses_actions_before_planners_run(self):
        row = snapshot_row()
        row["data"]["roster"].update({
            "period_number": 6,
            "period_start": "2026-05-18",
            "period_end": "2026-05-24",
        })

        with (
            patch("sandlot_win_week.sandlot_matchup.rank_matchup_improvement_actions") as lineup_planner,
            patch("sandlot_win_week.sandlot_waivers.payload_for_snapshot") as waiver_planner,
        ):
            plan = sandlot_win_week.build_plan(row, now=NOW)

        lineup_planner.assert_not_called()
        waiver_planner.assert_not_called()
        self.assertEqual(plan["state"], "paused")
        self.assertEqual(plan["actions"], [])
        self.assertIsNone(plan["primary_action_id"])
        self.assertEqual(plan["handoffs"], {})
        self.assertEqual(plan["monitoring_actions"][0]["state"], "blocked")
        self.assertIn("Period 6", plan["no_action"]["reason"])
        self.assertIn("Period 5", plan["no_action"]["reason"])
        self.assertEqual(plan["no_action"]["alternatives"], [])
        self.assertIsNotNone(plan["matchup"]["projected_my"])

    def test_missing_editable_period_needs_refresh_and_pauses_actions(self):
        row = snapshot_row()
        row["data"]["roster"].update({
            "period_number": None,
            "period_start": None,
            "period_end": None,
        })

        plan = sandlot_win_week.build_plan(row, now=NOW)

        self.assertEqual(plan["state"], "paused")
        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["monitoring_actions"][0]["state"], "needs_refresh")
        self.assertEqual(plan["handoffs"], {})

    def test_ranks_proven_waiver_path_over_smaller_lineup_gain(self):
        plan = sandlot_win_week.build_plan(snapshot_row(), now=NOW)

        self.assertEqual(plan["state"], "ready")
        self.assertTrue(plan["read_only"])
        self.assertFalse(plan["writes_enabled"])
        self.assertEqual(plan["handoffs"]["lineup"], {
            "label": "Open Fantrax lineup",
            "url": "https://www.fantrax.com/fantasy/league/league/team/roster;teamId=me",
            "method": "GET",
            "read_only": True,
            "writes_enabled": False,
            "target_period": {
                "period_number": 5,
                "start": "2026-05-11",
                "end": "2026-05-17",
                "matchup_key": None,
            },
        })
        self.assertEqual(plan["summary"]["headline"], "Down 10.0; the best current path adds about 9.0 projected points.")
        self.assertEqual(plan["summary"]["projected_margin_before_action"], -11.0)
        self.assertEqual(plan["summary"]["projected_margin_after_action"], -2.0)
        self.assertEqual(
            plan["summary"]["outlook"],
            "After this move, the remaining-week estimate leaves you 2.0 points behind.",
        )
        self.assertEqual([action["rank"] for action in plan["actions"]], list(range(1, len(plan["actions"]) + 1)))

        primary = plan["actions"][0]
        self.assertEqual(primary["kind"], "waiver")
        self.assertEqual(primary["state"], "review_now")
        self.assertEqual(primary["expected_points"]["estimate"], 9.0)
        self.assertEqual(primary["expected_points"]["incremental_over_best_lineup"], 6.0)
        self.assertTrue(primary["expected_points"]["comparable"])
        self.assertEqual(primary["deadline"]["at"], "2026-05-14T22:05:00+00:00")
        self.assertEqual(primary["legality"]["state"], "provisionally_legal")
        self.assertIn("live_fantrax_availability_and_transaction_preflight", primary["legality"]["blocked_by"])
        self.assertEqual(primary["dynasty_cost"]["level"], "low")
        add_step = next(step for step in primary["steps"] if step.get("action") == "add")
        self.assertEqual(add_step["to_slot"], "2B")
        self.assertTrue(any(step.get("player_id") == "fa" and step.get("to_slot") == "2B" for step in primary["steps"]))

        lineup = next(action for action in plan["actions"] if action["kind"] == "lineup")
        self.assertEqual(lineup["expected_points"]["estimate"], 3.0)
        self.assertEqual(lineup["dynasty_cost"]["level"], "none")
        self.assertIn("Fantrax destination eligibility", lineup["legality"]["verified"])
        self.assertEqual(plan["diagnostics"]["probability_calibrated"], False)
        self.assertIsNone(primary["win_probability_delta"])
        self.assertTrue(any(item["state"] == "scheduled_check" for item in plan["monitoring_actions"]))

    def test_untrusted_free_agent_schedule_is_monitor_only(self):
        add = free_agent()
        add.update({
            "future_games": [],
            "future_games_status": "fetch_error",
            "future_games_reason": "MLB schedule fetch failed",
        })

        plan = sandlot_win_week.build_plan(snapshot_row(free_agents=[add]), now=NOW)

        self.assertFalse(any(action["kind"] == "waiver" for action in plan["actions"]))
        schedule_monitor = next(
            item for item in plan["monitoring_actions"]
            if item["id"].endswith(":schedule")
        )
        self.assertEqual(schedule_monitor["state"], "needs_refresh")
        self.assertIn("MLB schedule fetch failed", schedule_monitor["reason"])

    def test_lower_rate_streamer_can_rank_when_extra_games_add_weekly_points(self):
        roster = [
            roster_player("starter", "One Game Starter", slot="2B", positions="2B", fppg=4.0, games=1, age=31),
        ]
        streamer = free_agent(
            pid="streamer",
            name="Three Game Streamer",
            fppg=3.0,
            games=3,
            positions="2B",
            age=31,
        )

        plan = sandlot_win_week.build_plan(
            snapshot_row(roster=roster, free_agents=[streamer]),
            now=NOW,
        )

        waiver = next(action for action in plan["actions"] if action["kind"] == "waiver")
        self.assertEqual(waiver["expected_points"]["estimate"], 5.0)
        self.assertEqual(waiver["source"]["type"], "waiver_card")
        self.assertEqual(waiver["confidence"], "low")

    def test_locked_move_out_is_monitor_only(self):
        roster = [
            roster_player("locked", "Locked Starter", slot="2B", positions="2B", fppg=1.0, age=31),
        ]
        roster[0]["transaction_eligibility"]["drop_available"] = False
        roster[0]["transaction_eligibility"]["action_type_ids"] = ["4"]

        plan = sandlot_win_week.build_plan(snapshot_row(roster=roster), now=NOW)

        self.assertFalse(any(action["kind"] == "waiver" for action in plan["actions"]))
        move_out_monitor = next(
            item for item in plan["monitoring_actions"]
            if item["id"].endswith(":move-out")
        )
        self.assertEqual(move_out_monitor["state"], "needs_refresh")
        self.assertIn("does not expose a Drop action", move_out_monitor["reason"])

    def test_locked_post_add_lineup_bridge_is_not_actionable(self):
        roster = [
            roster_player("weak", "Locked Active Player", slot="2B", positions="2B", fppg=1.0, age=31),
            roster_player("bench-drop", "Bench Drop", slot="BN", positions="2B", fppg=0.5, age=31),
        ]
        roster[0]["raw"]["scorer"]["disableLineupChange"] = True
        roster[0]["transaction_eligibility"]["drop_available"] = False
        roster[0]["transaction_eligibility"]["action_type_ids"] = ["4"]

        plan = sandlot_win_week.build_plan(snapshot_row(roster=roster), now=NOW)

        self.assertFalse(any(action["kind"] == "waiver" for action in plan["actions"]))
        rejected = [
            item for item in plan["diagnostics"]["considered"]
            if item.get("kind") == "waiver" and item.get("status") == "post_add_lineup_unverified"
        ]
        self.assertTrue(rejected)
        monitor = next(
            item for item in plan["monitoring_actions"]
            if ":post-add-movability" in item["id"]
        )
        self.assertEqual(monitor["state"], "needs_refresh")

    def test_unknown_post_add_lineup_movability_and_deadline_are_not_actionable(self):
        roster = [
            roster_player("weak", "Active Player", slot="2B", positions="2B", fppg=1.0, age=31),
            roster_player("bench-drop", "Bench Drop", slot="BN", positions="2B", fppg=0.5, age=31),
        ]
        roster[0]["transaction_eligibility"]["drop_available"] = False
        roster[0]["transaction_eligibility"]["action_type_ids"] = ["4"]
        roster[0]["future_games"][0].pop("gameDate")

        add = free_agent()
        add["raw"] = {"scorer": {"disableLineupChange": False}}
        plan = sandlot_win_week.build_plan(snapshot_row(roster=roster, free_agents=[add]), now=NOW)

        self.assertFalse(any(action["kind"] == "waiver" for action in plan["actions"]))
        rejected = [
            item for item in plan["diagnostics"]["considered"]
            if item.get("kind") == "waiver" and item.get("status") == "post_add_lineup_unverified"
        ]
        self.assertTrue(rejected)
        monitor = next(
            item for item in plan["monitoring_actions"]
            if ":post-add-movability" in item["id"]
        )
        self.assertEqual(monitor["deadline"]["state"], "unknown")

    def test_waiver_move_dominated_by_free_lineup_change_is_rejected(self):
        roster = [
            roster_player("weak", "Weak Starter", slot="2B", positions="2B", fppg=1.0, games=1, age=31),
            roster_player("better-bench", "Better Bench Option", slot="BN", positions="2B", fppg=5.0, games=2, age=31),
        ]
        inferior_add = free_agent(
            pid="inferior-add",
            name="Inferior Transaction",
            fppg=4.0,
            games=2,
            positions="2B",
            age=31,
        )

        plan = sandlot_win_week.build_plan(
            snapshot_row(roster=roster, free_agents=[inferior_add]),
            now=NOW,
        )

        self.assertTrue(any(action["kind"] == "lineup" for action in plan["actions"]))
        self.assertFalse(any(action["kind"] == "waiver" for action in plan["actions"]))
        dominated = [
            item for item in plan["diagnostics"]["considered"]
            if item.get("kind") == "waiver" and item.get("status") == "dominated"
        ]
        self.assertTrue(dominated)

    def test_combines_multiple_legal_lineup_changes_into_one_plan(self):
        roster = [
            roster_player("weak-2b", "Weak Second Baseman", slot="2B", positions="2B", fppg=1.0),
            roster_player("bench-2b", "Bench Second Baseman", slot="BN", positions="2B", fppg=4.0),
            roster_player("weak-ss", "Weak Shortstop", slot="SS", positions="SS", fppg=1.0),
            roster_player("bench-ss", "Bench Shortstop", slot="BN", positions="SS", fppg=3.0),
        ]

        plan = sandlot_win_week.build_plan(
            snapshot_row(roster=roster, free_agents=[]),
            now=NOW,
        )

        bundle = next(action for action in plan["actions"] if action["kind"] == "lineup_plan")
        self.assertEqual(bundle["rank"], 1)
        self.assertEqual(bundle["title"], "Make 2 lineup changes")
        self.assertEqual(bundle["expected_points"]["estimate"], 5.0)
        self.assertEqual(len(bundle["segments"]), 2)
        self.assertIn("Fantrax destination eligibility", bundle["legality"]["verified"])
        self.assertEqual({step["player_id"] for step in bundle["steps"]}, {
            "weak-2b", "bench-2b", "weak-ss", "bench-ss",
        })

    def test_waiver_plan_also_applies_independent_lineup_gains(self):
        roster = [
            roster_player("weak-2b", "Weak Second Baseman", slot="2B", positions="2B", fppg=1.0, age=31),
            roster_player("weak-ss", "Weak Shortstop", slot="SS", positions="SS", fppg=1.0, age=31),
            roster_player("bench-ss", "Bench Shortstop", slot="BN", positions="SS", fppg=4.0, age=31),
        ]
        impact_add = free_agent(
            pid="impact-2b",
            name="Impact Second Baseman",
            fppg=5.0,
            games=1,
            positions="2B",
            age=31,
        )

        plan = sandlot_win_week.build_plan(
            snapshot_row(roster=roster, free_agents=[impact_add]),
            now=NOW,
        )

        waiver = next(action for action in plan["actions"] if action["kind"] == "waiver")
        self.assertEqual(waiver["expected_points"]["estimate"], 7.0)
        self.assertEqual(waiver["expected_points"]["incremental_over_best_lineup"], 4.0)
        self.assertTrue(any(step.get("player_id") == "bench-ss" and step.get("to_slot") == "SS" for step in waiver["steps"]))
        self.assertEqual(len(waiver["lineup_segments"]), 1)

    def test_weekly_candidate_frontier_prefers_more_countable_points(self):
        roster = [
            roster_player("weak", "Weak Starter", slot="2B", positions="2B", fppg=1.0, age=31),
        ]
        candidates = [
            free_agent(pid=f"high-rate-{index}", name=f"High Rate {index}", fppg=6.0, games=1, positions="2B", age=31)
            for index in range(35)
        ]
        candidates.append(
            free_agent(pid="weekly-volume", name="Weekly Volume", fppg=4.0, games=4, positions="2B", age=31)
        )

        plan = sandlot_win_week.build_plan(
            snapshot_row(roster=roster, free_agents=candidates),
            now=NOW,
        )

        primary = plan["actions"][0]
        self.assertEqual(primary["kind"], "waiver")
        self.assertIn("Weekly Volume", primary["title"])
        self.assertEqual(primary["expected_points"]["estimate"], 15.0)

    def test_plan_surfaces_pitcher_lower_bound_caveat(self):
        row = snapshot_row(free_agents=[])
        row["data"]["roster"]["rows"].append({
            "id": "no-probable",
            "name": "No Posted Probable",
            "team": "NYY",
            "slot": "SP",
            "slot_source": "raw.lineupSlot",
            "positions": "SP",
            "all_positions": ["SP"],
            "fppg": 10.0,
            "future_games": [],
            "team_future_games": [game(14)],
            "future_games_source": "mlb_schedule",
            "future_games_status": "pitcher_probables_unavailable",
            "future_games_scope": "pitcher_probable_starts",
        })

        plan = sandlot_win_week.build_plan(row, now=NOW)

        self.assertEqual(plan["matchup"]["opportunity_completeness"], "known_opportunities_lower_bound")
        self.assertEqual(plan["matchup"]["pitchers_without_probable_start"], 1)
        self.assertIn("1 pitcher(s)", plan["summary"]["projection_caveat"])

    def test_aaron_judge_never_appears_as_a_waiver_move_out(self):
        roster = [
            roster_player("judge", "Aaron Judge", slot="OF", positions="OF", fppg=0.5, age=34),
            roster_player("weak", "Weak Starter", slot="2B", positions="2B", fppg=1.0, age=31),
            roster_player("bench", "Bench Player", slot="BN", positions="2B", fppg=1.5, age=31),
        ]

        plan = sandlot_win_week.build_plan(snapshot_row(roster=roster), now=NOW)

        waiver_actions = [action for action in plan["actions"] if action["kind"] == "waiver"]
        self.assertTrue(waiver_actions)
        for action in waiver_actions:
            move_out_steps = [step for step in action["steps"] if step.get("action") == "move_out"]
            self.assertTrue(move_out_steps)
            self.assertNotEqual(move_out_steps[0]["player_id"], "judge")

    def test_complete_matchup_returns_no_actions(self):
        row = snapshot_row()
        row["data"]["matchup"].update({"complete": True, "my_score": 80, "opponent_score": 75})

        plan = sandlot_win_week.build_plan(row, now=NOW)

        self.assertEqual(plan["state"], "complete")
        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["no_action"]["reason"], "The matchup is complete.")

    def test_no_action_surfaces_the_best_rejected_lineup_alternative(self):
        roster = [
            roster_player("weak", "Weak Starter", slot="2B", positions="2B", fppg=1.0),
            roster_player("bench", "Small Upgrade", slot="BN", positions="2B", fppg=1.4),
        ]

        plan = sandlot_win_week.build_plan(
            snapshot_row(roster=roster, free_agents=[]),
            now=NOW,
        )

        self.assertEqual(plan["state"], "no_action")
        self.assertEqual(plan["actions"], [])
        alternative = plan["no_action"]["alternatives"][0]
        self.assertEqual(alternative["title"], "Start Small Upgrade over Weak Starter")
        self.assertEqual(alternative["expected_points"]["estimate"], 0.4)
        self.assertEqual(alternative["status"], "below_threshold")
        self.assertIn("1.0-point meaningful-gain threshold", alternative["reason"])
        self.assertEqual([step["player_id"] for step in alternative["steps"]], ["bench", "weak"])
        self.assertIsNone(plan["summary"]["projected_margin_after_action"])
        self.assertIn("current remaining-week estimate", plan["summary"]["outlook"].lower())

    def test_snapshot_api_payload_exposes_the_read_only_plan(self):
        payload = _snapshot_payload(snapshot_row())

        self.assertEqual(payload["win_this_week"]["model_version"], sandlot_win_week.MODEL_VERSION)
        self.assertTrue(payload["win_this_week"]["read_only"])
        self.assertFalse(payload["win_this_week"]["writes_enabled"])
        self.assertEqual(payload["win_this_week"]["snapshot_id"], 501)
        self.assertEqual(payload["win_this_week"]["schedule_optimizer"]["state"], "policy_missing")
        self.assertFalse(payload["win_this_week"]["schedule_optimizer"]["writes_enabled"])

    def test_self_declared_policy_does_not_advertise_a_nonexistent_optimizer(self):
        row = snapshot_row()
        row["data"]["league_rules"] = {
            "lineup_change_policy": {
                "state": "trusted",
                "cadence": "weekly",
                "lock_scope": "period",
                "change_limit": None,
                "source": "fantrax.fixture.lineupPeriod",
                "reason": "Fixture-backed weekly cadence.",
            },
        }

        plan = sandlot_win_week.build_plan(row, now=NOW)

        self.assertEqual(plan["schedule_optimizer"]["state"], "policy_missing")
        self.assertIsNone(plan["schedule_optimizer"]["policy"]["cadence"])
        self.assertIsNone(plan["schedule_optimizer"]["policy"]["lock_scope"])
        self.assertTrue(plan["read_only"])
        self.assertFalse(plan["writes_enabled"])

    def test_unclassified_policy_evidence_does_not_change_static_action_ranking(self):
        row = snapshot_row()
        baseline = sandlot_win_week.build_plan(row, now=NOW)
        row["data"]["league_rules"] = {
            "lineup_change_policy": {
                "state": "observed_unclassified",
                "cadence": None,
                "lock_scope": None,
                "change_limit": None,
                "source": "fantrax.getLeagueRules.raw",
                "reason": "Exact mapping is not trusted yet.",
                "candidates": [{
                    "path": "settings.lineupChangePeriod",
                    "value_type": "str",
                    "hint": "weekly",
                }],
            },
        }

        observed = sandlot_win_week.build_plan(row, now=NOW)

        self.assertEqual(observed["actions"], baseline["actions"])
        self.assertEqual(observed["summary"], baseline["summary"])
        self.assertEqual(observed["schedule_optimizer"]["state"], "policy_unclassified")
        self.assertEqual(observed["schedule_optimizer"]["policy"]["candidate_count"], 1)
        self.assertNotIn("candidates", observed["schedule_optimizer"]["policy"])

    def test_dedicated_endpoint_matches_snapshot_plan_exactly(self):
        row = snapshot_row()

        with patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=row):
            dedicated = latest_win_this_week()
        embedded = _snapshot_payload(row)["win_this_week"]

        self.assertEqual(dedicated, embedded)

    def test_lineup_gain_with_unknown_start_time_is_monitor_only(self):
        roster = [
            roster_player("weak", "Weak Starter", slot="2B", positions="2B", fppg=1.0),
            roster_player("bench", "Bench Upgrade", slot="BN", positions="2B", fppg=4.0),
        ]
        for row in roster:
            row["future_games"] = [{"date": "2026-05-15", "source": "legacy"}]

        plan = sandlot_win_week.build_plan(
            snapshot_row(roster=roster, free_agents=[]),
            now=NOW,
        )

        self.assertFalse(any(action["kind"] == "lineup" for action in plan["actions"]))
        deadline_monitor = next(
            item for item in plan["monitoring_actions"]
            if item["id"].endswith(":deadline")
        )
        self.assertEqual(deadline_monitor["state"], "needs_refresh")
        self.assertIsNone(deadline_monitor["deadline"]["at"])


if __name__ == "__main__":
    unittest.main()
