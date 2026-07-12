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
    "/api/win-this-week/latest",
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
        trade_index_check = _validate_trade_index(snapshot, roster, fail)
        trade_index_check["ok"] = not any(item["code"].startswith("trade_index_") for item in failures)
        checks.append(trade_index_check)

        matchup_check = _validate_matchup_surface(snapshot, snapshot_id, fail)
        matchup_check["ok"] = not any(item["code"].startswith("matchup_") for item in failures)
        checks.append(matchup_check)
        embedded_plan = snapshot.get("win_this_week")
        if isinstance(embedded_plan, dict):
            before_failures = len(failures)
            embedded_check = _validate_win_this_week(
                embedded_plan,
                snapshot_id,
                fail,
                prefix="win_this_week_embedded",
                now=now,
            )
            embedded_check["name"] = "win_this_week_embedded"
            embedded_check["ok"] = len(failures) == before_failures
            checks.append(embedded_check)
        else:
            fail("win_this_week_embedded_missing", "Snapshot did not include the Win This Week plan")
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

    win_this_week = payloads.get("/api/win-this-week/latest")
    if isinstance(win_this_week, dict):
        before_failures = len(failures)
        win_check = _validate_win_this_week(
            win_this_week,
            snapshot_id,
            fail,
            prefix="win_this_week",
            now=now,
        )
        win_check["ok"] = len(failures) == before_failures
        checks.append(win_check)
        embedded_plan = snapshot.get("win_this_week") if isinstance(snapshot, dict) else None
        if isinstance(embedded_plan, dict) and _win_plan_signature(embedded_plan) != _win_plan_signature(win_this_week):
            fail(
                "win_this_week_cross_endpoint_drift",
                "Embedded and dedicated Win This Week plans did not expose the same ranked action contract",
            )
    elif "/api/win-this-week/latest" not in (transport_errors or {}):
        fail("win_this_week_missing", "Win This Week payload was missing")

    waivers = payloads.get("/api/waiver-swaps/latest")
    if isinstance(waivers, dict):
        _require_matching_snapshot_id("waivers", waivers, snapshot_id, fail)
        cards = waivers.get("cards")
        if not isinstance(cards, list):
            fail("waivers_cards_invalid", "Waiver cards field was not a list")
            cards = []
        protected_anchor_count = 0
        nonpositive_count = 0
        untrusted_count = 0
        for index, card in enumerate(cards):
            move_out = card.get("move_out") if isinstance(card, dict) and isinstance(card.get("move_out"), dict) else {}
            move_out_name = " ".join(str(move_out.get("name") or "").split()).casefold()
            if move_out_name in NEVER_DROP_PLAYER_NAMES:
                protected_anchor_count += 1
            delta = _number(card.get("net_delta")) if isinstance(card, dict) else None
            if delta is None or delta <= 0:
                nonpositive_count += 1
                continue
            add = card.get("add") if isinstance(card.get("add"), dict) else {}
            score_source = str(add.get("score_source") or "").casefold()
            if (
                card.get("confidence") == "Low"
                or add.get("age") is None
                or move_out.get("age") is None
                or not _trusted_age_source(add.get("age_source"))
                or not _trusted_age_source(move_out.get("age_source"))
                or "inferred" in score_source
            ):
                untrusted_count += 1
        if protected_anchor_count:
            fail(
                "waivers_protected_anchor",
                f"{protected_anchor_count} waiver card(s) attempted to move out an owner-protected anchor",
            )
        if nonpositive_count:
            fail(
                "waivers_nonpositive_delta",
                f"{nonpositive_count} waiver card(s) did not have a positive net delta",
            )
        if untrusted_count:
            fail(
                "waivers_untrusted_card",
                f"{untrusted_count} waiver card(s) were actionable without trusted value and dynasty-age context",
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
    reported_codes: set[str] = set()

    def fail_once(code: str, message: str) -> None:
        if code in reported_codes:
            return
        reported_codes.add(code)
        fail(code, message)

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        proposal = entry.get("proposal")
        if not isinstance(proposal, dict):
            continue
        if proposal.get("writes_enabled") is not False:
            fail_once(f"{prefix}_write_boundary", f"{prefix} proposal {index + 1} did not keep writes disabled")
        if proposal.get("executable") is True or proposal.get("status") == "executable":
            fail_once(f"{prefix}_executable_proposal", f"{prefix} proposal {index + 1} was marked executable")
        contract = proposal.get("contract")
        if not isinstance(contract, dict):
            fail_once(f"{prefix}_contract_missing", f"{prefix} proposal {index + 1} did not include a contract")
            continue
        contract_snapshot_id = contract.get("snapshot_id")
        if snapshot_id is None or str(contract_snapshot_id) != snapshot_id:
            fail_once(
                f"{prefix}_contract_snapshot_mismatch",
                f"{prefix} proposal {index + 1} contract was not bound to the latest snapshot",
            )
        if proposal.get("type") == "lineup_swap":
            _validate_lineup_contract(contract, prefix, index, fail_once)


def _validate_lineup_contract(contract: dict[str, Any], prefix: str, index: int, fail) -> None:
    label = f"{prefix} proposal {index + 1}"
    if contract.get("version") != 2:
        fail(f"{prefix}_contract_version", f"{label} did not use lineup contract version 2")
    input_hash = contract.get("input_hash")
    if not isinstance(input_hash, str) or len(input_hash) != 64:
        fail(f"{prefix}_contract_hash", f"{label} did not include a stable input hash")
    freshness = contract.get("freshness_policy")
    if not isinstance(freshness, dict) or freshness.get("requires_live_preflight") is not True:
        fail(f"{prefix}_freshness_policy", f"{label} did not require a live freshness preflight")
    post_write = contract.get("post_write_verification")
    if not isinstance(post_write, dict) or post_write.get("required") is not True:
        fail(f"{prefix}_post_write_verification", f"{label} did not require post-write verification")
    confirmation = contract.get("confirmation")
    expected = confirmation.get("expected") if isinstance(confirmation, dict) else None
    if (
        not isinstance(confirmation, dict)
        or confirmation.get("mode") != "exact_contract_match"
        or not isinstance(expected, dict)
        or expected.get("proposal_id") != contract.get("proposal_id")
        or expected.get("input_hash") != input_hash
        or expected.get("snapshot_id") != contract.get("snapshot_id")
        or expected.get("target_period") != contract.get("target_period")
        or expected.get("slot_moves") != contract.get("slot_moves")
    ):
        fail(f"{prefix}_exact_confirmation", f"{label} was not bound to an exact confirmation payload")


def _validate_trade_index(snapshot: dict[str, Any], roster: Any, fail) -> dict[str, Any]:
    index = snapshot.get("player_index")
    if not isinstance(index, list):
        fail("trade_index_missing", "Trade player index was missing")
        index = []

    trade_rows = [
        row
        for row in index
        if isinstance(row, dict) and row.get("source") in {"mine", "league"}
    ]
    mine_rows = [row for row in trade_rows if row.get("source") == "mine"]
    league_rows = [row for row in trade_rows if row.get("source") == "league"]
    roster_count = len(roster) if isinstance(roster, list) else 0
    if len(mine_rows) != roster_count:
        fail(
            "trade_index_my_roster_mismatch",
            "Trade index did not preserve every canonical roster player",
        )
    if not league_rows:
        fail("trade_index_league_missing", "Trade index did not include opponent roster players")

    identifiers = [str(row.get("id") or "") for row in trade_rows]
    if any(not identifier for identifier in identifiers) or len(identifiers) != len(set(identifiers)):
        fail("trade_index_identity", "Trade index had missing or duplicate player identities")
    missing_names = sum(1 for row in trade_rows if not str(row.get("name") or "").strip())
    if missing_names:
        fail("trade_index_names", "Trade index included players without names")

    valid_ages = sum(
        1
        for row in trade_rows
        if _valid_age(row.get("age")) and _trusted_age_source(row.get("age_source"))
    )
    valid_values = sum(1 for row in trade_rows if _valid_fppg(row.get("fppg")))
    if valid_ages != len(trade_rows):
        fail(
            "trade_index_age_coverage",
            f"Trade index age coverage was {valid_ages}/{len(trade_rows)}",
        )
    if valid_values != len(trade_rows):
        fail(
            "trade_index_value_coverage",
            f"Trade index FP/G coverage was {valid_values}/{len(trade_rows)}",
        )
    return {
        "name": "trade_index",
        "mine_count": len(mine_rows),
        "league_count": len(league_rows),
        "age_coverage": f"{valid_ages}/{len(trade_rows)}",
        "value_coverage": f"{valid_values}/{len(trade_rows)}",
    }


def _validate_matchup_surface(snapshot: dict[str, Any], snapshot_id: str | None, fail) -> dict[str, Any]:
    matchup = snapshot.get("matchup")
    if matchup is None:
        return {"name": "matchup", "state": "none", "recommendation_count": 0}
    if not isinstance(matchup, dict):
        fail("matchup_invalid", "Matchup payload was not an object")
        return {"name": "matchup", "state": "invalid", "recommendation_count": 0}

    quality = snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    projection = matchup.get("projection")
    if not isinstance(projection, dict):
        current_period = quality.get("current_period") if isinstance(quality.get("current_period"), dict) else {}
        recommendation_block = matchup.get("recommendations") if isinstance(matchup.get("recommendations"), dict) else {}
        no_action = recommendation_block.get("no_action") if isinstance(recommendation_block.get("no_action"), dict) else {}
        win_plan = snapshot.get("win_this_week") if isinstance(snapshot.get("win_this_week"), dict) else {}
        planning_horizon = win_plan.get("planning_horizon") if isinstance(win_plan.get("planning_horizon"), dict) else {}
        shifted_matchup = win_plan.get("matchup") if isinstance(win_plan.get("matchup"), dict) else {}
        editable_period = current_period.get("editable_period")
        matchup_period = current_period.get("matchup_period")
        shifted_projected_my = _number(shifted_matchup.get("projected_my"))
        shifted_projected_opponent = _number(shifted_matchup.get("projected_opponent"))
        shifted_projected_margin = _number(shifted_matchup.get("projected_margin"))
        has_valid_shifted_projection = (
            shifted_projected_my is not None
            and shifted_projected_opponent is not None
            and shifted_projected_margin is not None
            and abs(shifted_projected_margin - (shifted_projected_my - shifted_projected_opponent)) <= 0.11
        )
        shifted_no_action = win_plan.get("no_action") if isinstance(win_plan.get("no_action"), dict) else {}
        has_valid_shifted_state = win_plan.get("state") == "ready" or (
            win_plan.get("state") == "no_action"
            and bool(str(shifted_no_action.get("reason") or "").strip())
            and isinstance(shifted_no_action.get("alternatives"), list)
        )
        shifted_to_editable_period = (
            current_period.get("state") == "mismatch"
            and quality.get("lineup_recommendations_ready") is False
            and bool(str(no_action.get("reason") or "").strip())
            and has_valid_shifted_state
            and planning_horizon.get("mode") == "editable_period"
            and planning_horizon.get("period_number") == editable_period
            and planning_horizon.get("shifted_from_period") == matchup_period
            and matchup.get("period_number") == matchup_period
            and editable_period != matchup_period
            and has_valid_shifted_projection
        )
        if quality.get("projection_ready") is not False and not shifted_to_editable_period:
            fail("matchup_projection_missing", "Matchup projection was missing despite no explicit pause state")
    else:
        if projection.get("scoring_basis") != "current_snapshot_fppg_x_remaining_games":
            fail("matchup_scoring_basis", "Matchup projection did not identify its FP/G scoring basis")
        if projection.get("probability_calibrated") is not False:
            fail("matchup_probability_claim", "Matchup win probability was not explicitly marked uncalibrated")
        if not isinstance(projection.get("complete"), bool):
            fail("matchup_completion_state", "Matchup projection completion state was not explicit")
        for field in ("projected_my", "projected_opp"):
            if _number(projection.get(field)) is None:
                fail("matchup_projection_value", f"Matchup projection {field} was not finite")
        probability = _number(projection.get("win_probability"))
        if probability is None or not 0 <= probability <= 1:
            fail("matchup_probability_range", "Matchup win probability was outside 0..1")
        for field in ("my_remaining_games", "opp_remaining_games"):
            value = _number(projection.get(field))
            if value is None or value < 0:
                fail("matchup_game_volume", f"Matchup projection {field} was invalid")
        opportunity_completeness = projection.get("opportunity_completeness")
        if opportunity_completeness is not None:
            if opportunity_completeness not in {"complete", "known_opportunities_lower_bound"}:
                fail("matchup_opportunity_scope", "Matchup projection had an unknown opportunity-completeness state")
            if opportunity_completeness == "known_opportunities_lower_bound":
                missing_probables = _number(projection.get("pitchers_without_probable_start"))
                if missing_probables is None or missing_probables <= 0:
                    fail("matchup_opportunity_scope", "Lower-bound projection did not disclose omitted pitcher opportunities")

    block = matchup.get("recommendations")
    recommendations = block.get("recommendations") if isinstance(block, dict) else None
    if not isinstance(recommendations, list):
        fail("matchup_recommendations_invalid", "Matchup recommendations were not a list")
        recommendations = []
    if isinstance(block, dict) and isinstance(projection, dict):
        if block.get("model_version") != projection.get("model_version"):
            fail("matchup_model_mismatch", "Projection and recommendation model versions did not match")
        if projection.get("probability_calibrated") is False:
            thresholds = block.get("thresholds") if isinstance(block.get("thresholds"), dict) else {}
            if (
                thresholds.get("probability_calibrated") is not False
                or thresholds.get("win_probability_delta") is not None
            ):
                fail(
                    "matchup_uncalibrated_action_claim",
                    "Matchup recommendations used a probability threshold before calibration",
                )

    proposal_entries: list[dict[str, Any]] = []
    seen_outcomes: set[tuple[str, str]] = set()
    unchecked_movability_count = 0
    base_projection = block.get("base_projection") if isinstance(block, dict) else None
    base_projected_my = _number(base_projection.get("projected_my")) if isinstance(base_projection, dict) else None
    previous_points: float | None = None
    for index, recommendation in enumerate(recommendations):
        if not isinstance(recommendation, dict):
            fail("matchup_recommendation_invalid", f"Matchup recommendation {index + 1} was not an object")
            continue
        if recommendation.get("rank") != index + 1:
            fail("matchup_rank_order", "Matchup recommendation ranks were not contiguous")
        points = _number(recommendation.get("points_delta"))
        win_delta = _number(recommendation.get("win_probability_delta"))
        probability_calibrated = recommendation.get("probability_calibrated") is True
        if points is None or points <= 0:
            fail("matchup_nonpositive_recommendation", f"Matchup recommendation {index + 1} had no positive edge")
        if isinstance(projection, dict) and projection.get("probability_calibrated") is False:
            if (
                probability_calibrated
                or recommendation.get("win_probability_delta") is not None
                or recommendation.get("confidence_basis") != "projected_points_magnitude"
            ):
                fail(
                    "matchup_uncalibrated_action_claim",
                    f"Matchup recommendation {index + 1} exposed an uncalibrated probability edge",
                )
        elif win_delta is None or win_delta < 0:
            fail("matchup_nonpositive_recommendation", f"Matchup recommendation {index + 1} had no positive edge")
        if previous_points is not None and points is not None and points > previous_points + 0.05:
            fail("matchup_rank_order", "Matchup recommendations were not ordered by projected point edge")
        if points is not None:
            previous_points = points

        card = recommendation.get("replacement_card")
        if not isinstance(card, dict):
            fail("matchup_card_missing", f"Matchup recommendation {index + 1} had no replacement card")
            continue
        if (
            isinstance(projection, dict)
            and projection.get("probability_calibrated") is False
            and card.get("confidence_basis") != "projected_points_magnitude"
        ):
            fail(
                "matchup_uncalibrated_action_claim",
                f"Matchup recommendation {index + 1} card mislabeled point-edge strength as confidence",
            )
        proposal_entries.append({"proposal": card.get("proposal")})
        move_in = card.get("move_in") if isinstance(card.get("move_in"), dict) else {}
        move_out = card.get("move_out") if isinstance(card.get("move_out"), dict) else {}
        move_in_status = str(move_in.get("injury") or move_in.get("status") or "").strip().upper()
        if move_in.get("unavailable") is True or move_in_status in {
            "OUT", "SUSP", "SUSPENDED", "IL", "IL10", "IL60", "IR",
        }:
            fail(
                "matchup_unavailable_move_in",
                f"Matchup recommendation {index + 1} promoted an unavailable player",
            )
        outcome = (str(move_in.get("id") or ""), str(move_out.get("id") or ""))
        if not all(outcome):
            fail("matchup_outcome_identity", f"Matchup recommendation {index + 1} lacked player identities")
        elif outcome in seen_outcomes:
            fail("matchup_dominated_duplicate", "Matchup recommendations repeated the same active-in/active-out outcome")
        else:
            seen_outcomes.add(outcome)

        benefit = card.get("projected_benefit") if isinstance(card.get("projected_benefit"), dict) else {}
        if isinstance(projection, dict) and projection.get("probability_calibrated") is False:
            if (
                benefit.get("probability_calibrated") is not False
                or benefit.get("win_probability_delta") is not None
                or benefit.get("base_win_probability") is not None
                or benefit.get("new_win_probability") is not None
            ):
                fail(
                    "matchup_uncalibrated_action_claim",
                    f"Matchup recommendation {index + 1} card exposed uncalibrated probability evidence",
                )
        new_projected_my = _number(benefit.get("new_projected_my"))
        if base_projected_my is not None and new_projected_my is not None and points is not None:
            if abs((new_projected_my - base_projected_my) - points) > 0.11:
                fail("matchup_benefit_math", f"Matchup recommendation {index + 1} point benefit did not reconcile")

        proposal = card.get("proposal") if isinstance(card.get("proposal"), dict) else {}
        contract = proposal.get("contract") if isinstance(proposal.get("contract"), dict) else {}
        contract_benefit = contract.get("projected_benefit") if isinstance(contract.get("projected_benefit"), dict) else {}
        if isinstance(projection, dict) and projection.get("probability_calibrated") is False:
            if (
                contract_benefit.get("probability_calibrated") is not False
                or contract_benefit.get("win_probability_delta") is not None
            ):
                fail(
                    "matchup_uncalibrated_action_claim",
                    f"Matchup recommendation {index + 1} contract exposed an uncalibrated probability edge",
                )
        slot_moves = contract.get("slot_moves") if isinstance(contract.get("slot_moves"), list) else []
        slot_move_ids = {str(move.get("player_id") or "") for move in slot_moves if isinstance(move, dict)} - {""}
        movability = card.get("movability") if isinstance(card.get("movability"), dict) else {}
        participants = movability.get("participants") if isinstance(movability.get("participants"), dict) else {}
        participant_ids = {
            str(participant.get("id") or "")
            for participant in participants.values()
            if isinstance(participant, dict)
        } - {""}
        if slot_move_ids and participant_ids != slot_move_ids:
            unchecked_movability_count += 1

    _validate_read_only_proposals(proposal_entries, snapshot_id, "matchup", fail)
    if unchecked_movability_count:
        fail(
            "matchup_movability_coverage",
            f"{unchecked_movability_count} matchup recommendation(s) did not evaluate every slot-move participant",
        )
    return {
        "name": "matchup",
        "state": "ready" if isinstance(projection, dict) else "paused",
        "recommendation_count": len(recommendations),
        "model_version": projection.get("model_version") if isinstance(projection, dict) else None,
    }


def _validate_win_this_week(
    plan: dict[str, Any],
    snapshot_id: str | None,
    fail,
    *,
    prefix: str,
    now: datetime,
) -> dict[str, Any]:
    _require_matching_snapshot_id(prefix, plan, snapshot_id, fail)
    if plan.get("read_only") is not True or plan.get("writes_enabled") is not False:
        fail(f"{prefix}_write_boundary", "Win This Week did not explicitly remain read-only")
    matchup_context = plan.get("matchup") if isinstance(plan.get("matchup"), dict) else {}
    if matchup_context.get("opportunity_completeness") == "known_opportunities_lower_bound":
        summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
        if not summary.get("projection_caveat") or matchup_context.get("probability_calibrated") is True:
            fail(f"{prefix}_opportunity_scope", "Win This Week did not disclose its lower-bound pitcher assumption")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        fail(f"{prefix}_actions_invalid", "Win This Week actions field was not a list")
        actions = []
    current_period = plan.get("current_period") if isinstance(plan.get("current_period"), dict) else {}
    planning_horizon = plan.get("planning_horizon") if isinstance(plan.get("planning_horizon"), dict) else {}
    handoffs = plan.get("handoffs") if isinstance(plan.get("handoffs"), dict) else {}
    lineup_handoff = handoffs.get("lineup") if isinstance(handoffs.get("lineup"), dict) else {}
    if planning_horizon.get("mode") == "editable_period":
        expected_target = {
            key: planning_horizon.get(key)
            for key in ("period_number", "start", "end", "matchup_key")
        }
        invalid_actions = [
            action
            for action in actions
            if not isinstance(action, dict)
            or action.get("kind") not in {"lineup", "lineup_plan"}
            or action.get("target_period") != expected_target
            or (
                action.get("kind") == "lineup"
                and (
                    not isinstance(action.get("review"), dict)
                    or action.get("review", {}).get("state") != "reviewable"
                    or action.get("review", {}).get("target_period") != expected_target
                    or action.get("review", {}).get("writes_enabled") is not False
                    or (action.get("review", {}).get("contract") or {}).get("target_period") != expected_target
                    or action.get("review", {}).get("proposal_id") != (action.get("review", {}).get("contract") or {}).get("proposal_id")
                    or action.get("review", {}).get("snapshot_id") != (action.get("review", {}).get("contract") or {}).get("snapshot_id")
                    or action.get("review", {}).get("input_hash") != (action.get("review", {}).get("contract") or {}).get("input_hash")
                    or action.get("review", {}).get("slot_moves") != (action.get("review", {}).get("contract") or {}).get("slot_moves")
                )
            )
            or (
                action.get("kind") == "lineup_plan"
                and (
                    not isinstance(action.get("review"), dict)
                    or action.get("review", {}).get("state") != "unavailable"
                    or action.get("review", {}).get("writes_enabled") is not False
                )
            )
        ]
        handoff_target = lineup_handoff.get("target_period")
        monitoring = plan.get("monitoring_actions") if isinstance(plan.get("monitoring_actions"), list) else []
        has_waiver_boundary = any(
            isinstance(item, dict)
            and item.get("id") == "monitor:future-period-waiver-boundary"
            and item.get("state") == "blocked"
            for item in monitoring
        )
        if invalid_actions or (lineup_handoff and handoff_target != expected_target) or not has_waiver_boundary:
            fail(
                f"{prefix}_planning_horizon",
                "Future-period plan was not lineup-only and bound to its exact target period",
            )
    if current_period.get("state") != "ok" and (actions or lineup_handoff.get("url")):
        fail(
            f"{prefix}_period_alignment",
            "Win This Week exposed actions or a lineup handoff without an aligned editable Fantrax period",
        )
    matchup_complete = matchup_context.get("complete") is True
    if plan.get("state") == "complete" and not matchup_complete:
        fail(
            f"{prefix}_matchup_state",
            "Win This Week claimed completion without a complete matchup context",
        )
    period_gate_applies = current_period.get("state") != "ok" and not matchup_complete
    if period_gate_applies and plan.get("state") != "paused":
        fail(
            f"{prefix}_period_alignment",
            "Win This Week did not pause without an aligned editable Fantrax period",
        )
    if period_gate_applies:
        monitoring = plan.get("monitoring_actions") if isinstance(plan.get("monitoring_actions"), list) else []
        expected_monitor_state = "blocked" if current_period.get("state") == "mismatch" else "needs_refresh"
        if not any(
            isinstance(item, dict)
            and item.get("id") == "monitor:current-period-alignment"
            and item.get("state") == expected_monitor_state
            for item in monitoring
        ):
            fail(
                f"{prefix}_period_alignment",
                "Win This Week did not expose the required editable-period monitor",
            )
    probability_calibrated = ((plan.get("diagnostics") or {}).get("probability_calibrated") is True)
    previous_points: float | None = None
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            fail(f"{prefix}_action_invalid", f"Win This Week action {index + 1} was not an object")
            continue
        if action.get("rank") != index + 1:
            fail(f"{prefix}_rank_order", "Win This Week ranks were not contiguous")
        points_block = action.get("expected_points") if isinstance(action.get("expected_points"), dict) else {}
        points = _number(points_block.get("estimate"))
        if points is None or points <= 0 or points_block.get("comparable") is not True:
            fail(f"{prefix}_impact_invalid", f"Win This Week action {index + 1} lacked comparable positive impact")
        if previous_points is not None and points is not None and points > previous_points + 0.05:
            fail(f"{prefix}_rank_order", "Win This Week actions were not ordered by expected points")
        if points is not None:
            previous_points = points
        deadline = action.get("deadline") if isinstance(action.get("deadline"), dict) else {}
        if deadline.get("state") != "known" or not deadline.get("at"):
            fail(f"{prefix}_deadline_missing", f"Win This Week action {index + 1} lacked an exact deadline")
        else:
            deadline_at = _parse_datetime(deadline.get("at"))
            if deadline_at is None:
                fail(f"{prefix}_deadline_invalid", f"Win This Week action {index + 1} had an invalid deadline")
            elif deadline_at <= now:
                fail(f"{prefix}_deadline_expired", f"Win This Week action {index + 1} remained actionable after its deadline")
        dynasty = action.get("dynasty_cost") if isinstance(action.get("dynasty_cost"), dict) else {}
        if dynasty.get("level") not in {"none", "low", "medium", "high", "unknown"}:
            fail(f"{prefix}_dynasty_cost_missing", f"Win This Week action {index + 1} lacked dynasty cost")
        legality = action.get("legality") if isinstance(action.get("legality"), dict) else {}
        if legality.get("state") not in {"snapshot_verified", "provisionally_legal"}:
            fail(f"{prefix}_legality_missing", f"Win This Week action {index + 1} lacked a legal-path state")
        if action.get("writes_enabled") is not False:
            fail(f"{prefix}_write_boundary", f"Win This Week action {index + 1} did not keep writes disabled")
        if not probability_calibrated and action.get("win_probability_delta") is not None:
            fail(f"{prefix}_probability_claim", "Win This Week exposed an uncalibrated probability delta")
        for step in action.get("steps") or []:
            if not isinstance(step, dict) or step.get("action") != "move_out":
                continue
            move_out_name = " ".join(str(step.get("player_name") or "").split()).casefold()
            if move_out_name in NEVER_DROP_PLAYER_NAMES:
                fail(f"{prefix}_protected_anchor", "Win This Week attempted to move out an owner-protected anchor")
    primary_id = plan.get("primary_action_id")
    expected_primary = actions[0].get("id") if actions and isinstance(actions[0], dict) else None
    if primary_id != expected_primary:
        fail(f"{prefix}_primary_mismatch", "Win This Week primary action did not match rank 1")
    if actions and isinstance(actions[0], dict):
        summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
        before = _number(summary.get("projected_margin_before_action"))
        after = _number(summary.get("projected_margin_after_action"))
        primary_points = _number(((actions[0].get("expected_points") or {}).get("estimate")))
        if before is not None and primary_points is not None:
            if after is None or abs(after - round(before + primary_points, 1)) > 0.05:
                fail(
                    f"{prefix}_outlook_math",
                    "Win This Week post-action projected margin did not equal the base margin plus primary impact",
                )
            if not str(summary.get("outlook") or "").strip():
                fail(f"{prefix}_outlook_missing", "Win This Week did not explain the post-action matchup outlook")
        elif after is not None:
            fail(f"{prefix}_outlook_math", "Win This Week exposed a post-action margin without comparable inputs")
    if any(
        isinstance(action, dict) and action.get("kind") in {"lineup", "lineup_plan"}
        for action in actions
    ):
        url = str(lineup_handoff.get("url") or "")
        if (
            lineup_handoff.get("method") != "GET"
            or lineup_handoff.get("read_only") is not True
            or lineup_handoff.get("writes_enabled") is not False
            or not url.startswith("https://www.fantrax.com/fantasy/league/")
            or "/team/roster;teamId=" not in url
        ):
            fail(
                f"{prefix}_lineup_handoff",
                "Win This Week lineup action lacked a verified read-only Fantrax roster handoff",
            )
    if plan.get("state") == "no_action":
        no_action = plan.get("no_action") if isinstance(plan.get("no_action"), dict) else {}
        if not str(no_action.get("reason") or "").strip():
            fail(f"{prefix}_no_action_reason", "Win This Week no-action state did not explain why")
        alternatives = no_action.get("alternatives")
        if not isinstance(alternatives, list):
            fail(
                f"{prefix}_no_action_alternatives",
                "Win This Week no-action state did not expose the alternatives it considered",
            )
            alternatives = []
        for index, alternative in enumerate(alternatives):
            if not isinstance(alternative, dict):
                fail(f"{prefix}_no_action_alternative_invalid", f"No-action alternative {index + 1} was not an object")
                continue
            if not str(alternative.get("title") or "").strip() or not str(alternative.get("reason") or "").strip():
                fail(
                    f"{prefix}_no_action_alternative_invalid",
                    f"No-action alternative {index + 1} lacked a title or rejection reason",
                )
            points_block = alternative.get("expected_points") if isinstance(alternative.get("expected_points"), dict) else {}
            estimate = points_block.get("estimate")
            if estimate is not None and (_number(estimate) is None or points_block.get("comparable") is not True):
                fail(
                    f"{prefix}_no_action_alternative_invalid",
                    f"No-action alternative {index + 1} exposed a non-comparable impact",
                )
            for step in alternative.get("steps") or []:
                if not isinstance(step, dict) or step.get("action") != "move_out":
                    continue
                move_out_name = " ".join(str(step.get("player_name") or "").split()).casefold()
                if move_out_name in NEVER_DROP_PLAYER_NAMES:
                    fail(
                        f"{prefix}_protected_anchor",
                        "Win This Week exposed an owner-protected anchor in a rejected move-out alternative",
                    )
    return {
        "name": prefix,
        "state": plan.get("state"),
        "action_count": len(actions),
        "monitoring_count": len(plan.get("monitoring_actions")) if isinstance(plan.get("monitoring_actions"), list) else None,
        "writes_enabled": plan.get("writes_enabled"),
    }


def _win_plan_signature(plan: dict[str, Any]) -> tuple[Any, ...]:
    actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    handoffs = plan.get("handoffs") if isinstance(plan.get("handoffs"), dict) else {}
    lineup_handoff = handoffs.get("lineup") if isinstance(handoffs.get("lineup"), dict) else {}
    no_action = plan.get("no_action") if isinstance(plan.get("no_action"), dict) else {}
    alternatives = no_action.get("alternatives") if isinstance(no_action.get("alternatives"), list) else []
    return (
        plan.get("snapshot_id"),
        plan.get("state"),
        plan.get("primary_action_id"),
        summary.get("headline"),
        summary.get("outlook"),
        summary.get("projected_margin_before_action"),
        summary.get("projected_margin_after_action"),
        lineup_handoff.get("url"),
        lineup_handoff.get("method"),
        lineup_handoff.get("read_only"),
        lineup_handoff.get("writes_enabled"),
        tuple(
            (
                action.get("id"),
                action.get("rank"),
                action.get("kind"),
                ((action.get("expected_points") or {}).get("estimate")),
                ((action.get("deadline") or {}).get("at")),
                tuple(
                    (
                        step.get("action"),
                        step.get("player_id"),
                        step.get("from_slot"),
                        step.get("to_slot"),
                    )
                    for step in action.get("steps") or []
                    if isinstance(step, dict)
                ),
            )
            for action in actions
            if isinstance(action, dict)
        ),
        no_action.get("reason"),
        tuple(
            (
                alternative.get("id"),
                alternative.get("kind"),
                alternative.get("title"),
                alternative.get("status"),
                ((alternative.get("expected_points") or {}).get("estimate")),
                alternative.get("reason"),
                tuple(
                    (
                        step.get("action"),
                        step.get("player_id"),
                        step.get("from_slot"),
                        step.get("to_slot"),
                    )
                    for step in alternative.get("steps") or []
                    if isinstance(step, dict)
                ),
            )
            for alternative in alternatives
            if isinstance(alternative, dict)
        ),
    )


def _valid_age(value: Any) -> bool:
    parsed = _number(value)
    return parsed is not None and parsed.is_integer() and 16 <= parsed <= 50


def _trusted_age_source(value: Any) -> bool:
    source = str(value or "").strip().casefold()
    return bool(source) and not any(token in source for token in ("unknown", "fallback", "inferred", "legacy"))


def _valid_fppg(value: Any) -> bool:
    parsed = _number(value)
    return parsed is not None and abs(parsed) <= 100


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


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
