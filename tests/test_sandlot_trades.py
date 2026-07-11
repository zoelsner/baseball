import json
import unittest
from unittest.mock import patch

import sandlot_trades


def future_game(day=14):
    return {"date": f"2026-05-{day:02d}"}


def player(pid, name, *, slot, positions, fppg, team="T", age=28):
    return {
        "id": pid,
        "name": name,
        "slot": slot,
        "positions": positions,
        "team": team,
        "fppg": fppg,
        "age": age,
        "future_games": [future_game()],
    }


def trade_snapshot():
    my_rows = [
        player("m1", "My Second Baseman", slot="2B", positions="2B", fppg=2.0, team="ME", age=25),
        player("m2", "My Shortstop", slot="SS", positions="SS", fppg=5.0, team="ME", age=27),
        player("m3", "My Outfielder", slot="OF", positions="OF", fppg=6.0, team="ME", age=29),
    ]
    opp_rows = [
        player("o1", "Their Outfielder", slot="OF", positions="OF", fppg=1.5, team="OPP", age=30),
        player("o2", "Their Second Baseman", slot="2B", positions="2B", fppg=3.0, team="OPP", age=25),
        player("o3", "Their Shortstop", slot="SS", positions="SS", fppg=1.0, team="OPP", age=31),
        player("o4", "Their Reliever", slot="RP", positions="RP", fppg=0.9, team="OPP", age=26),
    ]
    return {
        "id": 321,
        "data": {
            "team_id": "me",
            "matchup": {
                "my_score": 10,
                "opponent_score": 8,
                "opponent_team_id": "opp",
                "end": "2026-05-20",
            },
            "roster": {"rows": my_rows},
            "all_team_rosters": {
                "me": {"is_me": True, "rows": my_rows},
                "opp": {"team_id": "opp", "team_name": "Opponent", "rows": opp_rows},
            },
        },
    }


class TradeCounterTests(unittest.TestCase):
    def test_grade_offer_returns_three_honest_counter_bands(self):
        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ), patch.object(sandlot_trades, "_overlay_counter_rationales"):
            result = sandlot_trades.grade_offer(trade_snapshot(), ["m1"], ["o1"])

        self.assertEqual(result["grade"], result["letter_grade"])
        self.assertEqual(result["my_weakest_position"], "2B")
        self.assertIsNone(result["no_counter_reason"])
        self.assertEqual([c["tier"] for c in result["counters"]], ["strong", "balanced", "light"])
        self.assertEqual([c["acceptance_band"] for c in result["counters"]], ["hard", "balanced", "easy"])
        self.assertNotIn("accept_pct", json.dumps(result))
        self.assertNotIn("%", json.dumps(result["counters"]))

    def test_offer_already_favoring_me_returns_no_counter_reason(self):
        snapshot = trade_snapshot()
        snapshot["data"]["all_team_rosters"]["opp"]["rows"][0]["fppg"] = 2.5

        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ):
            result = sandlot_trades.grade_offer(snapshot, ["m1"], ["o1"])

        self.assertEqual(result["counters"], [])
        self.assertEqual(result["my_delta"], 0.5)
        self.assertIn("already favors you", result["no_counter_reason"])

    def test_suspended_players_are_excluded_from_counter_candidates(self):
        snapshot = trade_snapshot()
        suspended = snapshot["data"]["all_team_rosters"]["opp"]["rows"][2]
        suspended.update({"fppg": 4.0, "injury": "SUSP"})

        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ), patch.object(sandlot_trades, "_overlay_counter_rationales"):
            result = sandlot_trades.grade_offer(snapshot, ["m1"], ["o1"])

        added_ids = {counter["added_player"]["id"] for counter in result["counters"]}
        self.assertNotIn("o3", added_ids)

    def test_raw_suspended_flag_is_unavailable_for_trade_candidates(self):
        self.assertTrue(sandlot_trades._is_unavailable({
            "raw": {"player": {"suspended": True}},
        }))
        self.assertFalse(sandlot_trades._is_unavailable({
            "raw": {"player": {"suspended": "false"}},
        }))

    def test_counter_bands_target_fair_packages_instead_of_biggest_star(self):
        candidates = [
            {"row": {"id": "star", "name": "Star"}, "counter_delta": 18.0, "score": 20.0},
            {"row": {"id": "strong", "name": "Strong"}, "counter_delta": 1.5, "score": 3.0},
            {"row": {"id": "balanced", "name": "Balanced"}, "counter_delta": 0.5, "score": 2.0},
            {"row": {"id": "light", "name": "Light"}, "counter_delta": 0.0, "score": 1.0},
        ]

        picked = sandlot_trades._pick_counter_tiers(candidates)

        self.assertEqual(
            [(tier, candidate["row"]["id"]) for tier, candidate in picked],
            [("strong", "strong"), ("balanced", "balanced"), ("light", "light")],
        )

    def test_incomplete_data_pauses_counters_but_keeps_grade(self):
        snapshot = trade_snapshot()
        snapshot["data"]["roster"]["rows"][0].pop("future_games")

        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ):
            result = sandlot_trades.grade_offer(snapshot, ["m1"], ["o1"])

        self.assertEqual(result["my_delta"], -0.5)
        self.assertEqual(result["counters"], [])
        self.assertIn("Counter guidance paused", result["no_counter_reason"])

    def test_counter_rationale_cache_uses_hashed_counter_subject(self):
        counters = [{
            "tier": "strong",
            "counter_strength": "strong",
            "acceptance_band": "hard",
            "give": [{"id": "m1", "name": "My Second Baseman"}],
            "get": [{"id": "o1", "name": "Their Outfielder"}, {"id": "o2", "name": "Their Second Baseman"}],
            "my_delta": 2.5,
            "rationale": "Deterministic rationale.",
        }]
        client = type("Client", (), {
            "complete": lambda self, *args, **kwargs: (
                '[{"tier":"strong","rationale":"Adds 2B help without fake odds."}]',
                "test-model",
            )
        })

        with patch.object(sandlot_trades.sandlot_db, "get_ai_brief", return_value=None), patch.object(
            sandlot_trades.sandlot_db, "set_ai_brief"
        ) as set_brief, patch.object(sandlot_trades.sandlot_skipper, "SkipperClient", return_value=client()):
            sandlot_trades._overlay_counter_rationales(
                snapshot_id=321,
                give_ids=["m1"],
                get_ids=["o1"],
                counters=counters,
                team_id="opp",
            )

        args = set_brief.call_args.args
        self.assertEqual(args[0], 321)
        self.assertEqual(args[1], sandlot_trades.BRIEF_TYPE_COUNTER)
        self.assertEqual(len(args[2]), 64)
        self.assertEqual(counters[0]["rationale"], "Adds 2B help without fake odds.")


