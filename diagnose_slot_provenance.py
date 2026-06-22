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
from types import SimpleNamespace
from typing import Any
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fantraxapi import FantraxAPI

import auth
import fantrax_data
import fantrax_dom
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


def _raw_roster_data_from_payload(payload: Any) -> dict[str, Any]:
    """Extract a Fantrax-shaped raw roster payload from saved JSON."""
    if isinstance(payload, list):
        return {"tables": [{"rows": [row for row in payload if isinstance(row, dict)]}]}
    if not isinstance(payload, dict):
        raise RuntimeError("raw roster JSON must be an object or list")

    direct_rows = payload.get("rows")
    if isinstance(direct_rows, list):
        return {"tables": [{"rows": [row for row in direct_rows if isinstance(row, dict)]}]}

    candidates = []
    if isinstance(payload.get("tables"), list):
        candidates.append(payload)
    for key in ("data", "payload", "response", "roster"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_rows = nested.get("rows")
            if isinstance(nested_rows, list):
                return {"tables": [{"rows": [row for row in nested_rows if isinstance(row, dict)]}]}
            if isinstance(nested.get("tables"), list):
                candidates.append(nested)

    for candidate in candidates:
        rows = fantrax_data._raw_roster_rows(SimpleNamespace(_data=candidate))
        if rows:
            return candidate
    if candidates:
        return candidates[0]
    raise RuntimeError("raw roster JSON must contain Fantrax tables or raw roster rows")


def _raw_roster_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Extract raw Fantrax roster rows from a saved getTeamRosterInfo payload."""
    return fantrax_data._raw_roster_rows(SimpleNamespace(_data=_raw_roster_data_from_payload(payload)))


def _raw_assignment_diagnostics(raw_data: dict[str, Any], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    roster = SimpleNamespace(_data=raw_data)
    status_lookup = fantrax_data._status_lookup(roster)
    source_counts = Counter()
    slot_counts = Counter()
    unassigned_examples = []
    assigned_examples = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        slot, source = fantrax_data._assigned_slot_from_raw(row, status_lookup)
        if slot and source:
            slot_counts[str(slot)] += 1
            source_counts[str(source)] += 1
            if len(assigned_examples) < 5:
                scorer = row.get("scorer") if isinstance(row.get("scorer"), dict) else {}
                assigned_examples.append({
                    "id": fantrax_data._row_player_id(row),
                    "name": scorer.get("name") or scorer.get("shortName"),
                    "slot": slot,
                    "source": source,
                    "statusId": row.get("statusId"),
                    "posId": row.get("posId"),
                })
        else:
            if len(unassigned_examples) < 5:
                scorer = row.get("scorer") if isinstance(row.get("scorer"), dict) else {}
                unassigned_examples.append({
                    "id": fantrax_data._row_player_id(row),
                    "name": scorer.get("name") or scorer.get("shortName"),
                    "statusId": row.get("statusId"),
                    "posId": row.get("posId"),
                })
    return {
        "status_lookup": dict(sorted(status_lookup.items())),
        "assigned_slot_rows": sum(slot_counts.values()),
        "unassigned_slot_rows": len(raw_rows) - sum(slot_counts.values()),
        "assigned_slot_counts": dict(sorted(slot_counts.items())),
        "assigned_slot_source_counts": dict(sorted(source_counts.items())),
        "assigned_examples": assigned_examples,
        "unassigned_examples": unassigned_examples,
    }


def raw_roster_report(payload: Any, *, source: str) -> dict[str, Any]:
    raw_data = _raw_roster_data_from_payload(payload)
    rows = fantrax_data._raw_roster_rows(SimpleNamespace(_data=raw_data))
    assigned_slot_rows = sum(
        1
        for row in rows
        if any(row.get(key) not in (None, "") for key in fantrax_data.RAW_ASSIGNED_SLOT_KEYS)
    )
    pos_only_rows = sum(
        1
        for row in rows
        if row.get("posId") not in (None, "")
        and not any(row.get(key) not in (None, "") for key in fantrax_data.RAW_ASSIGNED_SLOT_KEYS)
    )
    return {
        "source": source,
        "verdict": "raw_only",
        "row_count": len(rows),
        "assigned_slot_candidate_rows": assigned_slot_rows,
        "pos_only_rows": pos_only_rows,
        "note": (
            "Raw Fantrax JSON can identify candidate slot fields, but it cannot "
            "prove normalized Sandlot slot provenance until extract_roster maps "
            "those fields into roster slot_source values."
        ),
        "raw": _raw_row_diagnostics(rows),
        "assignment": _raw_assignment_diagnostics(raw_data, rows),
    }


def dom_roster_report(html: str, *, source: str) -> dict[str, Any]:
    slots = fantrax_dom.lineup_slots_from_html(html)
    return {
        "source": source,
        "verdict": "dom_only",
        "player_count": len(slots),
        "slot_counts": dict(sorted(Counter(item["slot"] for item in slots.values()).items())),
        "slot_source_counts": dict(sorted(Counter(item["slot_source"] for item in slots.values()).items())),
        "examples": [
            {"id": player_id, **item}
            for player_id, item in list(sorted(slots.items()))[:8]
        ],
        "note": (
            "Saved roster DOM can prove player-slot mappings from Fantrax "
            "lineup buttons when combined with a matching Sandlot snapshot."
        ),
    }


def _snapshot_with_dom_slots(snapshot: dict[str, Any], dom_slots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    normalized = dict(snapshot)
    roster = snapshot.get("roster")
    rows = _rows_from_snapshot(snapshot)
    updated_rows = []
    for row in rows:
        out = dict(row)
        player_id = str(out.get("id") or "")
        dom_slot = dom_slots.get(player_id)
        if dom_slot and dom_slot.get("slot"):
            out["slot"] = dom_slot["slot"]
            out["slot_full"] = dom_slot["slot"]
            out["slot_source"] = dom_slot.get("slot_source") or "dom.lineup-btn"
        updated_rows.append(out)
    if isinstance(roster, dict):
        roster_copy = dict(roster)
        roster_copy["rows"] = updated_rows
        normalized["roster"] = roster_copy
    else:
        normalized["roster"] = updated_rows
    normalized.pop("data_quality", None)
    return normalized


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


def _load_json_any_file(path: Path) -> Any:
    return json.loads(path.read_text())


def _load_text_file(path: Path) -> str:
    return path.read_text()


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


def _live_fantrax_snapshot(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
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
    return {"roster": roster_data, "team_id": team_id, "league_id": league_id}, raw_rows, cookies


def _print_human(report: dict[str, Any]) -> None:
    if report.get("verdict") == "dom_only":
        print("Fantrax roster DOM diagnostic: dom_only")
        print(f"Source: {report['source']}")
        print(f"Players with lineup buttons: {report['player_count']}")
        print(f"Slot counts: {json.dumps(report['slot_counts'], sort_keys=True)}")
        print(report["note"])
        examples = report.get("examples") or []
        if examples:
            print("Examples:")
            for example in examples:
                print(f"- {example.get('id')}: slot={example.get('slot')} text={example.get('text')!r}")
        return

    if report.get("verdict") == "raw_only":
        print("Raw Fantrax roster diagnostic: raw_only")
        print(f"Source: {report['source']}")
        print(f"Rows: {report['row_count']} raw roster rows")
        print(f"Assigned-slot candidate rows: {report['assigned_slot_candidate_rows']}")
        print(f"Rows with posId only: {report['pos_only_rows']}")
        print(report["note"])
        assignment = report.get("assignment") or {}
        if assignment:
            print(
                "Current extractor assignments: "
                f"{assignment.get('assigned_slot_rows', 0)} assigned, "
                f"{assignment.get('unassigned_slot_rows', 0)} unassigned"
            )
            print(
                "Assignment sources: "
                f"{json.dumps(assignment.get('assigned_slot_source_counts') or {}, sort_keys=True)}"
            )
        raw = report.get("raw")
        if raw:
            print(f"Raw statusId counts: {json.dumps(raw['status_id_counts'], sort_keys=True)}")
            print(f"Raw slot keys by statusId: {json.dumps(raw['slot_key_counts_by_status'], sort_keys=True)}")
        return

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
    source.add_argument("--raw-roster-file", help="Inspect a saved raw Fantrax getTeamRosterInfo JSON file")
    parser.add_argument("--roster-dom-file", help="Inspect or apply a saved Fantrax roster page HTML file")
    parser.add_argument("--capture-roster-dom", action="store_true", help="During live diagnostics, read roster page HTML and apply lineup-btn slots")
    parser.add_argument("--fantrax-roster-url", help="Override the Fantrax roster URL used by --capture-roster-dom")
    parser.add_argument("--dom-headful", action="store_true", help="Open visible Chrome for --capture-roster-dom")
    parser.add_argument("--dom-wait-seconds", type=float, default=20, help="Seconds to wait for roster DOM capture readiness")
    parser.add_argument("--league-id", help="Fantrax league id for live read-only diagnostics")
    parser.add_argument("--team-id", help="Fantrax team id for live read-only diagnostics")
    parser.add_argument("--cookies-file", default=str(auth.COOKIE_PATH), help="Fantrax cookie JSON path")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--require-trusted", action="store_true", help="Exit non-zero unless roster-slot provenance is trusted")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.capture_roster_dom and (args.snapshot_url or args.snapshot_file or args.raw_roster_file or args.roster_dom_file):
        raise RuntimeError("--capture-roster-dom is only supported for live Fantrax diagnostics")

    dom_slots = None
    dom_report = None
    if args.roster_dom_file:
        dom_html = _load_text_file(Path(args.roster_dom_file))
        dom_slots = fantrax_dom.lineup_slots_from_html(dom_html)
        dom_report = dom_roster_report(dom_html, source=args.roster_dom_file)

    if args.snapshot_url:
        snapshot = _load_json_url(args.snapshot_url)
        raw_rows = None
        source = args.snapshot_url
    elif args.snapshot_file:
        snapshot = _load_json_file(Path(args.snapshot_file))
        raw_rows = None
        source = args.snapshot_file
    elif args.raw_roster_file:
        report = raw_roster_report(_load_json_any_file(Path(args.raw_roster_file)), source=args.raw_roster_file)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human(report)
        if args.require_trusted:
            return 2
        return 0
    elif dom_report is not None:
        if args.json:
            print(json.dumps(dom_report, indent=2, sort_keys=True))
        else:
            _print_human(dom_report)
        if args.require_trusted:
            return 2
        return 0
    else:
        snapshot, raw_rows, cookies = _live_fantrax_snapshot(args)
        source = "live-fantrax-read-only"
        if args.capture_roster_dom:
            dom_html = fantrax_dom.capture_roster_html(
                cookies,
                league_id=str(snapshot["league_id"]),
                team_id=str(snapshot["team_id"]),
                headful=bool(args.dom_headful),
                url=args.fantrax_roster_url,
                wait_seconds=float(args.dom_wait_seconds),
            )
            dom_slots = fantrax_dom.lineup_slots_from_html(dom_html)
            dom_report = dom_roster_report(dom_html, source="live-fantrax-roster-dom")

    if dom_slots is not None:
        snapshot = _snapshot_with_dom_slots(snapshot, dom_slots)
        source = f"{source} + {args.roster_dom_file}"

    report = slot_provenance_report(snapshot, source=source, raw_rows=raw_rows)
    if dom_report is not None:
        report["dom"] = dom_report
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    if args.require_trusted and report["verdict"] != "trusted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
