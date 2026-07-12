import unittest
from unittest.mock import patch

import fantrax_data


class DummyTeam:
    def __init__(self, name):
        self.name = name


class DummyAPI:
    team_lookup = {
        "mine": DummyTeam("My Team"),
        "other": DummyTeam("Other Team"),
        "third": DummyTeam("Third Team"),
    }

    def pending_trades(self):
        raise KeyError("Accepted")

    def team(self, team_id):
        return self.team_lookup[team_id]


class FantraxPendingTradeTests(unittest.TestCase):
    def test_object_parser_retains_exact_player_id(self):
        class Ref:
            pass
        mine, other, player, move, trade = Ref(), Ref(), Ref(), Ref(), Ref()
        mine.id, mine.name = "mine", "My Team"
        other.id, other.name = "other", "Other Team"
        player.id, player.name = "p1", "Useful Player"
        move.from_team, move.to_team, move.player = mine, other, player
        trade.proposed_by, trade.moves, trade.trade_id = other, [move], "tx-object"
        trade.proposed, trade.accepted, trade.executed = None, None, None
        api = DummyAPI()
        api.pending_trades = lambda: [trade]

        trades = fantrax_data.extract_pending_trades(api, "mine")

        self.assertEqual(trades[0]["moves"][0]["player_id"], "p1")
        self.assertIsNone(trades[0]["accepted"])

    def test_raw_fallback_allows_missing_accepted_timestamp(self):
        raw = {
            "tradeInfoList": [
                {
                    "txSetId": "tx1",
                    "creatorTeamId": "other",
                    "usefulInfo": [
                        {"name": "Proposed", "value": "May 26, 3:00 PM EDT"},
                        {"name": "To be executed", "value": "May 27, 3:00 PM EDT"},
                    ],
                    "moves": [
                        {
                            "from": {"teamId": "mine"},
                            "to": {"teamId": "other"},
                            "scorer": {"scorerId": "p1", "name": "Useful Player"},
                        }
                    ],
                }
            ]
        }
        with patch.object(fantrax_data._fantrax_api, "get_pending_transactions", return_value=raw):
            trades = fantrax_data.extract_pending_trades(DummyAPI(), "mine")

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["trade_id"], "tx1")
        self.assertIsNone(trades[0]["accepted"])
        self.assertEqual(trades[0]["executed"], "May 27, 3:00 PM EDT")
        self.assertEqual(trades[0]["moves"][0]["player"], "Useful Player")
        self.assertEqual(trades[0]["moves"][0]["from_team"], "My Team")

    def test_raw_fallback_filters_unrelated_trades(self):
        raw = {
            "tradeInfoList": [
                {
                    "txSetId": "tx2",
                    "creatorTeamId": "other",
                    "usefulInfo": [{"name": "Proposed", "value": "May 26, 3:00 PM EDT"}],
                    "moves": [
                        {
                            "from": {"teamId": "other"},
                            "to": {"teamId": "third"},
                            "scorer": {"scorerId": "p2", "name": "Other Player"},
                        }
                    ],
                }
            ]
        }
        with patch.object(fantrax_data._fantrax_api, "get_pending_transactions", return_value=raw):
            trades = fantrax_data.extract_pending_trades(DummyAPI(), "mine")

        self.assertEqual(trades, [])


if __name__ == "__main__":
    unittest.main()
