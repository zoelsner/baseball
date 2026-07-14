"""FastAPI app for Sandlot v1."""

from __future__ import annotations

import json
import logging
import math
import os
import hashlib
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import player_service
import sandlot_attention
import sandlot_config
import sandlot_data_quality
import sandlot_db
import sandlot_execution
import sandlot_matchup
import sandlot_receipts
import sandlot_skipper
import sandlot_trades
import sandlot_waivers
import sandlot_win_week

log = logging.getLogger(__name__)

load_dotenv()

WEB_DIR = Path(__file__).parent / "web" / "sandlot"
FRESH_SNAPSHOT_MINUTES = 18 * 60
OLD_SNAPSHOT_MINUTES = 36 * 60
TRADE_RESEARCH_HEARTBEAT_SECONDS = 12.0

app = FastAPI(title="Sandlot", version="0.1.0")


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class ExecutionTargetPeriod(StrictModel):
    period_number: int
    matchup_key: str | int | None = None
    start: str | None = None
    end: str | None = None


class ExecutionSlotMove(StrictModel):
    order: int
    player_id: str
    player_name: str
    from_slot: str
    to_slot: str


class ExactExecutionConfirmation(StrictModel):
    proposal_id: str
    input_hash: str
    snapshot_id: int
    target_period: ExecutionTargetPeriod
    slot_moves: list[ExecutionSlotMove]


class ExecutionRequestCreate(StrictModel):
    mode: Literal["dry_run"] = "dry_run"
    proposal_id: str
    snapshot_id: int
    input_hash: str
    confirmation: ExactExecutionConfirmation


class RecommendationDecisionIn(StrictModel):
    decision: Literal["accepted", "rejected"]
    input_hash: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    reason: str | None = Field(default=None, max_length=240)


class ExecutionClaim(StrictModel):
    runner_id: str = Field(min_length=1, max_length=80)


class PreflightCheck(StrictModel):
    key: str
    state: Literal["passed", "failed"]
    detail: str = ""


class ExecutionPreflightResult(StrictModel):
    lease_token: str = Field(min_length=16, max_length=256)
    outcome: Literal["passed", "failed"]
    checks: list[PreflightCheck]
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime
    writes_attempted: bool


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]):
        response = await super().get_response(path, scope)
        if path in {"app.js", "index.html", ""}:
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


@app.on_event("startup")
def startup() -> None:
    if os.environ.get("DATABASE_URL"):
        sandlot_db.init_schema()


@app.get("/api/health")
def health() -> dict[str, Any]:
    db_ok = False
    latest = None
    latest_run = None
    error = None
    try:
        sandlot_db.init_schema()
        db_ok = True
        latest = sandlot_db.latest_successful_snapshot()
        latest_run = sandlot_db.latest_refresh_run()
    except Exception as exc:
        error = str(exc)

    taken_at = latest.get("taken_at") if latest else None
    return jsonable_encoder(
        {
            "ok": db_ok,
            "database": "ok" if db_ok else "error",
            "latest_successful_snapshot_at": taken_at,
            "freshness": _freshness(taken_at),
            "latest_refresh_run": _run_summary(latest_run),
            "error": error,
        }
    )


@app.get("/api/snapshot/latest")
def latest_snapshot() -> dict[str, Any]:
    try:
        row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc

    if not row:
        raise HTTPException(status_code=404, detail="No successful Fantrax snapshot has been stored yet")
    return jsonable_encoder(_snapshot_payload(row))


@app.get("/api/attention")
def attention_queue() -> dict[str, Any]:
    """Machine-readable Attention Queue (#64) — read-only.

    Same ordered queue the Today page renders, derived from the latest
    successful snapshot, so external agents (Zo) and the UI share one source
    of truth. Writes stay in POST /api/actions.
    """
    try:
        row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    if not row:
        raise HTTPException(status_code=503, detail="No successful Fantrax snapshot has been stored yet")
    taken_at = row.get("taken_at")
    previous_row = None
    try:
        previous_row = sandlot_db.previous_successful_snapshot(
            before_id=int(row.get("id")),
            before_taken_at=taken_at,
        )
    except Exception:
        log.exception("Previous snapshot lookup failed")
    current_data = _persisted_snapshot_data(row)
    previous_data = _persisted_snapshot_data(previous_row) if previous_row else None
    return jsonable_encoder(
        {
            "snapshot_id": row.get("id"),
            "previous_snapshot_id": previous_row.get("id") if previous_row else None,
            "taken_at": taken_at,
            "freshness": _freshness(taken_at),
            "items": sandlot_attention.attention_items(current_data),
            "changes": sandlot_attention.status_change_items(current_data, previous_data),
        }
    )


@app.get("/api/hot-swaps/latest")
def latest_hot_swaps() -> dict[str, Any]:
    """Read-only hot-swap proposal surface.

    This endpoint intentionally derives from the same Attention Queue contract
    so it inherits the fail-closed lineup-slot provenance gate. It never emits
    an executable Fantrax action payload.
    """
    try:
        row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    if not row:
        raise HTTPException(status_code=503, detail="No successful Fantrax snapshot has been stored yet")
    return jsonable_encoder(_hot_swap_payload(row))


@app.get("/api/win-this-week/latest")
def latest_win_this_week() -> dict[str, Any]:
    """Read-only ranked plan for maximizing the current matchup."""
    try:
        row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    if not row:
        raise HTTPException(status_code=404, detail="No successful Fantrax snapshot has been stored yet")
    return jsonable_encoder(_matchup_decisions(row)["win_this_week"])


@app.get("/api/recommendation-receipts/latest", response_model=None)
def latest_recommendation_receipt(source: Literal["monday_lineup"] = "monday_lineup") -> dict[str, Any] | Response:
    try:
        row = sandlot_db.latest_active_recommendation_receipt(source=source)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Recommendation receipt unavailable: {exc}") from exc
    if not row:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return jsonable_encoder(_public_recommendation_receipt(row, _latest_reconciliation_snapshot()))


