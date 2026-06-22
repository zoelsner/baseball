"""Unit tests for the GET /api/attention queue (issue #64).

The roster/recommendation fixtures here intentionally mirror
tests/playwright/specs/today-attention.spec.ts so the Python port and the
frontend's v2AttentionQueue stay pinned to the same expected output.
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi import HTTPException

import sandlot_attention
from sandlot_api import attention_queue, latest_hot_swaps

# The /api/actions request contract from PR #63 (sandlot_api.ActionRequest +
# sandlot_actions.SUPPORTED_ACTIONS). Hardcoded here because the executor
# branch isn't merged yet; if these drift, update both sides deliberately.
ACTION_REQUEST_FIELDS = {"action", "player_id", "to_slot", "confirm_player_name", "move_out_player_id"}
SUPPORTED_ACTIONS = {"move_to_il", "add_free_agent", "drop_player", "change_slot"}


def today_page_roster():
    """Same roster as the Playwright spec's baseSnapshot()."""
    return [
        {"id": "judge", "name": "Aaron Judge", "positions": "OF", "team": "NYY", "slot": "OF", "fppg": 6.2, "injury": "DTD"},
        {"id": "webb", "name": "Logan Webb", "positions": "SP", "team": "SF", "slot": "SP", "fppg": 0},
        {"id": "corner", "name": "Cold Corner", "positions": "1B", "team": "SEA", "slot": "UT", "fppg": 0.8},
    ]


def today_page_recommendations(chain=None):
    """Same top recommendation as the Playwright spec's baseSnapshot()."""
    chain = chain or [
        {"player_id": "bench-bat", "player_name": "Bench Bat", "from_slot": "BN", "to_slot": "UT"},
        {"player_id": "corner", "player_name": "Cold Corner", "from_slot": "UT", "to_slot": "BN"},
    ]
    return {
        "recommendations": [
            {
                "points_delta": 2.4,
                "confidence": "high",
                "reason_chips": ["bench upgrade"],
                "action": {"chain": chain},
                "replacement_card": {
                    "type": "lineup_hot_swap",
                    "proposal": {
                        "id": "lineup-swap:corner:bench-bat:UT",
                        "type": "lineup_swap",
                        "status": "blocked",
                        "writes_enabled": False,
                        "confirmation_required": True,
                        "summary": "Move Cold Corner out and Bench Bat in.",
                        "safety_checks": [
                            {"key": "trusted_slots", "label": "Trusted slot data", "state": "passed"},
                            {"key": "lineup_only", "label": "Lineup-only move", "state": "passed"},
                            {"key": "protected_players", "label": "Protected players excluded", "state": "passed"},
                            {"key": "executor_ready", "label": "Execution safety", "state": "blocked"},
                        ],
                    },
                    "move_in": {
                        "id": "bench-bat",
                        "name": "Bench Bat",
                        "team": "LAD",
                        "positions": "1B",
                        "from_slot": "BN",
                        "to_slot": "UT",
                        "fppg": 4.2,
                        "remaining_games": 2,
                        "slot_source": "raw.statusId",
                    },
                    "move_out": {
                        "id": "corner",
                        "name": "Cold Corner",
                        "team": "SEA",
                        "positions": "1B",
                        "from_slot": "UT",
                        "to_slot": "BN",
                        "fppg": 0.8,
                        "remaining_games": 1,
                        "slot_source": "raw.lineupSlot",
                    },
                    "projected_benefit": {"points": 2.4, "win_probability_delta": 0.02},
                    "reason": "Move Bench Bat into UT and Cold Corner to BN because the lineup-only simulation sees bench upgrade.",
                    "short_term_outlook": "Bench Bat has 2 remaining games at 4.2 FP/G; Cold Corner has 1 remaining game at 0.8 FP/G.",
                    "risk": "Medium risk: this is a lineup-only projection.",
                    "confidence": "high",
                    "risk_label": "medium",
                    "provenance": {
                        "source": "latest Fantrax snapshot",
                        "slot_provenance": "trusted",
                        "move_in_slot_source": "raw.statusId",
                        "move_out_slot_source": "raw.lineupSlot",
                    },
                    "safety": {"lineup_only": True, "add_drop": False, "live_writes": False},
                    "execution": {
                        "state": "blocked",
                        "label": "Propose swap",
                        "reason": "Lineup execution is disabled until safety is ready.",
                    },
                    "blocked_reason": "Propose swap is disabled until execution safety is ready.",
                },
            }
        ],
    }