class TradeValidationTests(unittest.TestCase):
    def assert_grade_error(self, snapshot, give_ids, get_ids, expected_text):
        with self.assertRaises(sandlot_trades.TradeGradeError) as raised:
            sandlot_trades.grade_offer(snapshot, give_ids, get_ids)
        self.assertIn(expected_text, str(raised.exception))

    def test_duplicate_ids_are_rejected_on_either_side(self):
        cases = (
            (["m1", "m1"], ["o1"], "duplicate player id(s) on give side"),
            (["m1"], ["o1", "o1"], "duplicate player id(s) on get side"),
        )
        for give_ids, get_ids, expected in cases:
            with self.subTest(expected=expected):
                self.assert_grade_error(trade_snapshot(), give_ids, get_ids, expected)

    def test_player_id_cannot_appear_on_both_sides(self):
        self.assert_grade_error(
            trade_snapshot(),
            ["m1"],
            ["m1"],
            "cannot appear on both sides",
        )

    def test_give_player_must_be_on_canonical_my_roster(self):
        self.assert_grade_error(
            trade_snapshot(),
            ["o1"],
            ["o2"],
            "not on my canonical roster",
        )

    def test_get_player_cannot_be_mine_free_agent_or_unknown(self):
        cases = []

        own_snapshot = trade_snapshot()
        cases.append((own_snapshot, "m2", "already on my roster"))

        free_agent_snapshot = trade_snapshot()
        free_agent_snapshot["data"]["free_agents"] = {
            "players": [
                player("fa1", "Free Agent", slot="FA", positions="OF", fppg=4.0),
            ]
        }
        cases.append((free_agent_snapshot, "fa1", "is a free agent"))

        cases.append((trade_snapshot(), "unknown", "not on an opponent roster"))

        for snapshot, get_id, expected in cases:
            with self.subTest(get_id=get_id):
                self.assert_grade_error(snapshot, ["m1"], [get_id], expected)

    def test_get_players_must_come_from_one_opponent_roster(self):
        snapshot = trade_snapshot()
        snapshot["data"]["all_team_rosters"]["opp2"] = {
            "team_id": "opp2",
            "team_name": "Second Opponent",
            "rows": [
                player("o5", "Second Opponent Catcher", slot="C", positions="C", fppg=2.5),
            ],
        }

        self.assert_grade_error(
            snapshot,
            ["m1"],
            ["o1", "o5"],
            "must all come from one opponent roster",
        )

    def test_get_players_from_same_opponent_can_be_graded(self):
        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ):
            result = sandlot_trades.grade_offer(
                trade_snapshot(),
                ["m1"],
                ["o1", "o2"],
            )

        self.assertEqual([row["id"] for row in result["my_get"]], ["o1", "o2"])

    def test_ambiguous_opponent_ownership_is_rejected(self):
        snapshot = trade_snapshot()
        snapshot["data"]["all_team_rosters"]["opp2"] = {
            "team_id": "opp2",
            "team_name": "Second Opponent",
            "rows": [
                player("o1", "Duplicated Player", slot="OF", positions="OF", fppg=1.5),
            ],
        }

        self.assert_grade_error(
            snapshot,
            ["m1"],
            ["o1"],
            "appears on multiple opponent rosters",
        )

    def test_minors_and_explicit_protection_flags_fail_closed(self):
        mutators = {
            "min-slot": lambda row: row.update({"slot": "MIN"}),
            "row-keeper": lambda row: row.update({"keeper_protected": True}),
            "raw-protected": lambda row: row.update({"raw": {"protected": "yes"}}),
            "raw-player-keeper": lambda row: row.update({"raw": {"player": {"is_keeper": "1"}}}),
            "raw-scorer-minor": lambda row: row.update({"raw": {"scorer": {"minor_league": True}}}),
        }
        for case, mutate in mutators.items():
            with self.subTest(case=case):
                snapshot = trade_snapshot()
                mutate(snapshot["data"]["roster"]["rows"][0])
                self.assert_grade_error(snapshot, ["m1"], ["o1"], "is protected")

        protected_get = trade_snapshot()
        protected_get["data"]["all_team_rosters"]["opp"]["rows"][0]["slot"] = "MINORS"
        self.assert_grade_error(protected_get, ["m1"], ["o1"], "is protected")

    def test_missing_or_invalid_selected_fppg_is_rejected(self):
        cases = (
            ("give-missing", "give", None),
            ("get-missing", "get", None),
            ("get-invalid", "get", "N/A"),
            ("get-nan", "get", float("nan")),
            ("get-infinite", "get", float("inf")),
        )
        for case, side, value in cases:
            with self.subTest(case=case):
                snapshot = trade_snapshot()
                if side == "give":
                    snapshot["data"]["roster"]["rows"][0]["fppg"] = value
                else:
                    snapshot["data"]["all_team_rosters"]["opp"]["rows"][0]["fppg"] = value
                self.assert_grade_error(
                    snapshot,
                    ["m1"],
                    ["o1"],
                    "missing a valid FP/G value",
                )

    def test_explicit_zero_fppg_is_not_treated_as_missing(self):
        snapshot = trade_snapshot()
        snapshot["data"]["roster"]["rows"][0]["fppg"] = 0.0
        snapshot["data"]["all_team_rosters"]["opp"]["rows"][0]["fppg"] = 0.0

        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ), patch.object(sandlot_trades, "_overlay_counter_rationales"):
            result = sandlot_trades.grade_offer(snapshot, ["m1"], ["o1"])

        self.assertEqual(result["my_give_fppg"], 0.0)
        self.assertEqual(result["my_get_fppg"], 0.0)

    def test_missing_or_implausible_age_is_rejected(self):
        cases = (None, "N/A", float("nan"), 15, 51)
        for value in cases:
            with self.subTest(age=value):
                snapshot = trade_snapshot()
                snapshot["data"]["all_team_rosters"]["opp"]["rows"][0]["age"] = value
                self.assert_grade_error(
                    snapshot,
                    ["m1"],
                    ["o1"],
                    "missing a valid age for dynasty grading",
                )

    def test_raw_age_without_provenance_is_rejected(self):
        snapshot = trade_snapshot()
        row = snapshot["data"]["all_team_rosters"]["opp"]["rows"][0]
        row["raw"] = {}
        row.pop("age_source", None)

        self.assert_grade_error(
            snapshot,
            ["m1"],
            ["o1"],
            "missing a valid age for dynasty grading",
        )

    def test_young_players_require_manual_dynasty_review_on_either_side(self):
        for side in ("give", "get"):
            with self.subTest(side=side):
                snapshot = trade_snapshot()
                rows = (
                    snapshot["data"]["roster"]["rows"]
                    if side == "give"
                    else snapshot["data"]["all_team_rosters"]["opp"]["rows"]
                )
                rows[0]["age"] = 24
                self.assert_grade_error(
                    snapshot,
                    ["m1"],
                    ["o1"],
                    "requires manual dynasty review",
                )

    def test_result_discloses_rate_only_scope_without_weekly_claims(self):
        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ), patch.object(sandlot_trades, "_overlay_counter_rationales"):
            result = sandlot_trades.grade_offer(trade_snapshot(), ["m1"], ["o1"])

        self.assertEqual(result["grade_scope"], "current_rate_only")
        self.assertEqual(result["value_basis"], "current_snapshot_fppg")
        self.assertEqual(result["time_horizon"], "per_game_rate_only")
        self.assertFalse(result["dynasty_complete"])
        self.assertNotIn("weekly", result["headline"].lower())
        self.assertNotIn("take it", result["headline"].lower())

    def test_fallback_rationale_uses_current_snapshot_rate_language(self):
        text = sandlot_trades._fallback_rationale({
            "my_give": [{"name": "Give Player"}],
            "my_get": [{"name": "Get Player"}],
            "my_delta": 1.25,
        })

        self.assertIn("+1.25 FP/G from the current snapshot", text)
        self.assertNotIn("weekly", text.lower())


if __name__ == "__main__":
    unittest.main()
