import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import fantrax_data
import sandlot_db


class EvidenceApi:
    league_id = "league"
    positions = {
        "012": SimpleNamespace(short_name="OF"),
        "015": SimpleNamespace(short_name="SP"),
    }

    def __init__(self, current, historical):
        self.current = current
        self.historical = historical
        self.calls = []

    def _request(self, method, **kwargs):
        self.calls.append((method, kwargs))
        return self.current if len(self.calls) == 1 else self.historical


def current_roster():
    return {
        "displayedLists": {
            "seasonOrProjections": [
                {"code": "SEASON_147", "timeframeTypeCode": "BY_SEASON"},
                {"code": "SEASON_147_BY_PERIOD", "timeframeTypeCode": "BY_PERIOD"},
            ]
        }
    }


def player(player_id, name, pos_id, points):
    return {
        "posId": pos_id,
        "statusId": "1",
        "scorer": {"scorerId": player_id, "name": name, "posIds": [pos_id]},
        "cells": [{"content": "0"}, {"content": str(points)}],
    }


def historical_roster():
    return {
        "displayedSelections": {
            "displayedPeriod": "15",
            "displayedScoringPeriod": "15",
            "teamId": "me",
            "displayedSeasonOrProjection": {"code": "SEASON_147_BY_PERIOD"},
            "timeframeTypeCode": "BY_PERIOD",
        },
        "miscData": {"statusTotals": [{"id": "1", "name": "Active"}]},
        "tables": [
            {
                "headers": [{"sortKey": "AGE"}, {"sortKey": "SCORING_CATEGORY_10_FPTS"}],
                "rows": [player("judge", "Aaron Judge", "012", "260.5")],
            },
            {
                "headers": [{"sortKey": "AGE"}, {"sortKey": "SCORING_CATEGORY_20_FPTS"}],
                "rows": [player("gausman", "Kevin Gausman", "015", "4")],
            },
        ],
    }


def completed_matchup():
    return {
        "complete": True,
        "source": "fantrax_schedule",
        "score_state": "live_or_final",
        "my_team_id": "me",
        "my_score": 264.5,
        "period_number": "15",
        "start": "2026-06-29",
        "end": "2026-07-05",
        "matchup_key": "league:15:me:opp",
    }