def row_with_slot_source(row):
    out = dict(row)
    if "slot_source" not in out:
        slot = str(out.get("slot") or "").upper()
        out["slot_source"] = "raw.statusId" if slot in sandlot_attention.RESERVED_SLOTS else "raw.lineupSlot"
    if "future_games" not in out:
        out["future_games"] = [{"date": "2026-06-23"}]
    return out


def snapshot_data(roster, *, include_matchup=True):
    data = {"roster": {"rows": [row_with_slot_source(row) for row in roster]}}
    if include_matchup:
        data["matchup"] = {
            "my_score": 1.0,
            "opponent_score": 1.0,
            "opponent_team_id": "opp",
            "end": "2026-06-29",
        }
        data["all_team_rosters"] = {
            "opp": {
                "rows": [
                    row_with_slot_source({
                        "id": "opp-ss",
                        "name": "Opponent Shortstop",
                        "positions": "SS",
                        "slot": "SS",
                        "fppg": 1.0,
                    })
                ]
            }
        }
    return data


def queue_for(roster, recommendations=None):
    return sandlot_attention.attention_items(snapshot_data(roster), recommendations=recommendations)


def replacement_for(chain):
    items = queue_for(today_page_roster(), today_page_recommendations(chain))
    return next(item for item in items if item["kind"] == "replacement")


class AttentionQueueOrderingTests(unittest.TestCase):
    def test_mirrors_today_page_fixture(self):
        items = queue_for(today_page_roster(), today_page_recommendations())

        self.assertEqual([i["kind"] for i in items], ["status", "lineup", "output", "replacement"])
        self.assertEqual([i["severity"] for i in items], ["urgent", "check", "review", "review"])
        self.assertEqual([i["title"] for i in items[:3]], ["Aaron Judge", "Logan Webb", "Cold Corner"])
        self.assertEqual(items[3]["title"], "Lineup hot swap")

        # Exact copy the Playwright spec asserts on the rendered page.
        self.assertEqual(items[0]["reason"], "Day-to-day on OF. Inspect replacement risk before lock.")
        self.assertEqual(items[1]["reason"], "No projected output. Confirm the active slot before leaving this player in.")
        self.assertEqual(items[2]["reason"], "Low FP/G for active slot. Check whether this active spot needs a replacement.")
        self.assertEqual(
            items[3]["reason"],
            "Move Bench Bat into UT and Cold Corner to BN because the lineup-only simulation sees bench upgrade.",
        )
        self.assertEqual(items[3]["context"], "Bench Bat for Cold Corner")

    def test_priority_values_match_frontend_formula(self):
        items = queue_for(today_page_roster(), today_page_recommendations())

        self.assertAlmostEqual(items[0]["priority"], 306.2)  # status 300 + 6.2 FP/G
        self.assertAlmostEqual(items[1]["priority"], 200.0)  # lineup 200 + 0
        self.assertAlmostEqual(items[2]["priority"], 100.8)  # output 100 + 0.8
        self.assertAlmostEqual(items[3]["priority"], 52.4)   # replacement 50 + 2.4
        self.assertEqual(items, sorted(items, key=lambda i: i["priority"], reverse=True))

    def test_caps_at_six_items(self):
        roster = [
            {"id": f"p{i}", "name": f"Player {i}", "slot": "OF", "fppg": 5 + i, "injury": "DTD"}
            for i in range(7)
        ]

        items = queue_for(roster)

        self.assertEqual(len(items), 6)
        # Highest-metric injured players win the cap.
        self.assertEqual(items[0]["player_id"], "p6")
        self.assertNotIn("p0", [i["player_id"] for i in items])

    def test_high_metric_cold_starter_is_not_cold(self):
        # Cutoff = max(1, median * 0.55): with starters at 5.0/4.0/1.0 the
        # cutoff is 2.2, so only the 1.0 FP/G starter flags as low output.
        roster = [
            {"id": "a", "name": "A", "slot": "OF", "fppg": 5.0},
            {"id": "b", "name": "B", "slot": "1B", "fppg": 4.0},
            {"id": "c", "name": "C", "slot": "UT", "fppg": 1.0},
        ]

        items = queue_for(roster)

        self.assertEqual([i["player_id"] for i in items], ["c"])
        self.assertEqual(items[0]["kind"], "output")

    def test_bench_and_il_players_are_excluded(self):
        roster = [
            {"id": "bench", "name": "Bench Guy", "slot": "BN", "fppg": 0, "injury": "DTD"},
            {"id": "stash", "name": "IL Stash", "slot": "IL", "fppg": 0, "injury": "OUT"},
            {"id": "ok", "name": "Healthy Starter", "slot": "OF", "fppg": 5.0},
        ]

        self.assertEqual(queue_for(roster), [])

    def test_minors_and_reserve_players_are_excluded(self):
        roster = [
            {"id": "min", "name": "Protected Prospect", "slot": "MIN", "positions": "1B", "fppg": 0},
            {"id": "res", "name": "Reserve Stash", "slot": "RES", "positions": "OF", "fppg": 0, "injury": "DTD"},
            {"id": "ok", "name": "Healthy Starter", "slot": "OF", "fppg": 5.0},
        ]

        self.assertEqual(queue_for(roster), [])

    def test_empty_state_is_empty_list(self):
        roster = [
            {"id": "healthy-a", "name": "Healthy Bat", "slot": "OF", "fppg": 5.8},
            {"id": "healthy-b", "name": "Healthy Arm", "slot": "SP", "fppg": 4.4},
            {"id": "healthy-c", "name": "Healthy Corner", "slot": "1B", "fppg": 3.9},
        ]

        self.assertEqual(queue_for(roster, {"recommendations": []}), [])
        self.assertEqual(queue_for([], None), [])

    def test_chips_are_deduped_and_capped_at_three(self):
        items = queue_for(today_page_roster(), today_page_recommendations())

        # Status text appears once even though the injury row repeats it.
        self.assertEqual(items[0]["chips"], ["Day-to-day", "6.2 FP/G"])
        for item in items:
            self.assertLessEqual(len(item["chips"]), 3)
            self.assertEqual(len(item["chips"]), len(set(item["chips"])))


