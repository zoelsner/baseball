import unittest
from datetime import datetime, timezone

import sandlot_future_games


def game(day, *, probable=None):
    out = {
        "date": f"2026-06-{day:02d}",
        "gameDate": f"2026-06-{day:02d}T23:05:00Z",
        "game_pk": day,
        "opponent": "BOS",
        "home": True,
        "source": "mlb_schedule",
    }
    if probable:
        out["probable_pitcher"] = probable
    return out


class SandlotFutureGamesTests(unittest.TestCase):
    def test_editable_matchup_owns_schedule_enrichment_window(self):
        fetch_calls = []

        def fetcher(_team_id, start, end, **_kwargs):
            fetch_calls.append((start.isoformat(), end.isoformat()))
            return []

        snapshot = {
            "matchup": {"period_number": 16, "start": "2026-07-06", "end": "2026-07-12"},
            "editable_matchup": {"period_number": 17, "start": "2026-07-13", "end": "2026-07-26"},
            "roster": {"rows": [{"id": "judge", "name": "Aaron Judge", "team": "NYY", "positions": "OF"}]},
        }

        sandlot_future_games.enrich_snapshot_future_games(
            snapshot,
            now=datetime(2026, 7, 11, 12, tzinfo=timezone.utc),
            schedule_fetcher=fetcher,
            team_resolver=lambda *_args: 147,
        )

        self.assertEqual(fetch_calls, [("2026-07-13", "2026-07-26")])

    def test_enriches_hitters_and_pitchers_with_separate_countable_games(self):
        fetch_calls = []

        def resolver(abbr, _season):
            return {"NYY": 147, "BOS": 111}.get(abbr)

        def fetcher(team_id, start, end, *, season=None, now=None):
            fetch_calls.append((team_id, start.isoformat(), end.isoformat()))
            if team_id == 147:
                return [
                    game(23, probable={"id": 592450, "name": "Gerrit Cole"}),
                    game(24),
                ]
            if team_id == 111:
                return [game(25)]
            return []

        snapshot = {
            "matchup": {"start": "2026-06-22", "end": "2026-06-28", "opponent_team_id": "opp"},
            "roster": {
                "rows": [
                    {"id": "hitter", "name": "Aaron Judge", "team": "NYY", "slot": "OF", "positions": "OF"},
                    {
                        "id": "cole",
                        "name": "Gerrit Cole",
                        "mlb_id": 592450,
                        "team": "NYY",
                        "slot": "SP",
                        "positions": "SP",
                    },
                    {"id": "other-sp", "name": "Other Starter", "team": "NYY", "slot": "SP", "positions": "SP"},
                    {"id": "unknown", "name": "Mystery Bat", "team": "XXX", "slot": "OF", "positions": "OF"},
                ],
            },
            "all_team_rosters": {
                "opp": {
                    "rows": [
                        {"id": "opp", "name": "Opp Bat", "team": "BOS", "slot": "SS", "positions": "SS"},
                    ],
                },
            },
        }

        enriched = sandlot_future_games.enrich_snapshot_future_games(
            snapshot,
            now=datetime(2026, 6, 22, 12, tzinfo=timezone.utc),
            schedule_fetcher=fetcher,
            team_resolver=resolver,
        )

        rows = {row["id"]: row for row in enriched["roster"]["rows"]}
        self.assertEqual(rows["hitter"]["future_games_scope"], "team_games")
        self.assertEqual(rows["hitter"]["future_games_status"], "ok")
        self.assertEqual(len(rows["hitter"]["future_games"]), 2)

        self.assertEqual(rows["cole"]["future_games_scope"], "pitcher_probable_starts")
        self.assertEqual(rows["cole"]["future_games_status"], "ok")
        self.assertEqual(len(rows["cole"]["future_games"]), 1)
        self.assertTrue(rows["cole"]["future_games"][0]["probable_start"])
        self.assertEqual(len(rows["cole"]["team_future_games"]), 2)

        self.assertEqual(rows["other-sp"]["future_games"], [])
        self.assertEqual(rows["other-sp"]["future_games_status"], "pitcher_probables_unavailable")
        self.assertEqual(len(rows["other-sp"]["team_future_games"]), 2)

        self.assertEqual(rows["unknown"]["future_games"], [])
        self.assertEqual(rows["unknown"]["future_games_status"], "unresolved_team")

        opp = enriched["all_team_rosters"]["opp"]["rows"][0]
        self.assertEqual(opp["future_games_status"], "ok")
        self.assertEqual(len(opp["future_games"]), 1)

        diagnostics = enriched["future_games_provenance"]
        self.assertEqual(diagnostics["schedule_fetch_count"], 2)
        self.assertEqual(diagnostics["unmapped_team_abbrs"], {"XXX": 1})
        self.assertEqual(diagnostics["status_counts"]["ok"], 3)
        self.assertEqual(diagnostics["status_counts"]["pitcher_probables_unavailable"], 1)
        self.assertEqual(diagnostics["status_counts"]["unresolved_team"], 1)
        self.assertEqual(sorted(fetch_calls), [(111, "2026-06-22", "2026-06-28"), (147, "2026-06-22", "2026-06-28")])

    def test_mapped_empty_schedule_is_ok_for_hitter_off_day(self):
        snapshot = {
            "matchup": {"start": "2026-06-22", "end": "2026-06-28"},
            "roster": {"rows": [{"id": "hitter", "name": "Off Day", "team": "NYY", "slot": "OF", "positions": "OF"}]},
        }

        enriched = sandlot_future_games.enrich_snapshot_future_games(
            snapshot,
            now=datetime(2026, 6, 22, 12, tzinfo=timezone.utc),
            schedule_fetcher=lambda *_args, **_kwargs: [],
            team_resolver=lambda *_args: 147,
        )

        row = enriched["roster"]["rows"][0]
        self.assertEqual(row["future_games_status"], "ok")
        self.assertEqual(row["future_games"], [])
        self.assertEqual(row["future_games_count"], 0)

    def test_enriches_free_agents_through_the_shared_team_schedule_cache(self):
        fetch_calls = []

        def fetcher(team_id, start, end, *, season=None, now=None):
            fetch_calls.append(team_id)
            return [game(23), game(24)]

        snapshot = {
            "matchup": {"start": "2026-06-22", "end": "2026-06-28"},
            "roster": {
                "rows": [{
                    "id": "rostered-nyy",
                    "name": "Rostered Hitter",
                    "team": "NYY",
                    "slot": "OF",
                    "positions": "OF",
                }],
            },
            "free_agents": {
                "method": "getPlayerStats",
                "players": [
                    {
                        "id": "free-agent-nyy",
                        "name": "Free Agent Hitter",
                        "team": "NYY",
                        "positions": "2B",
                        "stats": {"FP/G": 4.0},
                    },
                    {
                        "id": "free-agent-unknown",
                        "name": "Unknown Team Hitter",
                        "team": "XXX",
                        "positions": "OF",
                        "stats": {"FP/G": 3.0},
                    },
                ],
            },
        }

        enriched = sandlot_future_games.enrich_snapshot_future_games(
            snapshot,
            now=datetime(2026, 6, 22, 12, tzinfo=timezone.utc),
            schedule_fetcher=fetcher,
            team_resolver=lambda abbr, _season: 147 if abbr == "NYY" else None,
        )

        free_agents = {row["id"]: row for row in enriched["free_agents"]["players"]}
        self.assertEqual(enriched["free_agents"]["method"], "getPlayerStats")
        self.assertEqual(free_agents["free-agent-nyy"]["future_games_status"], "ok")
        self.assertEqual(free_agents["free-agent-nyy"]["future_games_scope"], "team_games")
        self.assertEqual(free_agents["free-agent-nyy"]["future_games_count"], 2)
        self.assertEqual(free_agents["free-agent-unknown"]["future_games_status"], "unresolved_team")
        self.assertEqual(fetch_calls, [147])
        self.assertEqual(enriched["future_games_provenance"]["schedule_fetch_count"], 1)
        self.assertEqual(enriched["future_games_provenance"]["rows_seen"], 3)

    def test_non_dict_free_agent_rows_are_preserved(self):
        snapshot = {
            "matchup": {"start": "2026-06-22", "end": "2026-06-28"},
            "free_agents": {"players": [None, "bad-row"]},
        }

        enriched = sandlot_future_games.enrich_snapshot_future_games(
            snapshot,
            now=datetime(2026, 6, 22, 12, tzinfo=timezone.utc),
            schedule_fetcher=lambda *_args, **_kwargs: [],
            team_resolver=lambda *_args: 147,
        )

        self.assertEqual(enriched["free_agents"]["players"], [None, "bad-row"])
        self.assertEqual(enriched["future_games_provenance"]["rows_seen"], 0)


if __name__ == "__main__":
    unittest.main()
