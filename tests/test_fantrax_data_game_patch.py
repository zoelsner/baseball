import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import fantrax_data
from fantraxapi.objs.game import Game


class DummyLeague:
    start_date = datetime(2026, 3, 1)
    end_date = datetime(2026, 11, 1)


class DummyPlayer:
    team_short_name = "NYY"


class FantraxGamePatchTests(unittest.TestCase):
    def test_missing_second_content_part_does_not_raise(self):
        game = Game(DummyLeague(), DummyPlayer(), "Sun 05/24", {
            "eventId": "g1",
            "content": "DET",
        })

        self.assertEqual(game.opponent, "DET")
        self.assertIsNone(game.time)
        self.assertFalse(game.home)

    def test_future_game_time_can_have_space_before_ampm(self):
        game = Game(DummyLeague(), DummyPlayer(), "Sun 05/24", {
            "eventId": "g2",
            "content": "@DET<br/>7:05 PM ET",
        })

        self.assertEqual(game.opponent, "DET")
        self.assertEqual(game.time.hour, 19)
        self.assertEqual(game.time.minute, 5)
        self.assertTrue(game.home)

    def test_completed_score_line_uses_opponent_team(self):
        game = Game(DummyLeague(), DummyPlayer(), "Sun 05/24", {
            "eventId": "g3",
            "content": "NYY 4<br/>BOS 2",
        })

        self.assertEqual(game.opponent, "BOS")
        self.assertIsNone(game.time)
        self.assertTrue(game.home)

    def test_current_matchup_carries_latest_completed_result(self):
        today = datetime.now(timezone.utc).date()
        me = SimpleNamespace(id="me", name="My Team")
        current_opponent = SimpleNamespace(id="current-opp", name="Current Opponent")
        prior_opponent = SimpleNamespace(id="prior-opp", name="Prior Opponent")
        current = SimpleNamespace(
            period=SimpleNamespace(number=5),
            name="Period 5",
            start=today - timedelta(days=1),
            end=today + timedelta(days=5),
            days=7,
            complete=False,
            current=True,
            matchups=[SimpleNamespace(
                away=me,
                home=current_opponent,
                away_score=4,
                home_score=3,
                matchup_key="current",
            )],
        )
        completed = SimpleNamespace(
            period=SimpleNamespace(number=4),
            name="Period 4",
            start=today - timedelta(days=8),
            end=today - timedelta(days=2),
            days=7,
            complete=False,
            current=False,
            matchups=[SimpleNamespace(
                away=prior_opponent,
                home=me,
                away_score=10,
                home_score=12,
                matchup_key="prior",
            )],
        )
        api = Mock()
        api.scoring_period_results.return_value = {4: completed, 5: current}

        result = fantrax_data.extract_matchup(api, "me")

        self.assertEqual(result["period_number"], 5)
        self.assertFalse(result["complete"])
        self.assertEqual(result["latest_completed"]["period_number"], 4)
        self.assertTrue(result["latest_completed"]["complete"])
        self.assertEqual(result["latest_completed"]["my_score"], 12)
        self.assertEqual(result["latest_completed"]["opponent_score"], 10)

    def test_returns_future_editable_matchup_separately_with_proven_zero_scores(self):
        today = datetime.now(timezone.utc).date()
        me = SimpleNamespace(id="me", name="My Team")
        current_opp = SimpleNamespace(id="current-opp", name="Current Opponent")
        future_opp = SimpleNamespace(id="future-opp", name="Future Opponent")
        current = SimpleNamespace(
            period=SimpleNamespace(number=5), name="Period 5",
            start=today - timedelta(days=1), end=today + timedelta(days=1),
            days=3, complete=False, current=True,
            matchups=[SimpleNamespace(away=me, home=current_opp, away_score=4, home_score=3, matchup_key="current")],
        )
        future = SimpleNamespace(
            period=SimpleNamespace(number=6), name="Period 6",
            start=today + timedelta(days=2), end=today + timedelta(days=8),
            days=7, complete=False, current=False,
            matchups=[SimpleNamespace(away=me, home=future_opp, away_score=0, home_score=0, matchup_key="future")],
        )
        api = Mock()
        api.scoring_period_results.return_value = {5: current, "6": future}

        contexts = fantrax_data.extract_matchup_contexts(api, "me", editable_period_number=6)

        self.assertEqual(contexts["matchup"]["period_number"], 5)
        self.assertEqual(contexts["editable_matchup"]["period_number"], 6)
        self.assertEqual(contexts["editable_matchup"]["score_state"], "not_started")
        self.assertEqual(contexts["editable_matchup"]["my_score"], 0)
        self.assertEqual(contexts["editable_matchup"]["opponent_score"], 0)

    def test_off_week_does_not_relabel_future_editable_matchup_as_current(self):
        today = datetime.now(timezone.utc).date()
        me = SimpleNamespace(id="me", name="My Team")
        opponent = SimpleNamespace(id="opp", name="Opponent")
        future = SimpleNamespace(
            period=SimpleNamespace(number=6), name="Period 6",
            start=today + timedelta(days=2), end=today + timedelta(days=8),
            days=7, complete=False, current=True,
            matchups=[SimpleNamespace(away=me, home=opponent, away_score=None, home_score=None, matchup_key="future")],
        )
        api = Mock()
        api.scoring_period_results.return_value = {6: future}

        contexts = fantrax_data.extract_matchup_contexts(api, "me", editable_period_number="6")

        self.assertIsNone(contexts["matchup"])
        self.assertEqual(contexts["editable_matchup"]["period_number"], 6)


if __name__ == "__main__":
    unittest.main()