class AttentionActionPayloadTests(unittest.TestCase):
    def assert_valid_action_payload(self, payload):
        self.assertIn(payload["action"], SUPPORTED_ACTIONS)
        self.assertTrue(payload["player_id"])
        self.assertTrue(set(payload).issubset(ACTION_REQUEST_FIELDS))

    def test_injured_starter_gets_move_to_il_action(self):
        items = queue_for(today_page_roster())

        status_item = items[0]
        self.assertEqual(status_item["action"], {"action": "move_to_il", "player_id": "judge"})
        self.assertEqual(status_item["actions"], [status_item["action"]])
        self.assert_valid_action_payload(status_item["action"])

    def test_suspended_starter_gets_no_action(self):
        # The #63 executor refuses IL moves for suspensions, so the queue
        # must not propose one — the item still surfaces for a human read.
        roster = [{"id": "susp", "name": "Suspended Guy", "slot": "OF", "fppg": 4.0, "injury": "SUSP"}]

        items = queue_for(roster)

        self.assertEqual(items[0]["kind"], "status")
        self.assertIsNone(items[0]["action"])
        self.assertEqual(items[0]["actions"], [])

    def test_lineup_and_output_items_carry_no_action(self):
        items = queue_for(today_page_roster())

        for item in items:
            if item["kind"] in ("lineup", "output"):
                self.assertIsNone(item["action"])
                self.assertEqual(item["actions"], [])

    def test_replacement_item_is_blocked_non_executable_proposal(self):
        chain = [
            {"player_id": "bench-bat", "player_name": "Bench Bat", "from_slot": "BN", "to_slot": "UT"},
            {"player_id": "corner", "player_name": "Cold Corner", "from_slot": "UT", "to_slot": "BN"},
        ]

        replacement = replacement_for(chain)

        self.assertIsNone(replacement["action"])
        self.assertEqual(replacement["actions"], [])
        self.assertEqual(replacement["blocked_action"]["state"], "blocked")
        self.assertEqual(replacement["blocked_action"]["label"], "Propose swap")
        self.assertEqual(replacement["proposal"]["id"], "lineup-swap:corner:bench-bat:UT")
        self.assertEqual(replacement["proposal"]["status"], "blocked")
        self.assertFalse(replacement["proposal"]["writes_enabled"])
        self.assertEqual(
            [check["state"] for check in replacement["proposal"]["safety_checks"]],
            ["passed", "passed", "passed", "blocked"],
        )
        self.assertEqual(replacement["replacement"]["move_in"]["name"], "Bench Bat")
        self.assertEqual(replacement["replacement"]["move_out"]["name"], "Cold Corner")
        self.assertFalse(replacement["replacement"]["safety"]["live_writes"])