@app.get("/api/recommendation-outcomes/recent")
def recent_recommendation_outcomes(
    source: Literal["monday_lineup"] = "monday_lineup",
    limit: int = 20,
) -> dict[str, Any]:
    """Expose labeled forecast telemetry without claiming realized lineup gain."""
    try:
        rows = sandlot_db.recent_scored_recommendation_receipts(source=source, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Recommendation outcomes unavailable: {exc}") from exc
    return jsonable_encoder({
        "scoring_version": "team_result_v1",
        "measurement_scope": "observed_team_total",
        "counterfactual_gain_available": False,
        "autopilot_eligible": False,
        "items": [
            {
                "receipt_id": row.get("receipt_id"),
                "period": {"start": row.get("period_start"), "end": row.get("period_end")},
                "decision_state": row.get("decision_state"),
                "projected_team_total": row.get("projected_value"),
                "outcome": _public_recommendation_outcome(row),
            }
            for row in rows
        ],
    })


@app.get("/api/recommendation-learning")
def recommendation_learning(
    source: Literal["monday_lineup"] = "monday_lineup",
) -> dict[str, Any]:
    """Expose sanitized counterfactual learning without authorizing automation."""
    try:
        report = sandlot_db.recommendation_outcome_evaluation_report(
            source=source,
            scoring_version=sandlot_receipts.COUNTERFACTUAL_LINEUP_SCORING_VERSION,
            detail_limit=8,
        )
    except Exception as exc:
        log.exception("Recommendation learning report failed")
        raise HTTPException(status_code=503, detail="Recommendation learning is temporarily unavailable") from exc
    return jsonable_encoder(_public_recommendation_learning(report))


@app.get("/api/matchup-probability-readiness")
def matchup_probability_readiness() -> dict[str, Any]:
    """Expose calibration evidence without activating probability product claims."""
    try:
        rows = sandlot_db.list_projection_logs_for_calibration()
        report = sandlot_matchup.calibration_report(rows)
        snapshot_row = sandlot_db.latest_successful_snapshot()
        plan = _matchup_decisions(dict(snapshot_row))["win_this_week"] if snapshot_row else {}
    except Exception as exc:
        log.exception("Matchup probability readiness report failed")
        raise HTTPException(
            status_code=503, detail="Matchup probability readiness is temporarily unavailable"
        ) from exc
    group = next(
        (
            item for item in report.get("groups") or []
            if item.get("model_version") == sandlot_matchup.MODEL_VERSION
            and item.get("surface") == "api"
        ),
        None,
    )
    readiness = report.get("release_readiness") or {}
    current = plan.get("matchup") if isinstance(plan.get("matchup"), dict) else {}
    unknown_pitchers = max(0, int(current.get("pitchers_without_probable_start") or 0))
    estimated_pitchers = max(0, int(current.get("pitchers_with_cadence_estimate") or 0))
    unmodeled_pitchers = max(0, int(current.get("pitchers_without_opportunity_model") or 0))
    opportunity_complete = (
        current.get("opportunity_completeness") == "complete" and unknown_pitchers == 0
    )
    current_reasons = []
    if not current:
        current_reasons.append("current_forecast_unavailable")
    elif not opportunity_complete:
        current_reasons.append("current_pitcher_opportunity_coverage_incomplete")
    evidence_band_ready = readiness.get("state") == "band_ready"
    applicability_reasons = list(readiness.get("reasons") or []) + current_reasons
    applicability_state = (
        "eligible_for_separate_release_review"
        if evidence_band_ready and opportunity_complete
        else "withheld"
    )
    return jsonable_encoder({
        "model_version": sandlot_matchup.MODEL_VERSION,
        "state": readiness.get("state") or "collecting",
        "probability_calibrated": False,
        "sample_unit": "unique_matchup",
        "forecast_row_count": int((group or {}).get("forecast_row_count") or 0),
        "labeled_row_count": int((group or {}).get("labeled_row_count") or 0),
        "eligible_matchup_count": int((group or {}).get("eligible_matchup_count") or 0),
        "independent_matchup_count": int((group or {}).get("independent_matchup_count") or 0),
        "actual_coverage": (group or {}).get("actual_coverage") or 0.0,
        "opportunity_cohorts": (group or {}).get("opportunity_cohorts") or {},
        "metrics": (group or {}).get("metrics") or {},
        "readiness": readiness,
        "current_forecast": {
            "planning_horizon": plan.get("planning_horizon") or {},
            "opportunity_completeness": current.get("opportunity_completeness"),
            "active_pitchers": int(current.get("active_pitchers") or 0),
            "pitchers_using_posted_probable_only": int(current.get("pitchers_using_posted_probable_only") or 0),
            "pitchers_without_probable_start": unknown_pitchers,
            "pitchers_with_cadence_estimate": estimated_pitchers,
            "pitchers_without_opportunity_model": unmodeled_pitchers,
        },
        "current_applicability": {
            "state": applicability_state,
            "evidence_band_ready": evidence_band_ready,
            "opportunity_complete": opportunity_complete,
            "reasons": list(dict.fromkeys(applicability_reasons)),
        },
        "product_activation": {
            "state": "locked",
            "requires_separate_reviewed_release": True,
            "precise_probability": False,
            "action_probability_delta": False,
            "autopilot_eligible": False,
        },
    })


@app.post("/api/recommendation-receipts/{receipt_id}/decision")
def decide_recommendation_receipt(
    receipt_id: str,
    payload: RecommendationDecisionIn,
    request: Request,
) -> dict[str, Any]:
    """Record owner intent only; this route never executes a Fantrax action."""
    _require_hashed_role(request, "SANDLOT_OWNER_ACTION_TOKEN_SHA256")
    reason = " ".join(str(payload.reason or "").split()) or None
    try:
        row, changed = sandlot_db.decide_recommendation_receipt(
            receipt_id=receipt_id,
            input_hash=payload.input_hash.lower(),
            decision=payload.decision,
            source="owner_bridge",
            reason=reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Recommendation decision unavailable: {exc}") from exc
    result = _public_recommendation_receipt(row, _latest_reconciliation_snapshot())
    result["changed"] = changed
    result["fantrax_changed"] = False
    result["writes_enabled"] = False
    return jsonable_encoder(result)


@app.get("/api/action-proposals/{proposal_id}")
def latest_action_proposal(
    proposal_id: str,
    snapshot_id: int,
    input_hash: str,
) -> dict[str, Any]:
    """Return one server-derived immutable action review; never execute it."""
    row, action, review = _latest_reviewed_action(proposal_id, snapshot_id, input_hash)
    return jsonable_encoder({
        "snapshot_id": row.get("id"),
        "taken_at": row.get("taken_at"),
        "freshness": _freshness(row.get("taken_at")),
        "is_current": True,
        "read_only": True,
        "writes_enabled": False,
        "action": action,
        "review": review,
        "execution": {
            "state": "offline",
            "request_enabled": False,
            "reason": "A trusted local headful runner and owner authentication are required before execution requests can be created.",
        },
    })


@app.post("/api/execution-requests", status_code=201)
def create_execution_request(
    payload: ExecutionRequestCreate,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Create one authenticated, immutable, dry-run-only preflight request."""
    _require_execution_role(request, "SANDLOT_OWNER_ACTION_TOKEN_SHA256")
    submitted = payload.model_dump()
    row, action, _review = _latest_reviewed_action(
        payload.proposal_id,
        payload.snapshot_id,
        payload.input_hash,
    )
    try:
        prepared = sandlot_execution.prepare_dry_run_request(
            snapshot_row=row,
            action=action,
            submitted=submitted,
        )
        stored, created = sandlot_db.create_execution_request(prepared)
    except sandlot_execution.ExecutionContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Execution request unavailable: {exc}") from exc
    result = sandlot_execution.public_request(stored, include_contract=False)
    result["created"] = created
    result["request_enabled"] = stored.get("state") in {"pending", "claimed"}
    if not created:
        response.status_code = 200
    return jsonable_encoder(result)


@app.get("/api/execution-requests/{request_id}")
def execution_request_status(request_id: str, request: Request) -> dict[str, Any]:
    _require_execution_role(request, "SANDLOT_OWNER_ACTION_TOKEN_SHA256")
    try:
        row = sandlot_db.execution_request_by_id(request_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Execution request unavailable: {exc}") from exc
    if not row:
        raise HTTPException(status_code=404, detail="Execution request not found")
    return jsonable_encoder(sandlot_execution.public_request(row, include_contract=False))


@app.post("/api/execution-requests/claim")
def claim_execution_request(payload: ExecutionClaim, request: Request) -> dict[str, Any]:
    """Atomically claim one request for the separately authenticated local runner."""
    _require_execution_role(request, "SANDLOT_RUNNER_TOKEN_SHA256")
    lease_token, lease_hash = sandlot_execution.new_lease()
    try:
        row = sandlot_db.claim_next_execution_request(
            runner_id=payload.runner_id,
            lease_token_hash=lease_hash,
            lease_seconds=sandlot_execution.LEASE_TTL_SECONDS,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Execution claim unavailable: {exc}") from exc
    if not row:
        return {"request": None, "writes_enabled": False}
    claimed = sandlot_execution.public_request(row, include_contract=True)
    claimed["lease_token"] = lease_token
    claimed["lease_expires_at"] = row.get("lease_expires_at")
    return jsonable_encoder({"request": claimed, "writes_enabled": False})


@app.post("/api/execution-requests/{request_id}/preflight")
def finish_execution_preflight(
    request_id: str,
    payload: ExecutionPreflightResult,
    request: Request,
) -> dict[str, Any]:
    _require_execution_role(request, "SANDLOT_RUNNER_TOKEN_SHA256")
    try:
        claimed = sandlot_db.execution_request_by_id(request_id)
        if not claimed or claimed.get("state") != "claimed":
            raise HTTPException(
                status_code=409,
                detail="Request is not currently claimed or is already terminal.",
            )
        report = sandlot_execution.validate_preflight_report(
            payload.model_dump(exclude={"lease_token"}),
            request_row=claimed,
        )
        failure_reason = None
        if report["outcome"] == "failed":
            failure_reason = next(
                (
                    f"Live preflight failed: {check['key']}"
                    for check in report["checks"]
                    if check["state"] == "failed"
                ),
                "Live preflight failed",
            )
        row = sandlot_db.finish_execution_preflight(
            request_id=request_id,
            lease_token_hash=sandlot_execution.token_digest(payload.lease_token),
            outcome=report["outcome"],
            evidence=report,
            failure_reason=failure_reason,
        )
    except sandlot_execution.ExecutionContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Preflight result unavailable: {exc}") from exc
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Request is not claimed by this live lease, has expired, or is already terminal.",
        )
    return jsonable_encoder(sandlot_execution.public_request(row, include_contract=False))


@app.post("/api/refresh")
def refresh(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    from sandlot_refresh import run_refresh

    _require_refresh_token(request)
    result = run_refresh(source="manual")
    row = sandlot_db.latest_successful_snapshot()
    if result.status == "skipped":
        if not row:
            raise HTTPException(
                status_code=409,
                detail={
                    "status": result.status,
                    "snapshot_id": None,
                    "duration_ms": result.duration_ms,
                    "errors": result.errors,
                    "fallback": False,
                },
            )
        return jsonable_encoder(
            {
                "status": result.status,
                "snapshot_id": row.get("id"),
                "duration_ms": result.duration_ms,
                "fallback_reason": "Refresh already running; showing the latest successful Fantrax snapshot.",
                "snapshot": _snapshot_payload(row),
            }
        )
    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "status": result.status,
                "snapshot_id": result.snapshot_id,
                "duration_ms": result.duration_ms,
                "errors": result.errors,
                "fallback": bool(row),
                "fallback_reason": "Showing latest successful Fantrax snapshot because refresh failed.",
                "snapshot": _snapshot_payload(row) if row else None,
            },
        )

    if result.snapshot_id and sandlot_config.waiver_ai_warm_enabled():
        background_tasks.add_task(sandlot_waivers.warm_latest_waiver_ai, result.snapshot_id)
    # Optional post-refresh warmups are intentionally opt-in; the refresh itself
    # should stay focused on producing one fresh Fantrax snapshot.
    if result.snapshot_id and sandlot_config.profile_warm_enabled():
        background_tasks.add_task(
            player_service.warm_roster_profiles,
            snapshot_id=result.snapshot_id,
            generate_takes=os.environ.get("SANDLOT_PROFILE_WARM_TAKES") == "1",
        )
    return jsonable_encoder(
        {
            "status": result.status,
            "snapshot_id": result.snapshot_id,
            "duration_ms": result.duration_ms,
            "snapshot": _snapshot_payload(row) if row else None,
        }
    )


@app.get("/api/waiver-swaps/latest")
def latest_waiver_swaps() -> dict[str, Any]:
    try:
        payload = sandlot_waivers.latest_waiver_payload()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Waiver swaps failed")
        raise HTTPException(status_code=503, detail=f"Waiver swaps unavailable: {exc}") from exc
    return jsonable_encoder(payload)


class TradeGradeIn(BaseModel):
    give: list[str] = Field(..., min_length=1, max_length=5)
    get: list[str] = Field(..., min_length=1, max_length=5)
    incoming_trade_id: str | None = Field(default=None, max_length=160)
    incoming_snapshot_id: int | None = None


@app.post("/api/trades/grade")
def grade_trade(payload: TradeGradeIn) -> dict[str, Any]:
    try:
        snapshot_row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Trade analysis is temporarily unavailable") from exc
    if not snapshot_row:
        raise HTTPException(status_code=409, detail="No Fantrax snapshot yet — run a refresh first")
    incoming_origin = None
    if payload.incoming_trade_id is not None or payload.incoming_snapshot_id is not None:
        if not payload.incoming_trade_id or payload.incoming_snapshot_id != snapshot_row.get("id"):
            raise HTTPException(status_code=409, detail="Incoming offer snapshot changed — refresh and review it again")
        expected = next(
            (offer for offer in _incoming_offers_from_snapshot(snapshot_row) if offer.get("trade_id") == payload.incoming_trade_id),
            None,
        )
        exact_give = sorted(item["player_id"] for item in (expected or {}).get("give", []) if item.get("player_id"))
        exact_get = sorted(item["player_id"] for item in (expected or {}).get("get", []) if item.get("player_id"))
        if (
            not expected or expected.get("gradeable") is not True
            or sorted(payload.give) != exact_give or sorted(payload.get) != exact_get
        ):
            raise HTTPException(status_code=409, detail="Incoming offer changed or is no longer exactly gradeable")
        incoming_origin = {
            "trade_id": expected["trade_id"],
            "snapshot_id": snapshot_row["id"],
            "proposed_by_team_id": expected["proposed_by_team_id"],
            "proposed_at_label": expected.get("proposed_at"),
            "scheduled_execution_at_label": expected.get("scheduled_execution_at_label"),
        }
    try:
        result = sandlot_trades.grade_offer(snapshot_row, payload.give, payload.get)
        receipt, _created = sandlot_db.record_recommendation_receipt(
            sandlot_receipts.build_trade_assessment_receipt(
                snapshot=snapshot_row, result=result, origin=incoming_origin
            )
        )
        result["receipt"] = _public_recommendation_receipt(receipt)
    except sandlot_trades.TradeGradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Trade grade failed")
        raise HTTPException(status_code=503, detail="Trade analysis is temporarily unavailable") from exc
    return jsonable_encoder(result)


@app.get("/api/trades/incoming")
def incoming_trades() -> dict[str, Any]:
    """Return sanitized incoming Fantrax offers; reviewing remains read-only and manual."""
    try:
        snapshot_row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Incoming trades are temporarily unavailable") from exc
    if not snapshot_row:
        raise HTTPException(status_code=409, detail="No Fantrax snapshot yet — run a refresh first")
    offers = _incoming_offers_from_snapshot(snapshot_row)
    return jsonable_encoder({
        "snapshot_id": snapshot_row.get("id"),
        "taken_at": snapshot_row.get("taken_at"),
        "freshness": _freshness(snapshot_row.get("taken_at")),
        "offers": offers,
        "read_only": True,
        "fantrax_changed": False,
        "writes_enabled": False,
    })


def _incoming_offers_from_snapshot(snapshot_row: dict[str, Any]) -> list[dict[str, Any]]:
    data = _persisted_snapshot_data(snapshot_row)
    my_team_id = str(data.get("team_id") or snapshot_row.get("team_id") or "").strip()
    freshness = _freshness(snapshot_row.get("taken_at"))
    raw_trades = data.get("pending_trades") if isinstance(data.get("pending_trades"), list) else []
    offers = []
    for raw in raw_trades:
        proposer_id = str(raw.get("proposed_by_id") or "").strip() if isinstance(raw, dict) else ""
        if not isinstance(raw, dict) or raw.get("error") or not proposer_id or proposer_id == my_team_id:
            continue
        give, get, unsupported, counterparties = [], [], [], set()
        for move in raw.get("moves") or []:
            if not isinstance(move, dict):
                unsupported.append("invalid_move")
                continue
            if move.get("draft_pick"):
                unsupported.append("draft_pick")
                continue
            from_team_id = str(move.get("from_team_id") or "")
            to_team_id = str(move.get("to_team_id") or "")
            if my_team_id not in {from_team_id, to_team_id}:
                unsupported.append("third_party_move")
                continue
            player_id = str(move.get("player_id") or "").strip()
            player_name = str(move.get("player") or "").strip()
            item = {"player_id": player_id or None, "player_name": player_name or None}
            if not player_id or not player_name:
                unsupported.append("missing_player_identity")
            if from_team_id == my_team_id:
                give.append(item)
                if to_team_id:
                    counterparties.add(to_team_id)
            elif to_team_id == my_team_id:
                get.append(item)
                if from_team_id:
                    counterparties.add(from_team_id)
        if len(counterparties) != 1:
            unsupported.append("multi_team_offer")
        elif proposer_id not in counterparties:
            unsupported.append("proposer_mismatch")
        if not str(raw.get("trade_id") or "").strip():
            unsupported.append("missing_trade_identity")
        if raw.get("accepted"):
            unsupported.append("already_accepted")
        give_ids = [item["player_id"] for item in give if item.get("player_id")]
        get_ids = [item["player_id"] for item in get if item.get("player_id")]
        if len(give_ids) != len(set(give_ids)) or len(get_ids) != len(set(get_ids)):
            unsupported.append("duplicate_player_identity")
        if set(give_ids) & set(get_ids):
            unsupported.append("overlapping_player_identity")
        if freshness.get("state") == "old":
            unsupported.append("old_snapshot")
        give.sort(key=lambda item: str(item.get("player_id") or ""))
        get.sort(key=lambda item: str(item.get("player_id") or ""))
        manual_review_reason = None
        manual_review = None
        if give and get and not unsupported:
            manual_review_reason = sandlot_trades.offer_validation_error(
                snapshot_row,
                [item["player_id"] for item in give],
                [item["player_id"] for item in get],
                expected_get_owner_id=next(iter(counterparties)),
            )
            if manual_review_reason:
                unsupported.append("participant_policy")
                try:
                    manual_review = sandlot_trades.build_manual_review(
                        snapshot_row,
                        [item["player_id"] for item in give],
                        [item["player_id"] for item in get],
                        expected_get_owner_id=next(iter(counterparties)),
                        scheduled_execution_at_label=str(raw.get("executed") or "").strip() or None,
                    )
                except sandlot_trades.TradeGradeError:
                    manual_review = None
        gradeable = bool(give and get and not unsupported)
        offers.append({
            "trade_id": str(raw.get("trade_id") or "").strip() or None,
            "proposed_by": str(raw.get("proposed_by") or "").strip() or "Another team",
            "proposed_by_team_id": proposer_id,
            "proposed_at": raw.get("proposed"),
            "scheduled_execution_at_label": raw.get("executed"),
            "status": "awaiting_execution" if raw.get("accepted") else "pending",
            "give": give,
            "get": get,
            "gradeable": gradeable,
            "blocked_reasons": sorted(set(unsupported + ([] if give else ["missing_give_side"]) + ([] if get else ["missing_get_side"]))),
            "includes_draft_pick": "draft_pick" in unsupported,
            "manual_review_reason": manual_review_reason,
            "manual_review": manual_review,
            "manual_only": True,
        })
    return offers


class SkipperMessageIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    model: str | None = Field(default=None, max_length=120)
    reasoning: bool = False
    reasoning_effort: str | None = Field(default=None, max_length=20)
    web_search: bool = True


@app.get("/api/skipper/options")
def skipper_options() -> dict[str, Any]:
    return {
        "default_model": sandlot_skipper.primary_model(),
        "models": [
            {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash", "short": "DS Flash", "primary": True},
            {"id": "moonshotai/kimi-k2", "label": "Kimi K2", "short": "Kimi"},
            {"id": "deepseek/deepseek-v4-pro", "label": "DeepSeek V4 Pro", "short": "DS Pro"},
            {"id": "z-ai/glm-5.2", "label": "GLM 5.2", "short": "GLM 5.2"},
        ],
        "reasoning": {
            "default_enabled": False,
            "default_effort": "medium",
            "efforts": ["minimal", "low", "medium", "high"],
        },
        "web_search": {
            "available": sandlot_skipper.web_search_available(),
            "default_enabled": sandlot_skipper.web_search_default_enabled(),
            "tool": sandlot_skipper.WEB_SEARCH_TOOL_TYPE,
        },
    }


@app.get("/api/skipper/messages")
def skipper_history() -> dict[str, Any]:
    try:
        session_id = sandlot_db.get_or_create_default_session()
        rows = sandlot_db.list_chat_messages(session_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    return jsonable_encoder({
        "session_id": session_id,
        "messages": [
            {
                "id": r.get("id"),
                "role": r.get("role"),
                "content": r.get("content"),
                "tier": r.get("tier"),
                "model": r.get("model"),
                "metadata": r.get("metadata") if isinstance(r.get("metadata"), dict) else {},
                "created_at": r.get("created_at"),
            }
            for r in rows
        ],
    })


@app.get("/api/player/{fantrax_id}")
def player_profile(fantrax_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = _player_response(fantrax_id, force_refresh=False)
    profile_cache = payload.get("profile_cache") or {}
    take_state = (profile_cache.get("take") or {}).get("state")
    take_missing = take_state == "missing"
    if profile_cache.get("needs_refresh") or take_missing:
        background_tasks.add_task(
            player_service.refresh_cached_profile,
            fantrax_id,
            generate_take=take_missing,
        )
        if take_missing:
            payload.setdefault("profile_cache", {}).setdefault("take", {})["pending"] = True
    return payload


@app.post("/api/player/{fantrax_id}/refresh")
def player_profile_refresh(fantrax_id: str) -> dict[str, Any]:
    return _player_response(fantrax_id, force_refresh=True)


@app.get("/api/team/{team_id}/roster")
def team_roster(team_id: str) -> dict[str, Any]:
    try:
        row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    if not row:
        raise HTTPException(status_code=404, detail="No successful Fantrax snapshot has been stored yet")
    data = row.get("data") or {}
    all_team_rosters = data.get("all_team_rosters") or {}
    team = all_team_rosters.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not in latest snapshot")
    return jsonable_encoder({
        "snapshot_id": row.get("id"),
        "taken_at": row.get("taken_at"),
        "team_id": team.get("team_id") or team_id,
        "team_name": team.get("team_name"),
        "team_short": team.get("team_short"),
        "is_me": bool(team.get("is_me")),
        "rows": team.get("rows") or [],
        "active": team.get("active"),
        "active_max": team.get("active_max"),
        "reserve": team.get("reserve"),
        "reserve_max": team.get("reserve_max"),
        "injured": team.get("injured"),
        "injured_max": team.get("injured_max"),
    })


def _player_response(fantrax_id: str, *, force_refresh: bool) -> dict[str, Any]:
    try:
        payload = player_service.get_player_profile(fantrax_id, force_refresh=force_refresh)
    except player_service.PlayerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Player profile failed for %s", fantrax_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return jsonable_encoder(payload)


@app.delete("/api/skipper/messages")
def skipper_clear() -> dict[str, Any]:
    try:
        session_id = sandlot_db.get_or_create_default_session()
        deleted = sandlot_db.clear_chat_messages(session_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    return {"session_id": session_id, "deleted": deleted}


@app.post("/api/skipper/messages")
def skipper_send(payload: SkipperMessageIn) -> StreamingResponse:
    user_text = payload.content.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="content is required")

    try:
        session_id = sandlot_db.get_or_create_default_session()
        snapshot_row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc

    if not snapshot_row:
        raise HTTPException(status_code=409, detail="No Fantrax snapshot yet — run a refresh first")

    snapshot = _snapshot_payload(snapshot_row)
    tier = sandlot_skipper.detect_tier(user_text, snapshot)
    use_web_search = sandlot_skipper.web_search_allowed(payload.web_search)
    context_block = sandlot_skipper.build_context(tier, snapshot, prompt=user_text)
    history = sandlot_db.list_chat_messages(session_id)
    messages = sandlot_skipper.build_messages(history, user_text, context_block, web_search=use_web_search)
    deterministic_reply = sandlot_skipper.deterministic_reply(user_text, snapshot)
    is_trade_research = user_text.startswith("Sandlot trade-analysis evidence:")
    selected_model = payload.model
    model_order = sandlot_skipper.model_order(selected_model)
    reasoning_effort = sandlot_skipper.normalize_reasoning_effort(
        payload.reasoning_effort if payload.reasoning else None
    )

    sandlot_db.append_chat_message(session_id, "user", user_text)
    _log_skipper_projection_surfaces(snapshot_row, user_text, snapshot)

    def event_stream():
        if deterministic_reply:
            try:
                sandlot_db.append_chat_message(
                    session_id, "assistant", deterministic_reply, tier=tier, model="deterministic"
                )
            except Exception:
                log.exception("Failed to persist deterministic assistant message")
            yield _sse({"type": "token", "text": deterministic_reply})
            yield _sse({
                "type": "done",
                "tier": tier,
                "model": "deterministic",
                "selected_model": selected_model,
                "reasoning": bool(reasoning_effort),
                "web_search": False,
            })
            return

        try:
            client = sandlot_skipper.SkipperClient()
        except Exception as exc:
            log.exception("Skipper client init failed")
            yield _sse({"type": "error", "message": str(exc)})
            return

        assistant_buf: list[str] = []
        sources_by_url: dict[str, dict[str, Any]] = {}
        web_search_requests = 0
        used_model: str | None = None
        try:
            model_events = client.stream(
                messages,
                model_order=model_order,
                reasoning_effort=reasoning_effort,
                web_search=use_web_search,
            )
            if is_trade_research:
                yield _sse({"type": "research_started", "stage": "researching"})
                event_iterator = _trade_research_events(
                    model_events,
                    on_cancel=getattr(client, "cancel_active_stream", None),
                )
            else:
                event_iterator = (("model_event", event) for event in model_events)

            for stream_kind, stream_payload in event_iterator:
                if stream_kind == "progress":
                    yield _sse(stream_payload)
                    continue
                kind, payload_text = stream_payload
                if kind == "token":
                    assistant_buf.append(payload_text)
                    if not is_trade_research:
                        yield _sse({"type": "token", "text": payload_text})
                elif kind == "model":
                    used_model = payload_text
                elif kind == "source" and isinstance(payload_text, dict):
                    url = str(payload_text.get("url") or "")
                    if url and url not in sources_by_url:
                        sources_by_url[url] = payload_text
                elif kind == "web_search_requests":
                    try:
                        web_search_requests = max(web_search_requests, int(payload_text))
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            log.exception("Skipper stream failed")
            yield _sse({"type": "error", "message": str(exc)})
            return

        raw = "".join(assistant_buf)
        full = sandlot_skipper.repair_reply(raw, user_text, snapshot)
        sources = list(sources_by_url.values())
        web_search_requests, web_search_executed, sources_available = _web_search_evidence(
            sources,
            web_search_requests,
        )
        if is_trade_research and full:
            # Trade research is buffered until the deterministic evidence guard
            # has built the only answer the user is allowed to see.
            yield _sse({"type": "token", "text": full})
        elif full and full != raw.strip():
            # The backend repaired a broken refusal or enforced a deterministic
            # evidence boundary. Replace the streamed draft with the safe result.
            yield _sse({"type": "replace", "text": full})
        if full:
            try:
                sandlot_db.append_chat_message(
                    session_id,
                    "assistant",
                    full,
                    tier=tier,
                    model=used_model,
                    metadata={
                        "sources": sources,
                        "web_search_requested": use_web_search,
                        "web_search": web_search_executed,
                        "sources_available": sources_available,
                        "web_search_requests": web_search_requests,
                    },
                )
            except Exception as exc:
                log.exception("Failed to persist assistant message")
        if sources:
            yield _sse({"type": "sources", "sources": sources})
        yield _sse({
            "type": "done",
            "tier": tier,
            "model": used_model,
            "selected_model": selected_model,
            "reasoning": bool(reasoning_effort),
            "web_search_requested": use_web_search,
            "web_search": web_search_executed,
            "sources_available": sources_available,
            "web_search_requests": web_search_requests,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering for live streams
        },
    )


def _trade_research_events(model_events, *, on_cancel=None):
    """Keep a buffered trade stream alive without exposing unguarded model text."""
    # Bound the handoff so a disconnected/slow client cannot leave a model
    # producer filling memory in the background.
    events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=32)
    complete = object()
    stop = threading.Event()

    def enqueue(item: tuple[str, Any]) -> bool:
        while not stop.is_set():
            try:
                events.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def collect() -> None:
        try:
            for event in model_events:
                if stop.is_set() or not enqueue(("model_event", event)):
                    break
        except BaseException as exc:  # re-raised on the response generator thread
            enqueue(("error", exc))
        finally:
            close = getattr(model_events, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    log.debug("Trade research model stream close failed", exc_info=True)
            enqueue(("complete", complete))

    worker = threading.Thread(target=collect, name="trade-research-stream", daemon=True)
    worker.start()
    last_progress = time.monotonic()
    try:
        while True:
            timeout = max(0.001, TRADE_RESEARCH_HEARTBEAT_SECONDS - (time.monotonic() - last_progress))
            try:
                kind, payload = events.get(timeout=timeout)
            except queue.Empty:
                yield "progress", {"type": "research_progress", "stage": "applying_guardrails"}
                last_progress = time.monotonic()
                continue
            if time.monotonic() - last_progress >= TRADE_RESEARCH_HEARTBEAT_SECONDS:
                yield "progress", {"type": "research_progress", "stage": "applying_guardrails"}
                last_progress = time.monotonic()
            if kind == "complete":
                return
            if kind == "error":
                raise payload
            yield kind, payload
    finally:
        # StreamingResponse closes this generator when the browser disconnects.
        # Signal the producer and close the active provider HTTP stream before
        # joining, so a long pre-token read does not have to reach another
        # model event before cancellation can take effect.
        stop.set()
        if callable(on_cancel):
            try:
                on_cancel()
            except Exception:
                log.debug("Trade research provider cancellation failed", exc_info=True)
        worker.join(timeout=0.25)


def _log_skipper_projection_surfaces(
    snapshot_row: dict[str, Any],
    user_text: str,
    snapshot_payload: dict[str, Any],
) -> None:
    """Log projections shown by Skipper without letting telemetry break chat."""
    matchup = snapshot_payload.get("matchup") if isinstance(snapshot_payload.get("matchup"), dict) else None
    projection = matchup.get("projection") if isinstance(matchup, dict) else None
    data_quality = snapshot_payload.get("data_quality") if isinstance(snapshot_payload.get("data_quality"), dict) else {}
    if not projection or not data_quality.get("projection_ready", True):
        return
    if not sandlot_skipper.is_matchup_request(user_text):
        return

    surfaces = {"skipper_chat"}
    if sandlot_skipper.is_deep_matchup_request(user_text):
        surfaces.add("skipper_card")

    raw_snapshot = snapshot_row.get("data") or {}
    try:
        record = sandlot_matchup.projection_log_payload(
            int(snapshot_row.get("id")),
            raw_snapshot,
            data_quality,
        )
        if not record:
            return
        for surface in sorted(surfaces):
            sandlot_db.upsert_projection_log(**record, surface=surface)
    except Exception:
        log.exception("Skipper projection log write failed for snapshot_id=%s", snapshot_row.get("id"))


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _web_search_evidence(
    sources: list[dict[str, Any]],
    reported_requests: int,
) -> tuple[int, bool, bool]:
    """Separate provider usage, actual search execution, and cited evidence."""
    try:
        requests = max(0, int(reported_requests))
    except (TypeError, ValueError):
        requests = 0
    sources_available = bool(sources)
    if sources_available and requests < 1:
        # Some OpenRouter responses include citations but omit usage. A
        # citation proves at least one search-backed attempt occurred.
        requests = 1
    return requests, bool(requests), sources_available


def _persisted_snapshot_data(row: dict[str, Any]) -> dict[str, Any]:
    """Bind derived recommendation contracts to the stored snapshot row."""
    raw_data = row.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    return {
        **data,
        "snapshot_id": row.get("id"),
        "snapshot_taken_at": row.get("taken_at"),
    }


def _matchup_decisions(row: dict[str, Any]) -> dict[str, Any]:
    """Derive every current-matchup surface from one shared decision context."""
    data = row.get("data") or {}
    snapshot_data = _persisted_snapshot_data(row)
    data_quality = sandlot_data_quality.snapshot_data_quality(data)
    matchup_block = data.get("matchup")
    matchup = None
    lineup_recommendations = None
    if isinstance(matchup_block, dict) and matchup_block:
        current_period_aligned = (
            (data_quality.get("current_period") or {}).get("state") != "mismatch"
        )
        lineup_recommendations = (
            sandlot_matchup.rank_matchup_improvement_actions(snapshot_data, data_quality)
            if current_period_aligned
            else {
                "recommendations": [],
                "no_action": {
                    "reason": "Current-period lineup slots are not the editable Fantrax lineup."
                },
            }
        )
        matchup = {
            **matchup_block,
            "projection": (
                sandlot_matchup.compute_projection(snapshot_data, data_quality)
                if current_period_aligned
                else None
            ),
            "recommendations": lineup_recommendations,
        }
    return {
        "data_quality": data_quality,
        "matchup": matchup,
        "win_this_week": sandlot_win_week.build_plan(
            row,
            data_quality=data_quality,
            lineup_recommendations=lineup_recommendations,
        ),
    }


def _latest_reviewed_action(
    proposal_id: str,
    snapshot_id: int,
    input_hash: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Re-derive and exact-match one proposal from the latest snapshot."""
    try:
        row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    if not row:
        raise HTTPException(status_code=404, detail="No successful Fantrax snapshot has been stored yet")
    plan = _matchup_decisions(row)["win_this_week"]
    action = next(
        (
            candidate
            for candidate in plan.get("actions") or []
            if isinstance(candidate, dict)
            and str(((candidate.get("review") or {}).get("proposal_id")) or candidate.get("id") or "")
            == proposal_id
        ),
        None,
    )
    if not action:
        raise HTTPException(
            status_code=404,
            detail="That proposal is not part of the latest actionable plan; refresh and review the replacement.",
        )
    review = action.get("review") if isinstance(action.get("review"), dict) else {}
    if review.get("state") != "reviewable":
        raise HTTPException(status_code=409, detail=review.get("reason") or "Proposal is not reviewable")
    if int(review.get("snapshot_id") or 0) != snapshot_id or str(review.get("input_hash") or "") != input_hash:
        raise HTTPException(
            status_code=409,
            detail="That proposal instance is stale or replaced; refresh and review the latest contract.",
        )
    return row, action, review


def _snapshot_payload(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data") or {}
    roster_meta = data.get("roster") or {}
    standings = data.get("standings") or {}
    decisions = _matchup_decisions(row)
    taken_at = row.get("taken_at")
    return {
        "snapshot_id": row.get("id"),
        "taken_at": taken_at,
        "source": row.get("source"),
        "status": row.get("status"),
        "freshness": _freshness(taken_at),
        "league_id": data.get("league_id") or row.get("league_id"),
        "team_id": data.get("team_id") or row.get("team_id"),
        "team_name": data.get("team_name") or row.get("team_name"),
        "roster": roster_meta.get("rows") or [],
        "roster_meta": {k: v for k, v in roster_meta.items() if k != "rows"},
        "standings": standings.get("records") or [],
        "my_standing": standings.get("my_record"),
        "matchup": decisions["matchup"],
        "win_this_week": decisions["win_this_week"],
        "data_quality": decisions["data_quality"],
        "player_index": _player_index(data),
        "errors": row.get("errors") or data.get("errors") or [],
    }


def _hot_swap_payload(row: dict[str, Any]) -> dict[str, Any]:
    data = _persisted_snapshot_data(row)
    taken_at = row.get("taken_at")
    data_quality = sandlot_data_quality.snapshot_data_quality(data)
    items = sandlot_attention.attention_items(data)
    proposals = []
    for item in items:
        if item.get("kind") != "replacement":
            continue
        proposal = item.get("proposal") if isinstance(item.get("proposal"), dict) else None
        replacement = item.get("replacement") if isinstance(item.get("replacement"), dict) else None
        if not proposal or not replacement:
            continue
        proposals.append({
            "proposal": proposal,
            "replacement": replacement,
            "blocked_action": item.get("blocked_action"),
            "source_item": {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "title": item.get("title"),
                "context": item.get("context"),
                "reason": item.get("reason"),
                "chips": item.get("chips") or [],
            },
        })
    lineup_ready = data_quality.get("lineup_recommendations_ready") is True
    state = "ready" if proposals else ("none" if lineup_ready else "paused")
    return {
        "snapshot_id": row.get("id"),
        "taken_at": taken_at,
        "freshness": _freshness(taken_at),
        "state": state,
        "writes_enabled": False,
        "proposals": proposals,
        "paused_reason": None if (proposals or lineup_ready) else sandlot_data_quality.short_reason(data_quality, purpose="lineup"),
        "data_quality": {
            "lineup_recommendations_ready": lineup_ready,
            "proposal_scoped_ready": bool(proposals),
            "lineup_slots": data_quality.get("lineup_slots"),
            "lineup_recommendation_reasons": data_quality.get("lineup_recommendation_reasons") or [],
        },
    }


def _player_index(
    data: dict[str, Any],
    *,
    drops: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Flat list of every player the snapshot knows about.

    Frontend uses this two ways:
    1. Skipper chat replies: lowercased-name -> fantrax_id map so full-name
       mentions become profile links even when the model forgets to emit a
       [[name|id]] tag.
    2. Trade tab pickers: filter by `source` ("mine" / "league" / "free_agent")
       to populate the give/get autocompletes.

    Malformed rows and team buckets are skipped from the output (the prior
    behavior) but emit WARN logs and, if a `drops` dict is supplied, increment
    per-reason counters so #14's data-quality gates can surface degraded
    snapshots honestly. Duplicate ids are expected when the same player is
    visible through multiple Fantrax surfaces, so they are counted without
    warning-level log spam. The `drops` kwarg is opt-in; existing callers that
    just want the flat list need no change.
    """
    if drops is not None:
        for reason in ("non_dict_row", "missing_id_or_name", "duplicate", "non_dict_team"):
            drops.setdefault(reason, 0)

    def _note(reason: str) -> None:
        if drops is not None:
            drops[reason] = drops.get(reason, 0) + 1

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(rows: Any, *, source: str, team_id: str | None = None) -> None:
        for r in rows or []:
            if not isinstance(r, dict):
                log.warning(
                    "_player_index drop: non_dict_row source=%s row_type=%s",
                    source, type(r).__name__,
                )
                _note("non_dict_row")
                continue
            pid = r.get("id")
            name = r.get("name")
            pid_key = str(pid) if pid else ""
            if not pid_key or not name:
                if pid is None and name is None:
                    log.debug(
                        "_player_index drop: blank_row source=%s",
                        source,
                    )
                else:
                    log.warning(
                        "_player_index drop: missing_id_or_name source=%s id=%r name=%r",
                        source, pid, name,
                    )
                _note("missing_id_or_name")
                continue
            if pid_key in seen:
                log.debug(
                    "_player_index drop: duplicate id=%s source=%s name=%r",
                    pid_key, source, name,
                )
                _note("duplicate")
                continue
            seen.add(pid_key)
            out.append({
                "id": pid,
                "name": name,
                "team": r.get("team"),
                "slot": r.get("slot"),
                "positions": r.get("positions"),
                "fppg": r.get("fppg"),
                "age": r.get("age"),
                "age_source": r.get("age_source"),
                "source": source,
                "team_id": team_id,
            })

    my_team_id = data.get("team_id")
    my_roster_rows = (data.get("roster") or {}).get("rows")
    has_canonical_my_roster = bool(my_roster_rows)
    add(my_roster_rows, source="mine", team_id=my_team_id)
    for tid, team in (data.get("all_team_rosters") or {}).items():
        if not isinstance(team, dict):
            log.warning(
                "_player_index drop: non_dict_team team_id=%r team_type=%s",
                tid, type(team).__name__,
            )
            _note("non_dict_team")
            continue
        team_id = team.get("team_id") or tid
        is_mine = bool(team.get("is_me")) or (
            my_team_id is not None and str(team_id) == str(my_team_id)
        )
        if is_mine and has_canonical_my_roster:
            continue
        add(team.get("rows"), source="mine" if is_mine else "league", team_id=team_id)
    add((data.get("free_agents") or {}).get("players"), source="free_agent")
    return out


def _freshness(taken_at: Any) -> dict[str, Any]:
    if not isinstance(taken_at, datetime):
        return {"state": "missing", "age_minutes": None}
    now = datetime.now(timezone.utc)
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((now - taken_at).total_seconds() / 60))
    if age_minutes <= FRESH_SNAPSHOT_MINUTES:
        state = "fresh"
    elif age_minutes <= OLD_SNAPSHOT_MINUTES:
        state = "stale"
    else:
        state = "old"
    return {"state": state, "age_minutes": age_minutes}


def _run_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "source": row.get("source"),
        "status": row.get("status"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "duration_ms": row.get("duration_ms"),
        "error": row.get("error"),
    }


def _latest_reconciliation_snapshot() -> dict[str, Any] | None:
    """Keep receipt reads available even when fresh Fantrax evidence is not."""
    try:
        return sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        log.warning("Latest Fantrax snapshot unavailable for receipt reconciliation: %s", exc)
        return None


def _public_recommendation_receipt(
    row: dict[str, Any],
    snapshot_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recommendation = row.get("recommendation") if isinstance(row.get("recommendation"), dict) else {}
    evaluation = recommendation.get("evaluation") if isinstance(recommendation.get("evaluation"), dict) else {}
    snapshot = recommendation.get("snapshot") if isinstance(recommendation.get("snapshot"), dict) else {}
    period = recommendation.get("period") if isinstance(recommendation.get("period"), dict) else {}
    trade = recommendation.get("offer") if isinstance(recommendation.get("offer"), dict) else None
    trade_origin = recommendation.get("origin") if isinstance(recommendation.get("origin"), dict) else {}
    trade_outcome = recommendation.get("outcome_contract") if isinstance(recommendation.get("outcome_contract"), dict) else {}
    result = {
        "receipt_id": row.get("receipt_id"),
        "builder_version": row.get("builder_version"),
        "scope_key": row.get("scope_key"),
        "source": row.get("source"),
        "action_type": row.get("action_type"),
        "proposal_id": row.get("proposal_id"),
        "input_hash": row.get("input_hash"),
        "period": {
            "start": period.get("start") or row.get("period_start"),
            "end": period.get("end") or row.get("period_end"),
        },
        "evaluation": {
            "horizon": evaluation.get("horizon") or row.get("evaluation_horizon"),
            "metric_name": evaluation.get("metric_name") or row.get("metric_name"),
            "metric_unit": evaluation.get("metric_unit") or row.get("metric_unit"),
            "baseline_value": row.get("baseline_value"),
            "projected_value": row.get("projected_value"),
            "projected_gain": row.get("projected_gain"),
        },
        "baseline_assignment": recommendation.get("baseline_assignment") or [],
        "proposed_assignment": recommendation.get("proposed_assignment") or [],
        "unfilled_slots": recommendation.get("unfilled_slots") or [],
        "trade": ({
            "give": trade.get("give") or [],
            "get": trade.get("get") or [],
            "origin": {
                "kind": trade_origin.get("kind"),
                "fantrax_trade_id": trade_origin.get("fantrax_trade_id"),
                "snapshot_id": trade_origin.get("snapshot_id"),
                "proposed_by_team_id": trade_origin.get("proposed_by_team_id"),
                "proposed_at_label": trade_origin.get("proposed_at_label"),
                "scheduled_execution_at_label": trade_origin.get("scheduled_execution_at_label"),
                "source_status": trade_origin.get("source_status"),
                "execution_verification": trade_origin.get("execution_verification"),
            },
            "outcome_contract": {
                "version": trade_outcome.get("version"),
                "eligible": trade_outcome.get("eligible") is True,
                "blocking_reasons": (
                    trade_outcome.get("blocking_reasons") or []
                    if trade_outcome
                    else [{"code": "legacy_outcome_contract_unavailable"}]
                ),
                "selection_rule": trade_outcome.get("selection_rule"),
                "target_period": _public_trade_target_period(trade_outcome.get("target_period")),
                "measurement_scope": trade_outcome.get("measurement_scope"),
                "target_metric": trade_outcome.get("target_metric"),
                "metric_unit": trade_outcome.get("metric_unit"),
                "causal_lift_claimed": False,
                "execution_claimed": False,
                "lineup_lift_claimed": False,
                "ros_claimed": False,
                "dynasty_claimed": False,
                "autopilot_eligible": False,
            },
            "grade": recommendation.get("grade") or {},
            "horizons": recommendation.get("horizons") or [],
            "guardrails": recommendation.get("guardrails") or {},
        } if trade is not None else None),
        "evidence": {
            "snapshot_id": snapshot.get("id"),
            "snapshot_taken_at": snapshot.get("taken_at"),
            "builder_version": recommendation.get("builder_version") or row.get("builder_version"),
        },
        "lifecycle_state": row.get("lifecycle_state"),
        "decision_state": row.get("decision_state"),
        "decision_reason": row.get("decision_reason"),
        "decided_at": row.get("decided_at"),
        "outcome_state": row.get("outcome_state"),
        "outcome": _public_recommendation_outcome(row),
        "generated_at": row.get("generated_at"),
        "expires_at": row.get("expires_at"),
        "read_only": True,
        "fantrax_changed": False,
        "writes_enabled": False,
    }
    if row.get("action_type") == "lineup_plan":
        result["reconciliation"] = sandlot_receipts.reconcile_lineup_receipt(row, snapshot_row)
    return result


def _public_trade_target_period(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "period_number": value.get("period_number"),
        "period_name": value.get("period_name"),
        "season": value.get("season"),
        "start": value.get("start"),
        "end": value.get("end"),
        "first_scoring_event_at": value.get("first_scoring_event_at"),
        "period_close_at": value.get("period_close_at"),
        "maturity_at": value.get("maturity_at"),
        "correction_grace_hours": value.get("correction_grace_hours"),
    }


def _public_recommendation_outcome(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("outcome_state") != "scored":
        return None
    evidence = row.get("outcome_evidence") if isinstance(row.get("outcome_evidence"), dict) else {}
    return {
        "scoring_version": row.get("scoring_version"),
        "measurement_scope": evidence.get("measurement_scope"),
        "actual_team_total": row.get("actual_value"),
        "actual_baseline": None,
        "actual_gain": None,
        "team_total_residual": evidence.get("team_total_residual"),
        "absolute_error": evidence.get("absolute_error"),
        "adherence_state": evidence.get("adherence_state"),
        "counterfactual_state": evidence.get("counterfactual_state"),
        "counterfactual_reason": evidence.get("counterfactual_reason"),
        "evaluated_at": row.get("evaluated_at"),
        "autopilot_eligible": False,
    }


def _public_recommendation_learning(report: dict[str, Any]) -> dict[str, Any]:
    """Aggregate immutable evaluations into a conservative public report."""
    raw_summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    rows = report.get("items") if isinstance(report.get("items"), list) else []
    evaluated = max(0, int(raw_summary.get("evaluated") or 0))
    scored = max(0, int(raw_summary.get("scored") or 0))
    unavailable = max(0, int(raw_summary.get("unavailable") or 0))
    aligned = max(0, int(raw_summary.get("accepted_and_observed") or 0))
    assignment_counts = {
        "proposed": max(0, int(raw_summary.get("proposed_matches") or 0)),
        "baseline": max(0, int(raw_summary.get("baseline_matches") or 0)),
        "other": max(0, int(raw_summary.get("other_matches") or 0)),
    }
    average_gain = _finite_public_metric(raw_summary.get("average_counterfactual_gain"))
    positive_rate = _finite_public_metric(raw_summary.get("positive_counterfactual_gain_rate"))

    required_scored = 8
    required_aligned = 4
    minimum_sample_reached = scored >= required_scored and aligned >= required_aligned
    return {
        "scoring_version": sandlot_receipts.COUNTERFACTUAL_LINEUP_SCORING_VERSION,
        "measurement_scope": "retrospective_static_lineup_counterfactual",
        "sample_definition": "one_latest_active_receipt_per_league_team_period",
        "counterfactual_gain_available": average_gain is not None,
        "sample_state": "minimum_sample_reached" if minimum_sample_reached else "collecting",
        "summary": {
            "evaluated": evaluated,
            "scored": scored,
            "unavailable": unavailable,
            "accepted_and_observed": aligned,
            "actual_assignment_matches": assignment_counts,
            "average_counterfactual_gain": round(average_gain, 4) if average_gain is not None else None,
            "positive_counterfactual_gain_rate": round(positive_rate, 4) if positive_rate is not None else None,
        },
        "evidence_checkpoint": {
            "state": "minimum_sample_reached" if minimum_sample_reached else "collecting",
            "minimum_sample_reached": minimum_sample_reached,
            "requirements": [
                {
                    "key": "scored_evaluations",
                    "current": scored,
                    "required": required_scored,
                    "passed": scored >= required_scored,
                },
                {
                    "key": "accepted_and_observed",
                    "current": aligned,
                    "required": required_aligned,
                    "passed": aligned >= required_aligned,
                },
            ],
        },
        "autopilot": {
            "state": "locked",
            "eligible": False,
            "reason": "Evidence quantity alone never grants Fantrax write authority; quality, safety, and verified execution require separate review.",
        },
        "items": [
            {
                "period": {"start": row.get("period_start"), "end": row.get("period_end")},
                "state": row.get("state"),
                "decision_state": row.get("decision_state"),
                "counterfactual": (
                    {
                        "baseline_total": _finite_public_metric(row.get("counterfactual_baseline_total")),
                        "proposed_total": _finite_public_metric(row.get("counterfactual_proposed_total")),
                        "gain": _finite_public_metric(row.get("counterfactual_gain")),
                    }
                    if row.get("state") == "scored"
                    else None
                ),
                "observed_team_total": (
                    _finite_public_metric(row.get("observed_team_total"))
                    if row.get("state") == "scored"
                    else None
                ),
                "actual_assignment_match": row.get("actual_assignment_match"),
                "decision_alignment": row.get("decision_alignment"),
                "evaluated_at": row.get("evaluated_at"),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "read_only": True,
        "fantrax_changed": False,
        "autopilot_eligible": False,
    }


def _finite_public_metric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _require_refresh_token(request: Request) -> None:
    expected = os.environ.get("SANDLOT_REFRESH_TOKEN")
    if not expected:
        return
    provided = request.headers.get("x-refresh-token")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid refresh token")


def _require_execution_role(request: Request, digest_env: str) -> None:
    if not sandlot_execution.dry_run_enabled():
        raise HTTPException(status_code=503, detail="Execution dry-run control plane is disabled")
    if not sandlot_execution.distinct_role_credentials_configured():
        raise HTTPException(status_code=503, detail="Distinct execution credentials are not configured")
    _require_hashed_role(request, digest_env)


def _require_hashed_role(request: Request, digest_env: str) -> None:
    try:
        sandlot_execution.require_hashed_bearer(
            request.headers.get("authorization"),
            digest_env=digest_env,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except sandlot_execution.ExecutionContractError as exc:
        raise HTTPException(status_code=503, detail="Execution credential is not configured") from exc


@app.get("/", include_in_schema=False)
def sandlot_index() -> HTMLResponse:
    html = (WEB_DIR / "index.html").read_text()
    app_js = WEB_DIR / "app.js"
    digest = hashlib.sha256(app_js.read_bytes()).hexdigest()[:12]
    html = html.replace("app.js?v=frontend-build", f"app.js?v={digest}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="sandlot")
