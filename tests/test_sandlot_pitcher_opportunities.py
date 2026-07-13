import json
import unittest
from datetime import datetime, timezone

import sandlot_pitcher_opportunities as opportunities


def future_game(day, *, probable=False):
    game = {"date": f"2026-07-{day:02d}", "game_pk": day, "source": "mlb_schedule"}
    if probable:
        game["probable_start"] = True
    return game


def pitcher(player_id, name, *, slot="SP", team="DET", posted=0):
    return {
        "id": player_id,
        "name": name,
        "team": team,
        "slot": slot,
        "positions": slot,
        "fppg": 12.0,
        "future_games_status": "ok" if posted else "pitcher_probables_unavailable",
        "future_games_scope": "pitcher_probable_starts",
        "future_games": [future_game(18, probable=True)] if posted else [],
        "team_future_games": [future_game(day) for day in range(17, 27)],
    }


class PitcherOpportunityTests(unittest.TestCase):
    def test_verified_gs_cadence_is_fractional_frozen_and_sp_only(self):
        starter = pitcher("starter", "Verified Starter", posted=1)
        reliever = pitcher("reliever", "Closer", slot="RP")
        bench = pitcher("bench", "Bench Starter", slot="RES")
        snapshot = {
            "matchup": {"opponent_team_id": "opp", "end": "2026-07-26"},
            "roster": {"rows": [starter, reliever, bench]},
            "all_team_rosters": {"opp": {"rows": []}},
        }
        games = [
            {"date": "2026-06-20", "team": "DET", "gs": True},
            {"date": "2026-06-26", "team": "DET", "gs": True},
            {"date": "2026-07-02", "team": "DET", "gs": True},
            {"date": "2026-07-08", "team": "DET", "gs": True},
            # Same-day final-looking rows stay outside both cadence windows
            # because game logs do not carry an authoritative completion time.
            {"date": "2026-07-13", "team": "DET", "gs": True},
        ]
        team_count_calls = []

        def team_counts(start, end, **_kwargs):
            team_count_calls.append((start.isoformat(), end.isoformat()))
            return {"DET": 24}

        enriched = opportunities.enrich_snapshot_pitcher_opportunities(
            snapshot,
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
            identity_resolver=lambda row, _season: {
                "status": "resolved_name_team", "mlb_id": 669373, "source": "fixture"
            },
            game_log_loader=lambda _mlb_id, _season: (
                games,
                {"state": "fresh", "fetched_at": datetime(2026, 7, 13, tzinfo=timezone.utc)},
            ),
            team_count_fetcher=team_counts,
            workers=1,
        )

        rows = {row["id"]: row for row in enriched["roster"]["rows"]}
        estimate = rows["starter"]["pitcher_opportunity_estimate"]
        self.assertEqual(estimate["state"], "estimated")
        self.assertEqual(estimate["starts_recent"], 4)
        self.assertEqual(estimate["team_games_recent"], 24)
        self.assertEqual(estimate["future_team_games"], 10)
        self.assertAlmostEqual(estimate["uncapped_expected_starts"], 4 / 24 * 10, places=4)
        self.assertAlmostEqual(estimate["expected_starts"], 4 / 24 * 10, places=4)
        self.assertEqual(estimate["posted_probable_starts"], 1)
        self.assertFalse(estimate["action_eligible"])
        self.assertFalse(estimate["probability_release_eligible"])
        self.assertEqual(estimate["history_window"]["end_exclusive"], "2026-07-13")
        self.assertEqual(rows["reliever"]["pitcher_opportunity_estimate"]["state"], "unmodeled")
        self.assertNotIn("pitcher_opportunity_estimate", rows["bench"])
        diagnostics = enriched["pitcher_opportunity_provenance"]
        self.assertEqual(diagnostics["active_pitchers"], 2)
        self.assertEqual(diagnostics["cadence_estimated_starters"], 1)
        self.assertEqual(diagnostics["unmodeled_relievers"], 1)
        self.assertEqual(team_count_calls, [("2026-06-13", "2026-07-12")])
        json.dumps(enriched)

    def test_editable_matchup_owns_both_window_and_opponent(self):
        current_sp = pitcher("current", "Current Opponent", team="DET")
        future_sp = pitcher("future", "Future Opponent", team="CHC")
        snapshot = {
            "matchup": {"opponent_team_id": "current-opp", "start": "2026-07-06", "end": "2026-07-12"},
            "editable_matchup": {"opponent_team_id": "future-opp", "start": "2026-07-13", "end": "2026-07-26"},
            "roster": {"rows": []},
            "all_team_rosters": {
                "current-opp": {"rows": [current_sp]},
                "future-opp": {"rows": [future_sp]},
            },
        }
        games = [
            {"date": "2026-06-25", "team": "CHC", "gs": True},
            {"date": "2026-07-01", "team": "CHC", "gs": True},
            {"date": "2026-07-07", "team": "CHC", "gs": True},
        ]

        enriched = opportunities.enrich_snapshot_pitcher_opportunities(
            snapshot,
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
            identity_resolver=lambda *_args: {"status": "resolved_name_team", "mlb_id": 1},
            game_log_loader=lambda *_args: (games, {"state": "fresh"}),
            team_count_fetcher=lambda *_args, **_kwargs: {"CHC": 24},
            workers=1,
        )

        self.assertNotIn(
            "pitcher_opportunity_estimate",
            enriched["all_team_rosters"]["current-opp"]["rows"][0],
        )
        future = enriched["all_team_rosters"]["future-opp"]["rows"][0]
        self.assertEqual(future["pitcher_opportunity_estimate"]["state"], "estimated")
        self.assertEqual(future["pitcher_opportunity_estimate"]["period_window"]["end"], "2026-07-26")

    def test_identity_and_recency_fail_closed(self):
        unresolved = pitcher("unresolved", "Namesake")
        stale = pitcher("stale", "Stale Starter")
        snapshot = {
            "matchup": {"opponent_team_id": "opp", "end": "2026-07-26"},
            "roster": {"rows": [unresolved, stale]},
            "all_team_rosters": {"opp": {"rows": []}},
        }

        def resolve(row, _season):
            if row["id"] == "unresolved":
                return {"status": "team_mismatch", "mlb_id": None, "source": "fixture"}
            return {"status": "resolved_name_team", "mlb_id": 123, "source": "fixture"}

        enriched = opportunities.enrich_snapshot_pitcher_opportunities(
            snapshot,
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
            identity_resolver=resolve,
            game_log_loader=lambda *_args: (
                [
                    {"date": "2026-06-15", "team": "DET", "gs": True},
                    {"date": "2026-06-20", "team": "DET", "gs": True},
                ],
                {"state": "fresh"},
            ),
            team_count_fetcher=lambda *_args, **_kwargs: {"DET": 24},
            workers=1,
        )

        rows = {row["id"]: row for row in enriched["roster"]["rows"]}
        self.assertEqual(rows["unresolved"]["pitcher_opportunity_estimate"]["state"], "unmodeled")
        self.assertIn("team_mismatch", rows["unresolved"]["pitcher_opportunity_estimate"]["reason"])
        self.assertEqual(rows["stale"]["pitcher_opportunity_estimate"]["state"], "unmodeled")
        self.assertEqual(rows["stale"]["pitcher_opportunity_estimate"]["reason"], "latest verified start is stale")

    def test_source_failure_preserves_snapshot_and_records_partial_evidence(self):
        starter = pitcher("starter", "Starter")
        snapshot = {
            "matchup": {"opponent_team_id": "opp", "end": "2026-07-26"},
            "roster": {"rows": [starter]},
            "all_team_rosters": {"opp": {"rows": []}},
        }

        enriched = opportunities.enrich_snapshot_pitcher_opportunities(
            snapshot,
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
            identity_resolver=lambda *_args: {"status": "resolved_name_team", "mlb_id": 1},
            game_log_loader=lambda *_args: ([], {"state": "error", "error": "timeout"}),
            team_count_fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")),
            workers=1,
        )

        row = enriched["roster"]["rows"][0]
        self.assertEqual(row["future_games"], [])
        self.assertEqual(row["pitcher_opportunity_estimate"]["state"], "unmodeled")
        self.assertEqual(enriched["pitcher_opportunity_provenance"]["state"], "partial")
        self.assertIn("team_game_counts", enriched["pitcher_opportunity_provenance"]["errors"][0])


if __name__ == "__main__":
    unittest.main()