class AttentionRecommendationGatingTests(unittest.TestCase):
    def test_no_matchup_block_skips_recommendation_compute(self):
        with mock.patch.object(sandlot_attention.sandlot_matchup, "rank_matchup_improvement_actions") as ranked:
            items = sandlot_attention.attention_items(snapshot_data(today_page_roster(), include_matchup=False))

        ranked.assert_not_called()
        self.assertEqual([i["kind"] for i in items], ["status"])
        self.assertEqual(items[0]["actions"], [])

    def test_matchup_block_uses_ranked_recommendations(self):
        data = snapshot_data(today_page_roster())
        with mock.patch.object(
            sandlot_attention.sandlot_matchup,
            "rank_matchup_improvement_actions",
            return_value=today_page_recommendations(),
        ) as ranked:
            items = sandlot_attention.attention_items(data)

        ranked.assert_called_once()
        self.assertEqual(items[-1]["kind"], "replacement")

    def test_untrusted_slot_provenance_suppresses_attention_swap_guidance(self):
        data = snapshot_data([
            {
                "id": "cold",
                "name": "Cold Active",
                "slot": "OF",
                "slot_source": "position_fallback",
                "positions": "OF",
                "fppg": 0.8,
            },
            {
                "id": "bench",
                "name": "Bench Upgrade",
                "slot": "RES",
                "slot_source": "raw.statusId",
                "positions": "OF",
                "fppg": 4.0,
            },
        ])
        data["matchup"] = {"my_score": 1.0}
        with mock.patch.object(
            sandlot_attention.sandlot_matchup,
            "rank_matchup_improvement_actions",
            return_value=today_page_recommendations(),
        ) as ranked:
            items = sandlot_attention.attention_items(data)

        ranked.assert_not_called()
        self.assertEqual(items, [])

    def test_partial_action_readiness_suppresses_lineup_health_and_actions_even_when_slots_trusted(self):
        data = snapshot_data(today_page_roster())
        for row in data["roster"]["rows"]:
            row.pop("future_games", None)
        with mock.patch.object(
            sandlot_attention.sandlot_matchup,
            "rank_matchup_improvement_actions",
            return_value=today_page_recommendations(),
        ) as ranked:
            items = sandlot_attention.attention_items(data)

        ranked.assert_not_called()
        self.assertEqual([item["kind"] for item in items], ["status"])
        self.assertIsNone(items[0]["action"])
        self.assertEqual(items[0]["actions"], [])

    def test_injected_recommendations_do_not_bypass_readiness_gate(self):
        data = snapshot_data(today_page_roster())
        for row in data["roster"]["rows"]:
            row.pop("future_games", None)

        items = sandlot_attention.attention_items(data, recommendations=today_page_recommendations())

        self.assertNotIn("replacement", [item["kind"] for item in items])

    def test_untrusted_slot_provenance_suppresses_status_action_payload(self):
        data = snapshot_data([
            {
                "id": "judge",
                "name": "Aaron Judge",
                "slot": "OF",
                "slot_source": "position_fallback",
                "positions": "OF",
                "fppg": 6.2,
                "injury": "DTD",
            }
        ])

        items = sandlot_attention.attention_items(data)

        self.assertEqual([item["kind"] for item in items], ["status"])
        self.assertIsNone(items[0]["action"])
        self.assertEqual(items[0]["actions"], [])


class AttentionStatusChangeTests(unittest.TestCase):
    def test_status_change_item_for_new_risk(self):
        previous = snapshot_data([
            {"id": "judge", "name": "Aaron Judge", "slot": "OF", "positions": "OF", "fppg": 6.2},
        ])
        current = snapshot_data([
            {"id": "judge", "name": "Aaron Judge", "slot": "OF", "positions": "OF", "fppg": 6.2, "injury": "DTD"},
        ])

        changes = sandlot_attention.status_change_items(current, previous)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["kind"], "change")
        self.assertEqual(changes[0]["severity"], "urgent")
        self.assertEqual(changes[0]["player_id"], "judge")
        self.assertEqual(changes[0]["changes"], [
            {"field": "status", "from": "Active", "to": "Day-to-day"},
            {"field": "state", "from": "Active", "to": "Injured"},
        ])
        self.assertEqual(changes[0]["reason"], "status Active -> Day-to-day; state Active -> Injured")

    def test_slot_and_state_transition_item(self):
        previous = snapshot_data([
            {"id": "woodruff", "name": "Brandon Woodruff", "slot": "SP", "positions": "SP", "fppg": 3.0},
        ])
        current = snapshot_data([
            {"id": "woodruff", "name": "Brandon Woodruff", "slot": "IR", "positions": "SP", "fppg": 3.0},
        ])

        changes = sandlot_attention.status_change_items(current, previous)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["changes"], [
            {"field": "slot", "from": "SP", "to": "IR"},
            {"field": "state", "from": "Active", "to": "Injured"},
        ])
        self.assertEqual(changes[0]["chips"], ["Slot", "State"])

    def test_no_previous_snapshot_has_no_changes(self):
        current = snapshot_data(today_page_roster())

        self.assertEqual(sandlot_attention.status_change_items(current, None), [])