class CompletedLineupEvidenceTests(unittest.TestCase):
    def evidence(self):
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=historical_roster()):
            return fantrax_data.extract_completed_lineup_evidence(
                EvidenceApi(current_roster(), historical_roster()), "me", completed_matchup()
            )

    def test_exact_period_archive_reconciles_and_hashes_deterministically(self):
        api = EvidenceApi(current_roster(), historical_roster())

        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=historical_roster()) as request:
            first = fantrax_data.extract_completed_lineup_evidence(api, "me", completed_matchup())
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=historical_roster()):
            second = fantrax_data.extract_completed_lineup_evidence(
                EvidenceApi(current_roster(), historical_roster()), "me", completed_matchup()
            )

        self.assertEqual(first, second)
        self.assertEqual(first["active_player_total"], "264.5")
        self.assertEqual(first["active_player_count"], 2)
        self.assertEqual(len(first["evidence_hash"]), 64)
        self.assertEqual(
            request.call_args.args[1], "getTeamRosterInfo"
        )
        self.assertEqual(request.call_args.kwargs, {
            "teamId": "me", "period": "15",
            "seasonOrProjection": "SEASON_147_BY_PERIOD", "timeframeTypeCode": "BY_PERIOD",
        })

    def test_wrong_returned_period_fails_closed(self):
        raw = historical_roster()
        raw["displayedSelections"]["displayedPeriod"] = "16"
        raw["displayedSelections"]["displayedScoringPeriod"] = "16"
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=raw), self.assertRaisesRegex(ValueError, "different period"):
            fantrax_data.extract_completed_lineup_evidence(EvidenceApi(current_roster(), raw), "me", completed_matchup())

    def test_score_mismatch_fails_closed(self):
        matchup = completed_matchup()
        matchup["my_score"] = 999
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=historical_roster()), self.assertRaisesRegex(ValueError, "does not match"):
            fantrax_data.extract_completed_lineup_evidence(EvidenceApi(current_roster(), historical_roster()), "me", matchup)

    def test_ambiguous_role_fails_closed(self):
        raw = historical_roster()
        raw["tables"][0]["headers"] = [{"sortKey": "FPTS"}]
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=raw), self.assertRaisesRegex(ValueError, "stable identity or scoring role"):
            fantrax_data.extract_completed_lineup_evidence(EvidenceApi(current_roster(), raw), "me", completed_matchup())

    def test_missing_and_conflicting_response_identity_fail_closed(self):
        missing = historical_roster()
        del missing["displayedSelections"]["teamId"]
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=missing), self.assertRaisesRegex(ValueError, "team identity"):
            fantrax_data.extract_completed_lineup_evidence(EvidenceApi(current_roster(), missing), "me", completed_matchup())
        conflict = historical_roster()
        conflict["displayedSelections"]["displayedScoringPeriod"] = "14"
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=conflict), self.assertRaisesRegex(ValueError, "conflicting periods"):
            fantrax_data.extract_completed_lineup_evidence(EvidenceApi(current_roster(), conflict), "me", completed_matchup())

    def test_scoring_value_follows_exact_header_index(self):
        raw = historical_roster()
        raw["tables"][0]["headers"] = [
            {"sortKey": "SCORING_CATEGORY_10_FPTS"}, {"sortKey": "AGE"}
        ]
        raw["tables"][0]["rows"][0]["cells"] = [{"content": "260.5"}, {"content": "30"}]
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=raw):
            evidence = fantrax_data.extract_completed_lineup_evidence(
                EvidenceApi(current_roster(), raw), "me", completed_matchup()
            )
        judge = next(item for item in evidence["players"] if item["player_id"] == "judge")
        self.assertEqual(judge["period_fpts"], "260.5")
        self.assertEqual(judge["period_fpts_source"], "SCORING_CATEGORY_10:cells[0].content")

    def test_reserve_points_are_archived_but_not_reconciled_as_active(self):
        raw = historical_roster()
        raw["miscData"]["statusTotals"].append({"id": "2", "name": "Bench", "shortName": "BN"})
        bench = player("bench", "Bench Player", "012", "12")
        bench["statusId"] = "2"
        raw["tables"][0]["rows"].append(bench)
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=raw):
            evidence = fantrax_data.extract_completed_lineup_evidence(
                EvidenceApi(current_roster(), raw), "me", completed_matchup()
            )
        self.assertEqual(evidence["active_player_total"], "264.5")
        self.assertEqual(next(item for item in evidence["players"] if item["player_id"] == "bench")["slot"], "BN")

    def test_active_two_way_double_count_fails_closed(self):
        raw = historical_roster()
        raw["tables"][1]["rows"][0]["scorer"]["scorerId"] = "judge"
        with patch.object(fantrax_data, "_direct_fxpa_request", return_value=raw), self.assertRaisesRegex(ValueError, "two-way"):
            fantrax_data.extract_completed_lineup_evidence(EvidenceApi(current_roster(), raw), "me", completed_matchup())

    def test_archive_failure_isolated_from_refresh(self):
        with patch.dict("sandlot_refresh.os.environ", {"DATABASE_URL": "postgres://test"}), patch(
            "sandlot_refresh.sandlot_db.archive_lineup_period_evidence",
            side_effect=RuntimeError("conflict"),
        ):
            import sandlot_refresh

            sandlot_refresh._persist_lineup_period_evidence(
                7, {"completed_lineup_evidence": {"evidence_version": "fantrax_period_lineup_v1"}}
            )

    def test_archive_replay_is_idempotent_and_concurrency_safe(self):
        evidence = self.evidence()
        stored = {"evidence_hash": evidence["evidence_hash"], "evidence": evidence}

        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Conn:
            def __init__(self):
                self.calls = 0
                self.sql = []

            def execute(self, sql, _params=None):
                self.calls += 1
                self.sql.append(sql)
                return Result(None if self.calls == 1 else stored)

        conn = Conn()

        @contextmanager
        def connect():
            yield conn

        with patch.object(sandlot_db, "connect", connect):
            row, changed = sandlot_db.archive_lineup_period_evidence(evidence=evidence, snapshot_id=7)

        self.assertFalse(changed)
        self.assertEqual(row, stored)
        self.assertIn("ON CONFLICT", conn.sql[0])

    def test_archive_rejects_tampered_hash_before_db_write(self):
        evidence = self.evidence()
        evidence["observed_team_total"] = "1"
        with self.assertRaisesRegex(ValueError, "hash is invalid"):
            sandlot_db.archive_lineup_period_evidence(evidence=evidence, snapshot_id=7)
