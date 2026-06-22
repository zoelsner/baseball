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
from sandlot_api import attention_queue

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
    return {
        "recommendations": [
            {
                "points_delta": 2.4,
                "confidence": "high",
                "reason_chips": ["bench upgrade"],
                "action": {"chain": chain or [{"player_name": "Bench Bat", "from_slot": "BN", "to_slot": "UT"}]},
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
        self.assertEqual(items[3]["title"], "Review lineup move")

        # Exact copy the Playwright spec asserts on the rendered page.
        self.assertEqual(items[0]["reason"], "Day-to-day on OF. Inspect replacement risk before lock.")
        self.assertEqual(items[1]["reason"], "No projected output. Confirm the active slot before leaving this player in.")
        self.assertEqual(items[2]["reason"], "Low FP/G for active slot. Check whether this active spot needs a replacement.")
        self.assertEqual(items[3]["reason"], "Bench Bat BN -> UT. Projected gain +2.4 points.")

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

    def test_single_step_chain_yields_change_slot_action(self):
        chain = [{"player_id": "bench-bat", "player_name": "Bench Bat", "from_slot": "BN", "to_slot": "UT"}]

        replacement = replacement_for(chain)
        self.assertEqual(replacement["action"], {"action": "change_slot", "player_id": "bench-bat", "to_slot": "UT"})
        self.assertEqual(replacement["actions"], [replacement["action"]])
        self.assert_valid_action_payload(replacement["action"])

    def test_multi_step_chain_yields_ordered_actions_list(self):
        chain = [
            {"player_id": "bench-bat", "player_name": "Bench Bat", "from_slot": "BN", "to_slot": "UT"},
            {"player_id": "slumper", "player_name": "Slumper", "from_slot": "UT", "to_slot": "BN"},
        ]

        replacement = replacement_for(chain)
        self.assertIsNone(replacement["action"])  # one payload can't represent two calls
        self.assertEqual(len(replacement["actions"]), 2)
        self.assertEqual([a["player_id"] for a in replacement["actions"]], ["bench-bat", "slumper"])
        for payload in replacement["actions"]:
            self.assert_valid_action_payload(payload)

    def test_chain_with_missing_player_id_yields_no_actions(self):
        # All-or-nothing: a consumer must never be able to execute half a swap.
        chain = [
            {"player_id": "bench-bat", "from_slot": "BN", "to_slot": "UT"},
            {"player_name": "Unknown Id", "from_slot": "UT", "to_slot": "BN"},
        ]

        replacement = replacement_for(chain)

        self.assertIsNone(replacement["action"])
        self.assertEqual(replacement["actions"], [])


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


if __name__ == "__main__":
    unittest.main()
