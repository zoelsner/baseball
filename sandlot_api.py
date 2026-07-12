"""FastAPI app for Sandlot v1."""

from __future__ import annotations

import json
import logging
import os
import hashlib
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
import sandlot_skipper
import sandlot_trades
import sandlot_waivers
import sandlot_win_week

log = logging.getLogger(__name__)

load_dotenv()

WEB_DIR = Path(__file__).parent / "web" / "sandlot"
FRESH_SNAPSHOT_MINUTES = 18 * 60
OLD_SNAPSHOT_MINUTES = 36 * 60

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
    return jsonable_encoder(_public_recommendation_receipt(row))


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
    result = _public_recommendation_receipt(row)
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


@app.post("/api/trades/grade")
def grade_trade(payload: TradeGradeIn) -> dict[str, Any]:
    try:
        snapshot_row = sandlot_db.latest_successful_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    if not snapshot_row:
        raise HTTPException(status_code=409, detail="No Fantrax snapshot yet — run a refresh first")
    try:
        result = sandlot_trades.grade_offer(snapshot_row, payload.give, payload.get)
    except sandlot_trades.TradeGradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Trade grade failed")
        raise HTTPException(status_code=500, detail=f"Trade grade failed: {exc}") from exc
    return jsonable_encoder(result)


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
            for kind, payload_text in client.stream(
                messages,
                model_order=model_order,
                reasoning_effort=reasoning_effort,
                web_search=use_web_search,
            ):
                if kind == "token":
                    assistant_buf.append(payload_text)
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
        if sandlot_skipper.is_broken_reply(raw) and full:
            # The streamed text was a broken refusal; tell the frontend to swap
            # in the deterministic explanation that replaces it.
            yield _sse({"type": "replace", "text": full})
        if full:
            try:
                sandlot_db.append_chat_message(
                    session_id, "assistant", full, tier=tier, model=used_model
                )
            except Exception as exc:
                log.exception("Failed to persist assistant message")
        sources = list(sources_by_url.values())
        if sources:
            yield _sse({"type": "sources", "sources": sources})
        yield _sse({
            "type": "done",
            "tier": tier,
            "model": used_model,
            "selected_model": selected_model,
            "reasoning": bool(reasoning_effort),
            "web_search_requested": use_web_search,
            "web_search": bool(sources or web_search_requests),
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


def _public_recommendation_receipt(row: dict[str, Any]) -> dict[str, Any]:
    recommendation = row.get("recommendation") if isinstance(row.get("recommendation"), dict) else {}
    evaluation = recommendation.get("evaluation") if isinstance(recommendation.get("evaluation"), dict) else {}
    snapshot = recommendation.get("snapshot") if isinstance(recommendation.get("snapshot"), dict) else {}
    period = recommendation.get("period") if isinstance(recommendation.get("period"), dict) else {}
    return {
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
        "generated_at": row.get("generated_at"),
        "expires_at": row.get("expires_at"),
        "read_only": True,
        "fantrax_changed": False,
        "writes_enabled": False,
    }


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
