"""Read-only production contract monitor for Sandlot.

The monitor only performs HTTP GET requests against Sandlot's public read
surfaces. It never calls Fantrax directly, triggers a refresh, or invokes the
actions executor. Reports intentionally contain counts and invariant failures,
not roster/player payloads, so they are safe to hand to an automated repair
agent.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://web-production-90664.up.railway.app"
ENDPOINTS = (
    "/api/health",
    "/api/snapshot/latest",
    "/api/attention",
    "/api/hot-swaps/latest",
    "/api/waiver-swaps/latest",
)
NEVER_DROP_PLAYER_NAMES = {"aaron judge"}


def fetch_json(base_url: str, path: str, *, timeout: float = 20.0) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "sandlot-readonly-monitor/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS deployment URL by default
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("response JSON was not an object")
    return payload


def collect_payloads(
    base_url: str,
    *,
    timeout: float = 20.0,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    payloads: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for path in ENDPOINTS:
        try:
            payloads[path] = fetch_json(base_url, path, timeout=timeout)
        except RuntimeError as exc:
            errors[path] = str(exc)
    return payloads, errors


def evaluate_payloads(
    payloads: dict[str, dict[str, Any]],
    *,
    transport_errors: dict[str, str] | None = None,
    max_age_hours: float = 36.0,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate stable cross-endpoint invariants without retaining raw data."""
    now = checked_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []

    def fail(code: str, message: str) -> None:
        failures.append({"code": code, "message": message})

    def warn(code: str, message: str) -> None:
        warnings.append({"code": code, "message": message})

    for path, error in sorted((transport_errors or {}).items()):
        fail("endpoint_unavailable", f"{path}: {error}")

    health = payloads.get("/api/health")
    if isinstance(health, dict):
        if health.get("ok") is not True or health.get("database") != "ok":
            fail("health_not_ok", "Health endpoint did not report an available database")
        latest_run = health.get("latest_refresh_run")
        if isinstance(latest_run, dict) and latest_run.get("status") == "failed":
            warn("latest_refresh_failed", "The latest recorded refresh run failed")
        checks.append({
            "name": "health",
            "ok": health.get("ok") is True and health.get("database") == "ok",
            "freshness": _freshness_state(health),
            "latest_refresh_status": latest_run.get("status") if isinstance(latest_run, dict) else None,
        })
    elif "/api/health" not in (transport_errors or {}):
        fail("health_missing", "Health payload was missing")

    snapshot = payloads.get("/api/snapshot/latest")
    snapshot_id = _snapshot_id(snapshot)
    if isinstance(snapshot, dict):
        if snapshot_id is None:
            fail("snapshot_id_missing", "Latest snapshot did not include a stable snapshot_id")
        roster = snapshot.get("roster")
        if not isinstance(roster, list) or not roster:
            fail("roster_missing", "Latest snapshot did not contain a non-empty roster")
        errors = snapshot.get("errors")
        if not isinstance(errors, list):
            fail("snapshot_errors_invalid", "Latest snapshot errors field was not a list")
        elif errors:
            fail("snapshot_has_errors", f"Latest snapshot reported {len(errors)} collection error(s)")

        age_minutes = _age_minutes(snapshot, now)
        freshness_state = _freshness_state(snapshot)
        if age_minutes is None:
            fail("snapshot_age_missing", "Latest snapshot age could not be determined")
        elif age_minutes > max_age_hours * 60:
            fail(
                "snapshot_too_old",
                f"Latest snapshot was {age_minutes} minutes old; limit is {int(max_age_hours * 60)}",
            )
        if freshness_state in {"missing", "old"}:
            fail("snapshot_not_fresh_enough", f"Latest snapshot freshness state was {freshness_state}")

        quality = snapshot.get("data_quality")
        my_roster_quality = quality.get("my_roster") if isinstance(quality, dict) else None
        if isinstance(my_roster_quality, dict) and my_roster_quality.get("state") != "ok":
            fail("roster_quality_degraded", "My-roster data-quality state was not ok")
        lineup_quality = quality.get("lineup_slots") if isinstance(quality, dict) else None
        if isinstance(lineup_quality, dict) and lineup_quality.get("state") != "ok":
            warn("lineup_advice_paused", "Lineup-slot provenance is not fully trusted")

        checks.append({
            "name": "snapshot",
            "ok": not any(item["code"].startswith("snapshot_") or item["code"] in {"roster_missing", "roster_quality_degraded"} for item in failures),
            "snapshot_id": snapshot_id,
            "roster_count": len(roster) if isinstance(roster, list) else 0,
            "error_count": len(errors) if isinstance(errors, list) else None,
            "age_minutes": age_minutes,
            "freshness": freshness_state,
            "lineup_slots_state": lineup_quality.get("state") if isinstance(lineup_quality, dict) else None,
        })
    elif "/api/snapshot/latest" not in (transport_errors or {}):
        fail("snapshot_missing", "Latest snapshot payload was missing")

    attention = payloads.get("/api/attention")
    if isinstance(attention, dict):
        _require_matching_snapshot_id("attention", attention, snapshot_id, fail)
        items = attention.get("items")
        if not isinstance(items, list):
            fail("attention_items_invalid", "Attention items field was not a list")
            items = []
        _validate_read_only_proposals(items, snapshot_id, "attention", fail)
        checks.append({
            "name": "attention",
            "ok": not any(item["code"].startswith("attention_") for item in failures),
            "item_count": len(items),
            "change_count": len(attention.get("changes")) if isinstance(attention.get("changes"), list) else None,
        })
    elif "/api/attention" not in (transport_errors or {}):
        fail("attention_missing", "Attention payload was missing")

    hot_swaps = payloads.get("/api/hot-swaps/latest")
    if isinstance(hot_swaps, dict):
        _require_matching_snapshot_id("hot_swaps", hot_swaps, snapshot_id, fail)
        if hot_swaps.get("writes_enabled") is not False:
            fail("hot_swaps_write_boundary", "Hot swaps did not explicitly keep writes disabled")
        proposals = hot_swaps.get("proposals")
        if not isinstance(proposals, list):
            fail("hot_swaps_proposals_invalid", "Hot-swap proposals field was not a list")
            proposals = []
        _validate_read_only_proposals(proposals, snapshot_id, "hot_swaps", fail)
        checks.append({
            "name": "hot_swaps",
            "ok": not any(item["code"].startswith("hot_swaps_") for item in failures),
            "state": hot_swaps.get("state"),
            "proposal_count": len(proposals),
            "writes_enabled": hot_swaps.get("writes_enabled"),
        })
    elif "/api/hot-swaps/latest" not in (transport_errors or {}):
        fail("hot_swaps_missing", "Hot-swaps payload was missing")

    waivers = payloads.get("/api/waiver-swaps/latest")
    if isinstance(waivers, dict):
        _require_matching_snapshot_id("waivers", waivers, snapshot_id, fail)
        cards = waivers.get("cards")
        if not isinstance(cards, list):
            fail("waivers_cards_invalid", "Waiver cards field was not a list")
            cards = []
        for index, card in enumerate(cards):
            move_out = card.get("move_out") if isinstance(card, dict) and isinstance(card.get("move_out"), dict) else {}
            move_out_name = " ".join(str(move_out.get("name") or "").split()).casefold()
            if move_out_name in NEVER_DROP_PLAYER_NAMES:
                fail(
                    "waivers_protected_anchor",
                    f"Waiver card {index + 1} attempted to move out an owner-protected anchor",
                )
            delta = _number(card.get("net_delta")) if isinstance(card, dict) else None
            if delta is None or delta <= 0:
                fail("waivers_nonpositive_delta", f"Waiver card {index + 1} did not have a positive net delta")
                continue
            add = card.get("add") if isinstance(card.get("add"), dict) else {}
            score_source = str(add.get("score_source") or "").casefold()
            if (
                card.get("confidence") == "Low"
                or add.get("age") is None
                or move_out.get("age") is None
                or "inferred" in score_source
            ):
                fail(
                    "waivers_untrusted_card",
                    f"Waiver card {index + 1} was actionable without trusted value and dynasty-age context",
                )
        quality = waivers.get("data_quality")
        if isinstance(quality, dict) and quality.get("add_drop_recommendations_ready") is not True:
            warn("waiver_advice_paused", "Add/drop recommendations are currently paused by data quality")
        checks.append({
            "name": "waivers",
            "ok": not any(item["code"].startswith("waivers_") for item in failures),
            "card_count": len(cards),
            "recommendations_ready": quality.get("add_drop_recommendations_ready") if isinstance(quality, dict) else None,
        })
    elif "/api/waiver-swaps/latest" not in (transport_errors or {}):
        fail("waivers_missing", "Waiver payload was missing")

    return {
        "schema_version": 1,
        "ok": not failures,
        "checked_at": now.isoformat(),
        "checks": checks,
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "failures": failures,
        "warnings": warnings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    status = "PASS" if report.get("ok") else "FAIL"
    lines = [
        f"# Sandlot read-only monitor: {status}",
        "",
        f"Checked at: `{report.get('checked_at')}`",
        f"Failures: **{report.get('failure_count', 0)}** · Warnings: **{report.get('warning_count', 0)}**",
        "",
        "## Checks",
        "",
    ]
    for check in report.get("checks") or []:
        details = ", ".join(
            f"{key}={value}"
            for key, value in check.items()
            if key not in {"name", "ok"} and value is not None
        )
        marker = "PASS" if check.get("ok") else "FAIL"
        lines.append(f"- **{check.get('name')}**: {marker}" + (f" — {details}" if details else ""))

    if report.get("failures"):
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- `{item['code']}`: {item['message']}" for item in report["failures"])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{item['code']}`: {item['message']}" for item in report["warnings"])
    lines.extend([
        "",
        "This report contains contract states and counts only. It performs GET requests and cannot execute Fantrax actions.",
        "",
    ])
    return "\n".join(lines)


