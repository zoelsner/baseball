import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import diagnose_slot_provenance as diagnostic


class SlotProvenanceDiagnosticTests(unittest.TestCase):
    def _write_snapshot(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(payload, handle)
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_trusted_snapshot_reports_trusted_verdict(self):
        report = diagnostic.slot_provenance_report(
            {
                "roster": {
                    "rows": [
                        {"id": "a", "name": "Starter", "slot": "OF", "positions": "OF", "slot_source": "raw.lineupSlot"},
                        {"id": "b", "name": "Reserve", "slot": "RES", "positions": "SP", "slot_source": "raw.statusId"},
                    ]
                },
                "data_quality": {
                    "lineup_recommendations_ready": False,
                    "add_drop_recommendations_ready": False,
                    "lineup_slots": {"state": "ok", "trusted_players": 2, "total_players": 2},
                },
            },
            source="test",
        )

        self.assertEqual(report["verdict"], "trusted")
        self.assertFalse(report["lineup_recommendations_ready"])
        self.assertEqual(report["row_slot_sources"]["state"], "ok")
        self.assertEqual(report["slot_source_counts"], {"raw.lineupSlot": 1, "raw.statusId": 1})
        self.assertEqual(report["active_untrusted_rows"], 0)

    def test_api_payload_roster_list_reports_fail_closed_for_position_fallback(self):
        report = diagnostic.slot_provenance_report(
            {
                "roster": [
                    {
                        "id": "friedl",
                        "name": "TJ Friedl",
                        "slot": "OF",
                        "positions": "OF",
                        "slot_source": "position_fallback",
                    }
                ],
                "data_quality": {
                    "lineup_recommendations_ready": False,
                    "add_drop_recommendations_ready": False,
                    "lineup_recommendation_reasons": ["Lineup-slot source trusted for 17/37 roster players"],
                    "lineup_slots": {
                        "state": "partial",
                        "trusted_players": 17,
                        "total_players": 37,
                    },
                },
            },
            source="api",
        )

        self.assertEqual(report["verdict"], "fail_closed")
        self.assertFalse(report["lineup_recommendations_ready"])
        self.assertEqual(report["active_untrusted_rows"], 1)
        self.assertEqual(report["active_untrusted_examples"][0]["name"], "TJ Friedl")

    def test_row_slot_sources_override_stale_trusted_data_quality(self):
        report = diagnostic.slot_provenance_report(
            {
                "roster": [
                    {
                        "id": "fallback",
                        "name": "Fallback Slot",
                        "slot": "OF",
                        "positions": "OF",
                        "slot_source": "position_fallback",
                    }
                ],
                "data_quality": {
                    "lineup_recommendations_ready": True,
                    "add_drop_recommendations_ready": True,
                    "lineup_slots": {"state": "ok", "trusted_players": 1, "total_players": 1},
                },
            },
            source="api",
        )

        self.assertEqual(report["verdict"], "fail_closed")
        self.assertEqual(report["row_slot_sources"]["state"], "missing")
        self.assertEqual(len(report["consistency_warnings"]), 1)

    def test_absent_slot_source_field_warns_about_wrong_json_source(self):
        report = diagnostic.slot_provenance_report(
            {
                "roster": [
                    {
                        "id": "flattened",
                        "name": "Flattened Source",
                        "slot": "OF",
                        "positions": "OF",
                    }
                ],
            },
            source="player-index",
        )

        self.assertEqual(report["verdict"], "fail_closed")
        self.assertEqual(report["row_slot_sources"]["field_present_players"], 0)
        self.assertIn("no roster rows include slot_source", report["consistency_warnings"][0])

    def test_raw_diagnostics_count_active_slot_keys_without_mutating(self):
        report = diagnostic.slot_provenance_report(
            {
                "roster": {
                    "rows": [
                        {"id": "a", "name": "Starter", "slot": "OF", "positions": "OF", "slot_source": "raw.lineupSlot"},
                    ]
                },
            },
            source="live",
            raw_rows=[
                {
                    "statusId": "1",
                    "posId": "OF",
                    "lineupSlot": "OF",
                    "scorer": {"scorerId": "a", "name": "Starter"},
                },
                {
                    "statusId": "2",
                    "posId": "SP",
                    "scorer": {"scorerId": "b", "name": "Bench Arm"},
                },
            ],
        )

        self.assertEqual(report["raw"]["raw_rows"], 2)
        self.assertEqual(report["raw"]["status_id_counts"], {"1": 1, "2": 1})
        self.assertEqual(report["raw"]["slot_key_counts_by_status"]["1"]["lineupSlot"], 1)
        self.assertEqual(report["raw"]["slot_key_counts_by_status"]["2"]["posId"], 1)
        self.assertEqual(report["raw"]["samples_by_status"]["1"][0]["present_slot_keys"], ["lineupSlot", "statusId", "posId"])

    def test_raw_diagnostics_do_not_assume_active_status_id_is_one(self):
        report = diagnostic.slot_provenance_report(
            {"roster": {"rows": []}},
            source="live",
            raw_rows=[
                {
                    "statusId": "9",
                    "posId": "UT",
                    "lineupSlot": "UT",
                    "scorer": {"scorerId": "future-active", "name": "Future Active"},
                },
            ],
        )

        self.assertEqual(report["raw"]["status_id_counts"], {"9": 1})
        self.assertEqual(report["raw"]["slot_key_counts_by_status"]["9"]["lineupSlot"], 1)

    def test_raw_roster_file_payload_reports_key_coverage_with_active_posid_assignments(self):
        report = diagnostic.raw_roster_report(
            {
                "data": {
                    "miscData": {
                        "statusTotals": [
                            {"id": "1", "name": "Active"},
                            {"id": "2", "name": "Reserve"},
                        ]
                    },
                    "tables": [
                        {
                            "rows": [
                                {
                                    "statusId": "1",
                                    "posId": "OF",
                                    "lineupSlot": "OF",
                                    "scorer": {"scorerId": "active", "name": "Active Bat"},
                                },
                                {
                                    "statusId": "2",
                                    "posId": "SP",
                                    "scorer": {"scorerId": "reserve", "name": "Reserve Arm"},
                                },
                                {
                                    "statusId": "1",
                                    "posId": "SS",
                                    "scorer": {"scorerId": "pos-only", "name": "Position Only"},
                                },
                            ]
                        }
                    ]
                }
            },
            source="raw.json",
        )

        self.assertEqual(report["verdict"], "raw_only")
        self.assertEqual(report["row_count"], 3)
        self.assertEqual(report["assigned_slot_candidate_rows"], 1)
        self.assertEqual(report["pos_only_rows"], 2)
        self.assertIn("cannot prove normalized Sandlot slot provenance", report["note"])
        self.assertEqual(report["raw"]["slot_key_counts_by_status"]["1"]["lineupSlot"], 1)
        self.assertEqual(report["raw"]["slot_key_counts_by_status"]["2"]["posId"], 1)
        self.assertEqual(report["assignment"]["assigned_slot_rows"], 3)
        self.assertEqual(report["assignment"]["unassigned_slot_rows"], 0)
        self.assertEqual(report["assignment"]["assigned_slot_counts"], {"OF": 1, "RES": 1, "SS": 1})
        self.assertEqual(
            report["assignment"]["assigned_slot_source_counts"],
            {"raw.lineupSlot": 1, "raw.posId": 1, "raw.statusId": 1},
        )
        self.assertEqual(report["assignment"]["status_lookup"], {"1": "ACTIVE", "2": "RES", "Active": "ACTIVE", "Reserve": "RES"})
        self.assertEqual(report["assignment"]["assigned_examples"][-1]["id"], "pos-only")

    def test_raw_roster_file_exit_code_cannot_satisfy_require_trusted(self):
        path = self._write_snapshot({
            "rows": [
                {
                    "statusId": "1",
                    "posId": "OF",
                    "lineupSlot": "OF",
                    "scorer": {"scorerId": "active", "name": "Active Bat"},
                }
            ]
        })

        with contextlib.redirect_stdout(io.StringIO()):
            code = diagnostic.main(["--raw-roster-file", path, "--require-trusted"])

        self.assertEqual(code, 2)

    def test_roster_dom_file_exit_code_cannot_satisfy_require_trusted_without_snapshot(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
        with handle:
            handle.write('<div class="player-row" data-player-id="active"><button class="lineup-btn">OF</button></div>')
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))

        with contextlib.redirect_stdout(io.StringIO()):
            code = diagnostic.main(["--roster-dom-file", handle.name, "--require-trusted"])

        self.assertEqual(code, 2)

    def test_roster_dom_overlay_can_prove_snapshot_slot_sources(self):
        snapshot = {
            "roster": [
                {
                    "id": "active",
                    "name": "Active Bat",
                    "slot": "OF",
                    "positions": "OF",
                    "slot_source": "position_fallback",
                },
                {
                    "id": "reserve",
                    "name": "Reserve Arm",
                    "slot": "SP",
                    "positions": "SP",
                    "slot_source": "position_fallback",
                },
            ],
            "data_quality": {
                "lineup_slots": {"state": "partial", "trusted_players": 0, "total_players": 2},
            },
        }
        dom_slots = {
            "active": {"slot": "OF", "slot_source": "dom.lineup-btn", "text": "OF"},
            "reserve": {"slot": "RES", "slot_source": "dom.lineup-btn", "text": "Reserve"},
        }

        normalized = diagnostic._snapshot_with_dom_slots(snapshot, dom_slots)
        report = diagnostic.slot_provenance_report(normalized, source="snapshot+dom")

        self.assertEqual(report["verdict"], "trusted")
        self.assertEqual(report["lineup_slots"]["state"], "ok")
        self.assertEqual(report["slot_source_counts"], {"dom.lineup-btn": 2})
        self.assertEqual(report["active_untrusted_rows"], 0)

    def test_snapshot_plus_dom_file_can_satisfy_require_trusted(self):
        snapshot_path = self._write_snapshot({
            "roster": [
                {
                    "id": "active",
                    "name": "Active Bat",
                    "slot": "OF",
                    "positions": "OF",
                    "slot_source": "position_fallback",
                }
            ],
            "data_quality": {
                "lineup_slots": {"state": "partial", "trusted_players": 0, "total_players": 1},
            },
        })
        handle = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
        with handle:
            handle.write('<div class="player-row" data-player-id="active"><button class="lineup-btn">OF</button></div>')
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))

        with contextlib.redirect_stdout(io.StringIO()):
            code = diagnostic.main([
                "--snapshot-file",
                snapshot_path,
                "--roster-dom-file",
                handle.name,
                "--require-trusted",
            ])

        self.assertEqual(code, 0)

    def test_live_capture_roster_dom_can_satisfy_require_trusted(self):
        snapshot = {
            "roster": [
                {
                    "id": "active",
                    "name": "Active Bat",
                    "slot": "OF",
                    "positions": "OF",
                    "slot_source": "position_fallback",
                }
            ],
            "team_id": "team-1",
            "league_id": "league-1",
        }
        html = '<div class="player-row" data-player-id="active"><button class="lineup-btn">OF</button></div>'

        with patch.object(diagnostic, "_live_fantrax_snapshot", return_value=(snapshot, [], [{"name": "JSESSIONID", "value": "ok"}])), \
            patch.object(diagnostic.fantrax_dom, "capture_roster_html", return_value=html) as capture, \
            contextlib.redirect_stdout(io.StringIO()):
            code = diagnostic.main([
                "--capture-roster-dom",
                "--require-trusted",
                "--dom-wait-seconds",
                "0",
            ])

        self.assertEqual(code, 0)
        capture.assert_called_once()
        self.assertEqual(capture.call_args.kwargs["league_id"], "league-1")
        self.assertEqual(capture.call_args.kwargs["team_id"], "team-1")
        self.assertEqual(capture.call_args.kwargs["wait_seconds"], 0.0)

    def test_capture_roster_dom_rejects_non_live_sources(self):
        path = self._write_snapshot({"roster": []})

        with self.assertRaisesRegex(RuntimeError, "only supported for live"):
            diagnostic.main(["--snapshot-file", path, "--capture-roster-dom"])

    def test_require_trusted_exit_code_fails_when_slots_are_untrusted(self):
        path = self._write_snapshot({
            "roster": [
                {
                    "id": "fallback",
                    "name": "Fallback Slot",
                    "slot": "OF",
                    "positions": "OF",
                    "slot_source": "position_fallback",
                }
            ],
        })

        with contextlib.redirect_stdout(io.StringIO()):
            code = diagnostic.main(["--snapshot-file", path, "--require-trusted"])

        self.assertEqual(code, 2)

    def test_require_trusted_exit_code_passes_when_slot_provenance_is_trusted(self):
        path = self._write_snapshot({
            "roster": [
                {
                    "id": "trusted",
                    "name": "Trusted Slot",
                    "slot": "OF",
                    "positions": "OF",
                    "slot_source": "raw.lineupSlot",
                }
            ],
        })

        with contextlib.redirect_stdout(io.StringIO()):
            code = diagnostic.main(["--snapshot-file", path, "--require-trusted"])

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
