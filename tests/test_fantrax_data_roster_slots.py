import unittest
from types import SimpleNamespace

import fantrax_data


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


class FakeApi:
    def __init__(self, roster):
        self._roster = roster

    def team_roster(self, _team_id):
        return self._roster


class FantraxRosterSlotTests(unittest.TestCase):
    def test_roster_status_overrides_player_position_for_assigned_slot(self):
        player = obj(
            id="condon",
            name="Charlie Condon",
            team_short_name="COL",
            team_name="Colorado",
            pos_short_name="1B",
            all_positions=[obj(short_name="1B"), obj(short_name="UT")],
            out=False,
            injured_reserve=False,
            suspended=False,
            day_to_day=False,
        )
        row = obj(
            player=player,
            position=obj(short_name="1B", name="First Base"),
            total_fantasy_points=0,
            fantasy_points_per_game=0,
        )
        roster = obj(
            rows=[row],
            active=0,
            active_max=0,
            reserve=0,
            reserve_max=0,
            injured=0,
            injured_max=0,
            period_number=1,
            period_date="2026-06-18",
            _data={
                "miscData": {
                    "statusTotals": [
                        {"id": "1", "name": "Active"},
                        {"id": "6", "name": "Min"},
                    ],
                },
                "tables": [
                    {
                        "rows": [
                            {
                                "posId": "1B",
                                "statusId": "6",
                                "scorer": {"scorerId": "condon"},
                            }
                        ]
                    }
                ],
            },
        )

        data = fantrax_data.extract_roster(FakeApi(roster), "team")

        self.assertEqual(data["rows"][0]["slot"], "MIN")
        self.assertEqual(data["rows"][0]["slot_full"], "MIN")
        self.assertEqual(data["rows"][0]["slot_source"], "raw.statusId")
        self.assertEqual(data["rows"][0]["positions"], "1B")
        self.assertEqual(data["rows"][0]["all_positions"], ["1B", "UT"])

    def test_position_fallback_is_marked_when_no_assigned_slot_exists(self):
        player = obj(
            id="active",
            name="Active Player",
            team_short_name="NYY",
            team_name="New York",
            pos_short_name="OF",
            all_positions=[obj(short_name="OF")],
            out=False,
            injured_reserve=False,
            suspended=False,
            day_to_day=False,
        )
        row = obj(
            player=player,
            position=obj(short_name="OF", name="Outfield"),
            total_fantasy_points=12,
            fantasy_points_per_game=4,
        )
        roster = obj(
            rows=[row],
            active=1,
            active_max=1,
            reserve=0,
            reserve_max=0,
            injured=0,
            injured_max=0,
            period_number=1,
            period_date="2026-06-18",
            _data={"miscData": {"statusTotals": []}, "tables": []},
        )

        data = fantrax_data.extract_roster(FakeApi(roster), "team")

        self.assertEqual(data["rows"][0]["slot"], "OF")
        self.assertEqual(data["rows"][0]["slot_full"], "Outfield")
        self.assertEqual(data["rows"][0]["slot_source"], "position_fallback")

    def test_active_status_label_does_not_replace_lineup_position(self):
        player = obj(
            id="starter",
            name="Starter",
            team_short_name="NYY",
            team_name="New York",
            pos_short_name="SS",
            all_positions=[obj(short_name="SS")],
            out=False,
            injured_reserve=False,
            suspended=False,
            day_to_day=False,
        )
        row = obj(
            player=player,
            position=obj(short_name="SS", name="Shortstop"),
            total_fantasy_points=8,
            fantasy_points_per_game=4,
        )
        roster = obj(
            rows=[row],
            active=1,
            active_max=1,
            reserve=0,
            reserve_max=0,
            injured=0,
            injured_max=0,
            period_number=1,
            period_date="2026-06-18",
            _data={
                "miscData": {"statusTotals": [{"id": "1", "name": "Active"}]},
                "tables": [
                    {
                        "rows": [
                            {
                                "posId": "SS",
                                "statusId": "1",
                                "statusName": "Active",
                                "scorer": {"scorerId": "starter"},
                            }
                        ]
                    }
                ],
            },
        )

        data = fantrax_data.extract_roster(FakeApi(roster), "team")

        self.assertEqual(data["rows"][0]["slot"], "SS")
        self.assertEqual(data["rows"][0]["slot_source"], "position_fallback")


if __name__ == "__main__":
    unittest.main()