def _snapshot_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("snapshot_id")
    return None if value in (None, "") else str(value)


def _require_matching_snapshot_id(name: str, payload: dict[str, Any], expected: str | None, fail) -> None:
    actual = _snapshot_id(payload)
    if expected is None or actual is None or actual != expected:
        fail(
            f"{name}_snapshot_mismatch",
            f"{name} snapshot_id did not match the latest persisted snapshot",
        )


def _validate_read_only_proposals(
    entries: list[Any],
    snapshot_id: str | None,
    prefix: str,
    fail,
) -> None:
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        proposal = entry.get("proposal")
        if not isinstance(proposal, dict):
            continue
        if proposal.get("writes_enabled") is not False:
            fail(f"{prefix}_write_boundary", f"{prefix} proposal {index + 1} did not keep writes disabled")
        if proposal.get("executable") is True or proposal.get("status") == "executable":
            fail(f"{prefix}_executable_proposal", f"{prefix} proposal {index + 1} was marked executable")
        contract = proposal.get("contract")
        if isinstance(contract, dict):
            contract_snapshot_id = contract.get("snapshot_id")
            if snapshot_id is None or str(contract_snapshot_id) != snapshot_id:
                fail(
                    f"{prefix}_contract_snapshot_mismatch",
                    f"{prefix} proposal {index + 1} contract was not bound to the latest snapshot",
                )


def _freshness_state(payload: dict[str, Any]) -> str | None:
    freshness = payload.get("freshness")
    return str(freshness.get("state")) if isinstance(freshness, dict) and freshness.get("state") else None


def _age_minutes(payload: dict[str, Any], now: datetime) -> int | None:
    freshness = payload.get("freshness")
    if isinstance(freshness, dict):
        age = freshness.get("age_minutes")
        if isinstance(age, (int, float)) and age >= 0:
            return int(age)
    taken_at = payload.get("taken_at")
    if not isinstance(taken_at, str) or not taken_at.strip():
        return None
    try:
        parsed = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds() / 60))


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in {float("inf"), float("-inf")} else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-age-hours", type=float, default=36.0)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-markdown", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payloads, errors = collect_payloads(args.base_url, timeout=args.timeout)
    report = evaluate_payloads(
        payloads,
        transport_errors=errors,
        max_age_hours=args.max_age_hours,
    )
    markdown = render_markdown(report)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report_markdown:
        args.report_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.report_markdown.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
