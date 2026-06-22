import unittest
from types import SimpleNamespace

import fantrax_data
from fantraxapi.objs import RosterRow


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


class FakeApi:
    def __init__(self, roster):
        self._roster = roster

    def team_roster(self, _team_id):
        return self._roster


class FakeRosterInfoApi:
    def __init__(self, roster, raw):
        self._roster = roster
        self._raw = raw
        self.raw_request = None

    def _request(self, method, **kwargs):
        self.raw_request = (method, kwargs)
        return self._raw

    def roster_info(self, _team_id):
        return self._roster


class FakePosition:
    def __init__(self, short_name, name=None):
        self.short_name = short_name
        self.name = name or short_name


class FakeRowApi:
    positions = {
        "OF": FakePosition("OF", "Outfield"),
        "UT": FakePosition("UT", "Utility"),
    }


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

    def test_external_trusted_slot_override_upgrades_position_fallback(self):
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
            _data={"miscData": {"statusTotals": []}, "tables": []},
        )

        data = fantrax_data.extract_roster(
            FakeApi(roster),
            "team",
            slot_overrides={
                "starter": {"slot": "UT", "slot_source": "dom.lineup-btn", "text": "UT"},
            },
        )

        self.assertEqual(data["rows"][0]["slot"], "UT")
        self.assertEqual(data["rows"][0]["slot_full"], "UT")
        self.assertEqual(data["rows"][0]["slot_source"], "dom.lineup-btn")

    def test_external_slot_override_preserves_already_trusted_reserved_slot(self):
        roster_data = {
            "rows": [
                {"id": "reserve", "name": "Reserve Arm", "slot": "RES", "slot_source": "raw.statusId"},
                {"id": "active", "name": "Active Bat", "slot": "OF", "slot_source": "position_fallback"},
                {"id": "conflict", "name": "Conflict Bat", "slot": "OF", "slot_source": "position_fallback"},
            ]
        }

        updated = fantrax_data.apply_trusted_slot_overrides(
            roster_data,
            {
                "reserve": {"slot": "OF", "slot_source": "dom.lineup-btn"},
                "active": {"slot": "UT", "slot_source": "dom.lineup-btn"},
                "conflict": {
                    "slot": "SS",
                    "slot_source": "dom.lineup-btn",
                    "conflicts": [{"slot": "UT", "text": "UT"}],
                },
            },
        )

        self.assertEqual(updated["rows"][0]["slot"], "RES")
        self.assertEqual(updated["rows"][0]["slot_source"], "raw.statusId")
        self.assertEqual(updated["rows"][1]["slot"], "UT")
        self.assertEqual(updated["rows"][1]["slot_source"], "dom.lineup-btn")
        self.assertEqual(updated["rows"][2]["slot"], "OF")
        self.assertEqual(updated["rows"][2]["slot_source"], "position_fallback")
        self.assertEqual(roster_data["rows"][1]["slot"], "OF")

    def test_roster_info_compat_preserves_raw_slots_and_current_field_names(self):
        player = obj(
            id="compat-player",
            name="Compat Player",
            team_short_name="ATL",
            team_name="Atlanta",
            pos_short_name="OF",
            all_positions=[obj(short_name="OF"), obj(short_name="UT")],
            injured=True,
            suspended=False,
        )
        row = obj(
            player=player,
            pos=obj(short_name="OF", name="Outfield"),
            fppg=3.5,
        )
        roster = obj(
            rows=[row],
            active=0,
            active_max=0,
            reserve=1,
            reserve_max=1,
            injured=0,
            injured_max=0,
            period_number=1,
            period_date="2026-06-18",
        )
        raw = {
            "miscData": {
                "statusTotals": [
                    {"id": "1", "name": "Active", "total": 0, "max": 22},
                    {"id": "2", "name": "Reserve", "total": 1, "max": 7},
                    {"id": "3", "name": "Injured", "total": 0, "max": 3},
                ],
            },
            "tables": [
                {
                    "rows": [
                        {
                            "posId": "OF",
                            "statusId": "2",
                            "scorer": {"scorerId": "compat-player"},
                        }
                    ]
                }
            ],
        }
        api = FakeRosterInfoApi(roster, raw)

        data = fantrax_data.extract_roster(api, "team")

        self.assertEqual(api.raw_request, ("getTeamRosterInfo", {"teamId": "team"}))
        self.assertEqual(getattr(roster, "_data"), raw)
        self.assertEqual(data["rows"][0]["slot"], "RES")
        self.assertEqual(data["rows"][0]["slot_source"], "raw.statusId")
        self.assertEqual(data["rows"][0]["fppg"], 3.5)
        self.assertEqual(data["rows"][0]["injury"], "INJ")
        self.assertEqual(data["active"], 0)
        self.assertEqual(data["active_max"], 22)
        self.assertEqual(data["reserve"], 1)
        self.assertEqual(data["reserve_max"], 7)
        self.assertEqual(data["injured"], 0)
        self.assertEqual(data["injured_max"], 3)

    def test_current_roster_row_patch_tolerates_missing_game_time_parts(self):
        row = RosterRow(FakeRowApi(), {
            "statusId": "1",
            "posId": "OF",
            "scorer": {
                "scorerId": "player-1",
                "name": "Future Row",
                "shortName": "Future Row",
                "teamName": "Atlanta",
                "teamShortName": "ATL",
                "posShortNames": "OF",
                "posIdsNoFlex": ["OF"],
                "posIds": ["OF", "UT"],
            },
            "cells": [
                {"content": ""},
                {"content": "DET"},
                {"content": ""},
                {"content": "3.5"},
            ],
        })

        self.assertEqual(row.player.id, "player-1")
        self.assertEqual(row.pos.short_name, "OF")
        self.assertEqual(row.opponent, "DET")
        self.assertIsNone(row.time)
        self.assertEqual(row.fppg, 3.5)
        self.assertEqual(row.fantasy_points_per_game, 3.5)

    def test_current_roster_row_patch_parses_spaced_future_game_time(self):
        row = RosterRow(FakeRowApi(), {
            "statusId": "1",
            "posId": "OF",
            "scorer": {
                "scorerId": "player-2",
                "name": "Timed Row",
                "shortName": "Timed Row",
                "teamName": "Atlanta",
                "teamShortName": "ATL",
                "posShortNames": "OF",
                "posIdsNoFlex": ["OF"],
                "posIds": ["OF", "UT"],
            },
            "cells": [
                {"content": ""},
                {"content": "@DET<br/>7:05 PM ET"},
                {"content": ""},
                {"content": "4.25"},
            ],
        })

        self.assertEqual(row.opponent, "DET")
        self.assertEqual(row.time.hour, 19)
        self.assertEqual(row.time.minute, 5)
        self.assertEqual(row.fppg, 4.25)


if __name__ == "__main__":
    unittest.main()