class AttentionRouteTests(unittest.TestCase):
    def test_503_when_database_unavailable(self):
        with mock.patch("sandlot_db.latest_successful_snapshot", side_effect=RuntimeError("no db")):
            with self.assertRaises(HTTPException) as ctx:
                attention_queue()

        self.assertEqual(ctx.exception.status_code, 503)

    def test_503_when_no_successful_snapshot(self):
        with mock.patch("sandlot_db.latest_successful_snapshot", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                attention_queue()

        self.assertEqual(ctx.exception.status_code, 503)

    def test_payload_shape(self):
        row = {
            "id": 42,
            "taken_at": datetime.now(timezone.utc),
            "data": snapshot_data(today_page_roster()),
        }
        previous = {
            "id": 41,
            "taken_at": datetime.now(timezone.utc),
            "data": snapshot_data([
                {"id": "judge", "name": "Aaron Judge", "positions": "OF", "team": "NYY", "slot": "OF", "fppg": 6.2},
                {"id": "webb", "name": "Logan Webb", "positions": "SP", "team": "SF", "slot": "SP", "fppg": 0},
                {"id": "corner", "name": "Cold Corner", "positions": "1B", "team": "SEA", "slot": "UT", "fppg": 0.8},
            ]),
        }
        with mock.patch("sandlot_db.latest_successful_snapshot", return_value=row), \
            mock.patch("sandlot_db.previous_successful_snapshot", return_value=previous):
            payload = attention_queue()

        self.assertEqual(payload["snapshot_id"], 42)
        self.assertEqual(payload["previous_snapshot_id"], 41)
        self.assertEqual(payload["freshness"]["state"], "fresh")
        self.assertEqual([i["kind"] for i in payload["items"]], ["status", "lineup", "output"])
        self.assertEqual([c["player_id"] for c in payload["changes"]], ["judge"])


class HotSwapRouteTests(unittest.TestCase):
    def test_latest_hot_swaps_returns_read_only_proposals(self):
        row = {
            "id": 42,
            "taken_at": datetime.now(timezone.utc),
            "data": snapshot_data(today_page_roster()),
        }
        with mock.patch("sandlot_db.latest_successful_snapshot", return_value=row), \
            mock.patch.object(
                sandlot_attention.sandlot_matchup,
                "rank_matchup_improvement_actions",
                return_value=today_page_recommendations(),
            ):
            payload = latest_hot_swaps()

        self.assertEqual(payload["snapshot_id"], 42)
        self.assertEqual(payload["state"], "ready")
        self.assertFalse(payload["writes_enabled"])
        self.assertIsNone(payload["paused_reason"])
        self.assertEqual(len(payload["proposals"]), 1)
        proposal = payload["proposals"][0]["proposal"]
        self.assertEqual(proposal["id"], "lineup-swap:corner:bench-bat:UT")
        self.assertEqual(proposal["status"], "blocked")
        self.assertFalse(proposal["writes_enabled"])
        self.assertEqual(payload["proposals"][0]["blocked_action"]["state"], "blocked")
        self.assertEqual(payload["proposals"][0]["source_item"]["kind"], "replacement")

    def test_latest_hot_swaps_pauses_when_slot_provenance_is_untrusted(self):
        data = snapshot_data(today_page_roster())
        for row in data["roster"]["rows"]:
            if row.get("slot") not in sandlot_attention.RESERVED_SLOTS:
                row["slot_source"] = "position_fallback"
        row = {
            "id": 42,
            "taken_at": datetime.now(timezone.utc),
            "data": data,
        }
        with mock.patch("sandlot_db.latest_successful_snapshot", return_value=row), \
            mock.patch.object(sandlot_attention.sandlot_matchup, "rank_matchup_improvement_actions") as ranked:
            payload = latest_hot_swaps()

        ranked.assert_not_called()
        self.assertEqual(payload["state"], "paused")
        self.assertFalse(payload["writes_enabled"])
        self.assertEqual(payload["proposals"], [])
        self.assertIn("Lineup-slot source trusted", payload["paused_reason"])
        self.assertFalse(payload["data_quality"]["lineup_recommendations_ready"])


if __name__ == "__main__":
    unittest.main()
