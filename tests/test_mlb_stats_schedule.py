import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import mlb_stats


TEAM_ABBREVS = {
    109: "ARI",
    144: "ATL",
    110: "BAL",
    111: "BOS",
    112: "CHC",
    113: "CIN",
    114: "CLE",
    115: "COL",
    145: "CWS",
    116: "DET",
    117: "HOU",
    118: "KC",
    108: "LAA",
    119: "LAD",
    146: "MIA",
    158: "MIL",
    142: "MIN",
    121: "NYM",
    147: "NYY",
    133: "ATH",
    143: "PHI",
    134: "PIT",
    135: "SD",
    136: "SEA",
    137: "SF",
    138: "STL",
    139: "TB",
    140: "TEX",
    141: "TOR",
    120: "WSH",
}


def schedule_game(game_pk, *, state="Scheduled", game_date="2026-06-23T23:05:00Z", home=147, away=111):
    return {
        "gamePk": game_pk,
        "gameDate": game_date,
        "officialDate": game_date[:10],
        "doubleHeader": "N",
        "status": {"detailedState": state},
        "teams": {
            "home": {
                "team": {"id": home, "abbreviation": TEAM_ABBREVS.get(home, str(home))},
                "probablePitcher": {"id": 592450, "fullName": "Gerrit Cole"},
            },
            "away": {
                "team": {"id": away, "abbreviation": TEAM_ABBREVS.get(away, str(away))},
            },
        },
    }


class MlbStatsScheduleTests(unittest.TestCase):
    def test_resolves_all_teams_and_known_fantrax_aliases(self):
        with patch.object(mlb_stats, "_get_team_abbreviations", return_value=TEAM_ABBREVS):
            expected = {
                "ARI": 109,
                "ATL": 144,
                "BAL": 110,
                "BOS": 111,
                "CHC": 112,
                "CIN": 113,
                "CLE": 114,
                "COL": 115,
                "CHW": 145,
                "CWS": 145,
                "CHA": 145,
                "CHN": 112,
                "DET": 116,
                "HOU": 117,
                "KC": 118,
                "KCR": 118,
                "LAA": 108,
                "ANA": 108,
                "LAD": 119,
                "MIA": 146,
                "FLA": 146,
                "MIL": 158,
                "MIN": 142,
                "NYM": 121,
                "NYN": 121,
                "NYY": 147,
                "NYA": 147,
                "OAK": 133,
                "ATH": 133,
                "PHI": 143,
                "PIT": 134,
                "SD": 135,
                "SDP": 135,
                "SDN": 135,
                "SEA": 136,
                "SF": 137,
                "SFG": 137,
                "SFN": 137,
                "STL": 138,
                "TB": 139,
                "TBR": 139,
                "TEX": 140,
                "TOR": 141,
                "WSH": 120,
                "WAS": 120,
                "WSN": 120,
            }
            for abbr, team_id in expected.items():
                self.assertEqual(mlb_stats.team_id_by_abbreviation(abbr, 2026), team_id, abbr)

            self.assertIsNone(mlb_stats.team_id_by_abbreviation("XXX", 2026))

    def test_normalizes_remaining_games_with_status_and_time_filters(self):
        payload = {
            "dates": [
                {
                    "date": "2026-06-22",
                    "games": [
                        schedule_game(1, game_date="2026-06-22T16:00:00Z"),
                        schedule_game(2, game_date="2026-06-22T23:05:00Z"),
                        schedule_game(3, state="Postponed", game_date="2026-06-23T23:05:00Z"),
                        schedule_game(4, state="Final", game_date="2026-06-24T23:05:00Z"),
                        schedule_game(5, game_date="2026-06-23T18:05:00Z", home=119, away=111),
                    ],
                },
                {
                    "date": "2026-06-24",
                    "games": [
                        schedule_game(6, game_date="2026-06-24T17:05:00Z"),
                        schedule_game(7, game_date="2026-06-24T23:05:00Z"),
                    ],
                },
            ],
        }

        games = mlb_stats.normalize_schedule_games(
            payload,
            team_id=147,
            team_abbrev=TEAM_ABBREVS,
            now=datetime(2026, 6, 22, 18, tzinfo=timezone.utc),
        )

        self.assertEqual([game["game_pk"] for game in games], [2, 6, 7])
        self.assertEqual(games[0]["date"], "2026-06-22")
        self.assertEqual(games[0]["opponent"], "BOS")
        self.assertTrue(games[0]["home"])
        self.assertEqual(games[0]["probable_pitcher"], {"id": 592450, "name": "Gerrit Cole"})

    def test_completed_team_counts_exclude_nonfinal_and_future_and_keep_doubleheaders(self):
        first = schedule_game(10, state="Final", game_date="2026-07-10T17:00:00Z")
        second = schedule_game(11, state="Final", game_date="2026-07-10T23:00:00Z")
        first["doubleHeader"] = "Y"
        second["doubleHeader"] = "Y"
        payload = {"dates": [{"games": [
            first,
            second,
            schedule_game(12, state="Postponed", game_date="2026-07-11T17:00:00Z"),
            schedule_game(13, state="In Progress", game_date="2026-07-11T19:00:00Z"),
            schedule_game(14, state="Final", game_date="2026-07-13T17:00:00Z"),
        ]}]}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        with (
            patch.object(mlb_stats.requests, "get", return_value=Response()),
            patch.object(mlb_stats, "_get_team_abbreviations", return_value=TEAM_ABBREVS),
        ):
            counts = mlb_stats.fetch_completed_team_game_counts(
                "2026-07-01",
                "2026-07-13",
                season=2026,
                now=datetime(2026, 7, 13, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(counts, {"NYY": 2, "BOS": 2})


if __name__ == "__main__":
    unittest.main()
