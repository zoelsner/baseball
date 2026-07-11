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


class RawFirstApi:
    def __init__(self, raw_by_team):
        self._raw_by_team = raw_by_team
        self.raw_requests = []
        self.team_lookup = {
            "me": obj(name="Zohann", short="ZOH"),
            "opp": obj(name="Opponent", short="OPP"),
        }

    def _request(self, method, **kwargs):
        self.raw_requests.append((method, kwargs))
        return self._raw_by_team[kwargs["teamId"]]

    def roster_info(self, _team_id):
        raise AttributeError("'Roster' object has no attribute 'positions'")


class FakeResponse:
    status_code = 200
    reason = "OK"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeDirectSession:
    def __init__(self, raw):
        self.raw = raw
        self.requests = []

    def post(self, url, *, params=None, json=None, timeout=None):
        self.requests.append({"url": url, "params": params, "json": json, "timeout": timeout})
        return FakeResponse({"responses": [{"data": self.raw}]})


class DirectRawApi:
    league_id = "league-1"

    def __init__(self, raw):
        self._session = FakeDirectSession(raw)

    def _request(self, method, **kwargs):
        raise RuntimeError(f"{method} unavailable through library")

    def roster_info(self, _team_id):
        raise AttributeError("'Roster' object has no attribute 'positions'")


class BrokenRawHelper:
    @staticmethod
    def get_team_roster_info(_api, **_kwargs):
        raise AttributeError("'Roster' object has no attribute 'positions'")


class FakePosition:
    def __init__(self, short_name, name=None):
        self.short_name = short_name
        self.name = name or short_name


class FakeRowApi:
    positions = {
        "001": FakePosition("C", "Catcher"),
        "002": FakePosition("1B", "First Base"),
        "003": FakePosition("2B", "Second Base"),
        "004": FakePosition("3B", "Third Base"),
        "005": FakePosition("SS", "Shortstop"),
        "012": FakePosition("OF", "Outfield"),
        "014": FakePosition("UT", "Utility"),
        "OF": FakePosition("OF", "Outfield"),
        "UT": FakePosition("UT", "Utility"),
    }


def raw_roster_row(player_id, *, name, team="ATL", pos="OF", status="1", fppg="3.5", fpts="21.0"):
    return {
        "posId": pos,
        "statusId": status,
        "scorer": {
            "scorerId": player_id,
            "name": name,
            "shortName": name,
            "teamName": team,
            "teamShortName": team,
            "posShortNames": pos,
            "posIdsNoFlex": [pos],
            "posIds": [pos, "UT"],
        },
        "cells": [
            {"content": "30"},
            {"content": fpts},
            {"content": fppg},
            {"content": "100"},
        ],
        "futureGames": [{"date": "2026-06-23", "eventId": "evt-1", "opponent": "DET"}],
    }


def raw_roster(*rows):
    return {
        "miscData": {
            "statusTotals": [
                {"id": "1", "name": "Active", "total": 1, "max": 22},
                {"id": "2", "name": "Reserve", "shortName": "Res", "total": 1, "max": 7},
                {"id": "3", "name": "Injured", "shortName": "IR", "total": 0, "max": 3},
            ],
            "periodNumber": 14,
            "periodDate": "2026-06-22",
        },
        "tables": [{"rows": list(rows)}],
    }


