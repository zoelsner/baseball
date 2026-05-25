import unittest
from datetime import datetime

import fantrax_data  # noqa: F401 - applies fantraxapi monkey patches
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


if __name__ == "__main__":
    unittest.main()
