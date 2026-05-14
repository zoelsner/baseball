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
        player("o2", "Their Second Baseman", slot="2B", positions="2B", fppg=3.0, team="OPP", age=24),
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

    def test_strong_offer_returns_no_counter_reason(self):
        snapshot = trade_snapshot()
        snapshot["data"]["all_team_rosters"]["opp"]["rows"][0]["fppg"] = 5.0

        with patch.object(
            sandlot_trades,
            "_load_or_generate_rationale",
            return_value=("Deterministic grade rationale.", "", False),
        ):
            result = sandlot_trades.grade_offer(snapshot, ["m1"], ["o1"])

        self.assertEqual(result["counters"], [])
        self.assertIn("already grades strong", result["no_counter_reason"])

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


if __name__ == "__main__":
    unittest.main()