class FantraxRosterSlotTests(unittest.TestCase):
    def test_roster_status_overrides_player_position_for_assigned_slot(self):
        player = obj(
            id="condon",
            name="Charlie Condon",
            age=23,
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
        self.assertEqual(data["rows"][0]["age"], 23)
        self.assertEqual(data["rows"][0]["age_source"], "player.age")

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

    def test_active_status_uses_raw_pos_id_as_trusted_lineup_slot(self):
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
        self.assertEqual(data["rows"][0]["slot_source"], "raw.posId")

    def test_active_raw_pos_id_can_differ_from_default_position(self):
        roster = obj(
            rows=[],
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
                                "posId": "003",
                                "statusId": "1",
                                "scorer": {
                                    "scorerId": "brooks",
                                    "name": "Brooks Lee",
                                    "shortName": "B. Lee",
                                    "teamShortName": "MIN",
                                    "defaultPosId": "005",
                                    "posShortNames": "2B,3B,SS",
                                    "posIds": ["003", "014", "004", "005"],
                                    "posIdsNoFlex": ["003", "004", "005"],
                                },
                                "cells": [
                                    {"content": "25"},
                                    {"content": "100.0"},
                                    {"content": "2.5"},
                                ],
                            }
                        ]
                    }
                ],
            },
        )

        api = FakeApi(roster)
        api.positions = FakeRowApi.positions
        data = fantrax_data.extract_roster(api, "team")

        self.assertEqual(data["rows"][0]["slot"], "2B")
        self.assertEqual(data["rows"][0]["slot_full"], "2B")
        self.assertEqual(data["rows"][0]["slot_source"], "raw.posId")
        self.assertEqual(data["rows"][0]["positions"], "2B,3B,SS")

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
                            "scorer": {
                                "scorerId": "compat-player",
                                "name": "Compat Player",
                                "shortName": "Compat Player",
                                "teamName": "Atlanta",
                                "teamShortName": "ATL",
                                "posShortNames": "OF",
                                "posIdsNoFlex": ["OF"],
                                "posIds": ["OF", "UT"],
                                "injuryStatus": "INJ",
                            },
                            "cells": [
                                {"content": "27"},
                                {"content": "9.5"},
                                {"content": "3.5"},
                                {"content": "110"},
                            ],
                        }
                    ]
                }
            ],
        }
        api = FakeRosterInfoApi(roster, raw)

        data = fantrax_data.extract_roster(api, "team")

        self.assertEqual(api.raw_request, ("getTeamRosterInfo", {"teamId": "team"}))
        self.assertEqual(data["rows"][0]["slot"], "RES")
        self.assertEqual(data["rows"][0]["slot_source"], "raw.statusId")
        self.assertEqual(data["rows"][0]["fppg"], 3.5)
        self.assertEqual(data["rows"][0]["fpts"], 9.5)
        self.assertEqual(data["rows"][0]["injury"], "INJ")
        self.assertEqual(data["active"], 0)
        self.assertEqual(data["active_max"], 22)
        self.assertEqual(data["reserve"], 1)
        self.assertEqual(data["reserve_max"], 7)
        self.assertEqual(data["injured"], 0)
        self.assertEqual(data["injured_max"], 3)

    def test_raw_roster_payload_bypasses_broken_upstream_object_parser(self):
        api = RawFirstApi({
            "me": raw_roster(
                raw_roster_row("starter", name="Raw Starter", pos="OF", status="1", fppg="4.25", fpts="42.5"),
                raw_roster_row("bench", name="Raw Bench", pos="SP", status="2", fppg="3.1", fpts="31.0"),
            ),
        })

        data = fantrax_data.extract_roster(api, "me")

        self.assertEqual(api.raw_requests, [("getTeamRosterInfo", {"teamId": "me"})])
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(data["rows"][0]["name"], "Raw Starter")
        self.assertEqual(data["rows"][0]["positions"], "OF")
        self.assertEqual(data["rows"][0]["all_positions"], ["OF", "UT"])
        self.assertEqual(data["rows"][0]["slot"], "OF")
        self.assertEqual(data["rows"][0]["slot_source"], "raw.posId")
        self.assertEqual(data["rows"][0]["fppg"], 4.25)
        self.assertEqual(data["rows"][0]["fpts"], 42.5)
        self.assertEqual(data["rows"][0]["age"], 30)
        self.assertEqual(data["rows"][0]["age_source"], "raw.cells[0]")
        self.assertEqual(data["rows"][0]["future_games"][0]["eventId"], "evt-1")
        self.assertEqual(data["rows"][1]["slot"], "RES")
        self.assertEqual(data["rows"][1]["slot_source"], "raw.statusId")
        self.assertEqual(data["reserve"], 1)
        self.assertEqual(data["reserve_max"], 7)

    def test_raw_roster_age_prefers_explicit_player_field(self):
        row = raw_roster_row("prospect", name="Named Age Prospect", fppg="2.5", fpts="25")
        row["scorer"]["playerAge"] = "23"
        api = RawFirstApi({"me": raw_roster(row)})

        data = fantrax_data.extract_roster(api, "me")

        self.assertEqual(data["rows"][0]["age"], 23)
        self.assertEqual(data["rows"][0]["age_source"], "raw.scorer.playerAge")

    def test_free_agent_normalization_carries_explicit_age_provenance(self):
        player = fantrax_data._normalize_fa_player(
            {
                "scorer": {
                    "scorerId": "fa-1",
                    "name": "Young Free Agent",
                    "playerAge": "22",
                    "posShortNames": "OF",
                },
                "cells": [{"content": "4.5"}],
            },
            ["FP/G"],
        )

        self.assertEqual(player["age"], 22)
        self.assertEqual(player["age_source"], "raw.scorer.playerAge")

    def test_free_agent_table_header_labels_live_age_and_fpg_cells(self):
        payload = {
            "tableHeader": {
                "cells": [
                    {"shortName": "Rk", "key": "rankOv"},
                    {"shortName": "Sta", "key": "status"},
                    {"shortName": "Age", "key": "age"},
                    {"shortName": "FPts", "key": "fpts"},
                    {"shortName": "FP/G", "key": "fptsPerGame"},
                    {"shortName": "Ros"},
                    {"shortName": "+/-"},
                ],
            },
            "scoringCategoryTypes": [
                {"value": "Tracked", "key": "5"},
                {"value": "Standard", "key": "1"},
            ],
        }
        entry = {
            "scorer": {
                "scorerId": "fa-live",
                "name": "Live Shape Free Agent",
                "posShortNames": "2B",
                "teamShortName": "BOS",
            },
            "cells": [
                {"content": "125"},
                {"content": "FA"},
                {"content": "29"},
                {"content": "140.0"},
                {"content": "4.5"},
                {"content": "12%"},
                {"content": "+3%"},
            ],
        }

        stat_keys = fantrax_data._extract_stat_keys(payload)
        player = fantrax_data._normalize_fa_player(entry, stat_keys)

        self.assertEqual(stat_keys, ["Rk", "Sta", "Age", "FPts", "FP/G", "Ros", "+/-"])
        self.assertEqual(player["stats"]["FP/G"], "4.5")
        self.assertEqual(player["age"], 29)
        self.assertEqual(player["age_source"], "stats.Age")

    def test_raw_roster_cell_age_requires_valid_stat_schema_fingerprint(self):
        cases = (
            ("missing-fpts", "", "2.5", "23"),
            ("missing-fppg", "25", "N/A", "23"),
            ("implausible-age", "25", "2.5", "99"),
        )
        for player_id, fpts, fppg, cell_age in cases:
            with self.subTest(player_id=player_id):
                row = raw_roster_row(player_id, name=player_id, fppg=fppg, fpts=fpts)
                row["cells"][0]["content"] = cell_age
                data = fantrax_data.extract_roster(
                    RawFirstApi({"me": raw_roster(row)}),
                    "me",
                )

                self.assertIsNone(data["rows"][0]["age"])
                self.assertIsNone(data["rows"][0]["age_source"])

    def test_raw_request_falls_back_to_api_request_when_helper_parser_breaks(self):
        api = RawFirstApi({
            "me": raw_roster(raw_roster_row("starter", name="Raw Starter", pos="OF", status="1")),
        })
        original_helper = fantrax_data._fantrax_api
        fantrax_data._fantrax_api = BrokenRawHelper()
        try:
            data = fantrax_data.extract_roster(api, "me")
        finally:
            fantrax_data._fantrax_api = original_helper

        self.assertEqual(api.raw_requests, [("getTeamRosterInfo", {"teamId": "me"})])
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["name"], "Raw Starter")

    def test_raw_request_can_call_fxpa_directly_when_library_paths_fail(self):
        api = DirectRawApi(raw_roster(raw_roster_row("starter", name="Raw Starter", pos="OF", status="1")))
        original_helper = fantrax_data._fantrax_api
        fantrax_data._fantrax_api = BrokenRawHelper()
        try:
            data = fantrax_data.extract_roster(api, "me")
        finally:
            fantrax_data._fantrax_api = original_helper

        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["name"], "Raw Starter")
        self.assertEqual(api._session.requests[0]["url"], fantrax_data.FXPA_URL)
        self.assertEqual(api._session.requests[0]["params"], {"leagueId": "league-1"})
        self.assertEqual(api._session.requests[0]["json"]["msgs"][0]["method"], "getTeamRosterInfo")
        self.assertEqual(api._session.requests[0]["json"]["msgs"][0]["data"]["teamId"], "me")

    def test_all_team_rosters_use_raw_first_roster_parser(self):
        api = RawFirstApi({
            "me": raw_roster(raw_roster_row("my-player", name="My Player", pos="OF", status="1")),
            "opp": raw_roster(raw_roster_row("opp-player", name="Opp Player", pos="SS", status="1")),
        })

        data = fantrax_data.extract_all_team_rosters(api, "me")

        self.assertEqual(data["me"]["rows"][0]["name"], "My Player")
        self.assertEqual(data["me"]["is_me"], True)
        self.assertEqual(data["opp"]["rows"][0]["name"], "Opp Player")
        self.assertEqual(data["opp"]["is_me"], False)

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

    def test_raw_roster_normalizes_current_destination_eligibility(self):
        row = raw_roster_row("eligible", name="Eligible Player", pos="003", status="2")
        row["eligibleStatusIds"] = ["1", "2", "3"]
        row["eligiblePosIds"] = ["003", "014", "004", "005"]
        row["actions"] = [{"typeId": "3", "teamId": "me"}, {"typeId": "4"}]
        api = RawFirstApi({"me": raw_roster(row)})
        api.positions = FakeRowApi.positions

        data = fantrax_data.extract_roster(api, "me")

        eligibility = data["rows"][0]["lineup_eligibility"]
        self.assertEqual(eligibility["current_status_id"], "2")
        self.assertEqual(eligibility["current_status"], "RES")
        self.assertEqual(eligibility["current_position"], "2B")
        self.assertEqual(eligibility["eligible_statuses"], ["ACTIVE", "RES", "IR"])
        self.assertEqual(eligibility["eligible_positions"], ["2B", "UT", "3B", "SS"])
        self.assertEqual(eligibility["source"], "fantrax.raw.eligibleStatusIds+eligiblePosIds")
        transaction = data["rows"][0]["transaction_eligibility"]
        self.assertEqual(transaction["action_type_ids"], ["3", "4"])
        self.assertTrue(transaction["drop_available"])
        self.assertTrue(transaction["trade_available"])

    def test_current_roster_row_patch_uses_live_stat_table_columns(self):
        row = RosterRow(FakeRowApi(), {
            "statusId": "1",
            "posId": "OF",
            "scorer": {
                "scorerId": "player-raw-stats",
                "name": "Raw Stats Row",
                "shortName": "Raw Stats Row",
                "teamName": "Atlanta",
                "teamShortName": "ATL",
                "posShortNames": "OF",
                "posIdsNoFlex": ["OF"],
                "posIds": ["OF", "UT"],
            },
            "cells": [
                {"content": "36"},
                {"content": "142"},
                {"content": "1.95"},
                {"content": "285"},
            ],
        })

        self.assertEqual(row.total_fantasy_points, 142.0)
        self.assertEqual(row.fpts, 142.0)
        self.assertEqual(row.fppg, 1.95)
        self.assertEqual(row.fantasy_points_per_game, 1.95)
        self.assertIsNone(row.opponent)
        self.assertIsNone(row.time)

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
