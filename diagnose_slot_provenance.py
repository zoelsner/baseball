"""Read-only roster slot provenance diagnostic.

This script is intentionally non-mutating: it never writes Fantrax actions,
snapshots, cookies, or database rows. Use it to prove whether the latest roster
data has trusted slot provenance before lineup/add-drop recommendation execution
is allowed.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fantraxapi import FantraxAPI

import auth
import fantrax_data
import sandlot_data_quality


RAW_SLOT_KEYS = (*fantrax_data.RAW_ASSIGNED_SLOT_KEYS, "statusId", "posId")


class _FetchedRosterApi:
    def __init__(self, roster: Any):
        self._roster = roster

    def team_roster(self, _team_id: str) -> Any:
        return self._roster


def _rows_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    roster = snapshot.get("roster")
    if isinstance(roster, dict):
        rows = roster.get("rows")
    else:
        rows = roster
    return [row for row in rows or [] if isinstance(row, dict)]


def _quality_from_snapshot(snapshot: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    existing = snapshot.get("data_quality")
    if isinstance(existing, dict) and isinstance(existing.get("lineup_slots"), dict):
        return existing
    normalized = dict(snapshot)
    normalized["roster"] = {"rows": rows}
    return sandlot_data_quality.snapshot_data_quality(normalized)


def _is_inactive_slot(slot: Any) -> bool:
    return str(slot or "").strip().upper() in sandlot_data_quality.INACTIVE_SLOTS


def _trusted_slot_source(row: dict[str, Any]) -> bool:
    return sandlot_data_quality._has_trusted_slot_source(row)


def _examples(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for row in rows[:limit]:
        out.append({
            "name": row.get("name"),
            "id": row.get("id"),
            "slot": row.get("slot"),
            "positions": row.get("positions"),
            "slot_source": row.get("slot_source"),
        })
    return out


def _row_slot_source_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality = dict(sandlot_data_quality._lineup_slots_quality(rows))
    field_present = sum(1 for row in rows if "slot_source" in row)
    quality["field_present_players"] = field_present
    return quality


def _raw_row_diagnostics(raw_rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if raw_rows is None:
        return None
    status_counts = Counter(str(row.get("statusId") or "") for row in raw_rows if isinstance(row, dict))
    key_counts = Counter()
    key_counts_by_status: dict[str, Counter[str]] = {}
    samples_by_status: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        present = [key for key in RAW_SLOT_KEYS if row.get(key) not in (None, "")]
        key_counts.update(present)
        status_id = str(row.get("statusId") or "<missing>")
        key_counts_by_status.setdefault(status_id, Counter()).update(present)
        samples = samples_by_status.setdefault(status_id, [])
        if len(samples) < 3:
            scorer = row.get("scorer") if isinstance(row.get("scorer"), dict) else {}
            samples.append({
                "id": row.get("scorerId") or row.get("playerId") or scorer.get("scorerId") or scorer.get("id"),
                "name": scorer.get("name") or scorer.get("shortName"),
                "present_slot_keys": present,
                "statusId": row.get("statusId"),
                "posId": row.get("posId"),
            })
    return {
        "raw_rows": len(raw_rows),
        "status_id_counts": dict(sorted(status_counts.items())),
        "slot_key_counts": dict(sorted(key_counts.items())),
        "slot_key_counts_by_status": {
            status_id: dict(sorted(counts.items()))
            for status_id, counts in sorted(key_counts_by_status.items())
        },
        "samples_by_status": dict(sorted(samples_by_status.items())),
    }


def slot_provenance_report(
    snapshot: dict[str, Any],
    *,
    source: str,
    raw_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = _rows_from_snapshot(snapshot)
    quality = _quality_from_snapshot(snapshot, rows)
    lineup_slots = quality.get("lineup_slots") if isinstance(quality.get("lineup_slots"), dict) else {}
    row_slot_sources = _row_slot_source_quality(rows)
    active_rows = [row for row in rows if not _is_inactive_slot(row.get("slot"))]
    untrusted_rows = [row for row in rows if not _trusted_slot_source(row)]
    active_untrusted_rows = [row for row in active_rows if not _trusted_slot_source(row)]
    trusted = len(rows) - len(untrusted_rows)
    consistency_warnings = []
    if rows and row_slot_sources["field_present_players"] == 0:
        consistency_warnings.append(
            "no roster rows include slot_source; this JSON source cannot prove roster-slot provenance"
        )
    if lineup_slots.get("state") == "ok" and row_slot_sources["state"] != "ok":
        consistency_warnings.append(
            "data_quality reports trusted lineup slots, but roster row slot_source coverage is not fully trusted"
        )
    ready = lineup_slots.get("state") == "ok" and row_slot_sources["state"] == "ok"
    return {
        "source": source,
        "verdict": "trusted" if ready else "fail_closed",
        "lineup_recommendations_ready": bool(quality.get("lineup_recommendations_ready")),
        "add_drop_recommendations_ready": bool(quality.get("add_drop_recommendations_ready")),
        "lineup_slots": lineup_slots,
        "row_slot_sources": row_slot_sources,
        "consistency_warnings": consistency_warnings,
        "row_count": len(rows),
        "trusted_rows": trusted,
        "untrusted_rows": len(untrusted_rows),
        "active_row_count": len(active_rows),
        "active_untrusted_rows": len(active_untrusted_rows),
        "slot_source_counts": dict(sorted(Counter(str(row.get("slot_source") or "") for row in rows).items())),
        "untrusted_examples": _examples(untrusted_rows),
        "active_untrusted_examples": _examples(active_untrusted_rows),
        "reasons": quality.get("lineup_recommendation_reasons") or quality.get("recommendation_reasons") or [],
        "raw": _raw_row_diagnostics(raw_rows),
    }


def _load_json_url(url: str) -> dict[str, Any]:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "sandlot-slot-diagnostic"})
    with urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return data


def _load_json_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} did not contain a JSON object")
    return data


def _cookies_from_env_or_file(path: Path) -> list[dict[str, Any]]:
    raw = os.environ.get("FANTRAX_COOKIES_JSON")
    if raw:
        cookies = json.loads(raw)
        if isinstance(cookies, list) and cookies:
            return cookies
        raise RuntimeError("FANTRAX_COOKIES_JSON must be a non-empty JSON array")
    if path.exists():
        cookies = json.loads(path.read_text())
        if isinstance(cookies, list) and cookies:
            return cookies
        raise RuntimeError(f"{path} must contain a non-empty JSON array")
    raise RuntimeError(
        f"No Fantrax cookies available. Set FANTRAX_COOKIES_JSON or create {path} with import_chrome_cookies.py."
    )


def _live_fantrax_snapshot(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    load_dotenv()
    league_id = args.league_id or os.environ.get("FANTRAX_LEAGUE_ID")
    team_id = args.team_id or os.environ.get("FANTRAX_TEAM_ID")
    if not league_id or not team_id:
        raise RuntimeError("FANTRAX_LEAGUE_ID and FANTRAX_TEAM_ID are required for live Fantrax diagnostics")
    cookies = _cookies_from_env_or_file(Path(args.cookies_file))
    session = auth._build_session(cookies)
    api = FantraxAPI(league_id, session=session)
    roster = fantrax_data._team_roster(api, team_id)
    roster_data = fantrax_data.extract_roster(_FetchedRosterApi(roster), team_id)
    raw_rows = fantrax_data._raw_roster_rows(roster)
    return {"roster": roster_data, "team_id": team_id, "league_id": league_id}, raw_rows


def _print_human(report: dict[str, Any]) -> None:
    print(f"Slot provenance diagnostic: {report['verdict']}")
    print(f"Source: {report['source']}")
    print(
        "Rows: "
        f"{report['row_count']} total, {report['trusted_rows']} trusted, "
        f"{report['untrusted_rows']} untrusted"
    )
    print(
        "Active rows: "
        f"{report['active_row_count']} total, {report['active_untrusted_rows']} untrusted"
    )
    print(f"Slot sources: {json.dumps(report['slot_source_counts'], sort_keys=True)}")
    lineup_slots = report.get("lineup_slots") or {}
    row_slot_sources = report.get("row_slot_sources") or {}
    print(
        "Lineup slots: "
        f"{lineup_slots.get('state', 'unknown')} "
        f"({lineup_slots.get('trusted_players', lineup_slots.get('trusted', '?'))}/"
        f"{lineup_slots.get('total_players', lineup_slots.get('total', '?'))} trusted)"
    )
    print(
        "Row slot sources: "
        f"{row_slot_sources.get('state', 'unknown')} "
        f"({row_slot_sources.get('trusted_players', '?')}/"
        f"{row_slot_sources.get('total_players', '?')} trusted; "
        f"{row_slot_sources.get('field_present_players', '?')} with slot_source)"
    )
    warnings = report.get("consistency_warnings") or []
    if warnings:
        print("Consistency warnings:")
        for warning in warnings:
            print(f"- {warning}")
    reasons = report.get("reasons") or []
    if reasons:
        print("Reasons:")
        for reason in reasons:
            print(f"- {reason}")
    if report.get("active_untrusted_examples"):
        print("Active untrusted examples:")
        for example in report["active_untrusted_examples"]:
            print(
                "- "
                f"{example.get('name') or example.get('id')}: "
                f"slot={example.get('slot')} source={example.get('slot_source')}"
            )
    raw = report.get("raw")
    if raw:
        print(f"Raw roster rows: {raw['raw_rows']}")
        print(f"Raw statusId counts: {json.dumps(raw['status_id_counts'], sort_keys=True)}")
        print(f"Raw slot keys by statusId: {json.dumps(raw['slot_key_counts_by_status'], sort_keys=True)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--snapshot-url", help="Read an existing Sandlot snapshot API URL")
    source.add_argument("--snapshot-file", help="Read an existing snapshot JSON file")
    parser.add_argument("--league-id", help="Fantrax league id for live read-only diagnostics")
    parser.add_argument("--team-id", help="Fantrax team id for live read-only diagnostics")
    parser.add_argument("--cookies-file", default=str(auth.COOKIE_PATH), help="Fantrax cookie JSON path")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--require-trusted", action="store_true", help="Exit non-zero unless roster-slot provenance is trusted")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.snapshot_url:
        snapshot = _load_json_url(args.snapshot_url)
        raw_rows = None
        source = args.snapshot_url
    elif args.snapshot_file:
        snapshot = _load_json_file(Path(args.snapshot_file))
        raw_rows = None
        source = args.snapshot_file
    else:
        snapshot, raw_rows = _live_fantrax_snapshot(args)
        source = "live-fantrax-read-only"

    report = slot_provenance_report(snapshot, source=source, raw_rows=raw_rows)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    if args.require_trusted and report["verdict"] != "trusted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
