"""FastAPI app for Sandlot v1."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import player_service
import sandlot_db
import sandlot_skipper
import sandlot_trades
import sandlot_waivers
from sandlot_refresh import run_refresh

log = logging.getLogger(__name__)

load_dotenv()

WEB_DIR = Path(__file__).parent / "web" / "sandlot"

app = FastAPI(title="Sandlot", version="0.1.0")


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


@app.post("/api/refresh")
def refresh(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    _require_refresh_token(request)
    result = run_refresh(source="manual")
    row = sandlot_db.latest_successful_snapshot()
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

    if result.snapshot_id and os.environ.get("SANDLOT_WAIVER_AI_WARM_DISABLED") != "1":
        background_tasks.add_task(sandlot_waivers.warm_latest_waiver_ai, result.snapshot_id)
    # Mirror the cron's profile warm so manual refresh doesn't leave every
    # cached take un-keyed against the new snapshot_id.
    if result.snapshot_id and os.environ.get("SANDLOT_PROFILE_WARM_DISABLED") != "1":
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


@app.get("/api/skipper/options")
def skipper_options() -> dict[str, Any]:
    return {
        "default_model": sandlot_skipper.primary_model(),
        "models": [
            {"id": "moonshotai/kimi-k2", "label": "Kimi K2", "primary": True},
            {"id": "tencent/hy3-preview:free", "label": "Tencent HY3 free"},
            {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
            {"id": "deepseek/deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
        ],
        "reasoning": {
            "default_enabled": False,
            "default_effort": "medium",
            "efforts": ["minimal", "low", "medium", "high"],
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

    snapshot = snapshot_row.get("data") or {}
    tier = sandlot_skipper.detect_tier(user_text, snapshot)
    context_block = sandlot_skipper.build_context(tier, snapshot, prompt=user_text)
    history = sandlot_db.list_chat_messages(session_id)
    messages = sandlot_skipper.build_messages(history, user_text, context_block)
    deterministic_reply = sandlot_skipper.deterministic_reply(user_text, snapshot)
    selected_model = payload.model
    model_order = sandlot_skipper.model_order(selected_model)
    reasoning_effort = sandlot_skipper.normalize_reasoning_effort(
        payload.reasoning_effort if payload.reasoning else None
    )

    sandlot_db.append_chat_message(session_id, "user", user_text)

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
            })
            return

        try:
            client = sandlot_skipper.SkipperClient()
        except Exception as exc:
            log.exception("Skipper client init failed")
            yield _sse({"type": "error", "message": str(exc)})
            return

        assistant_buf: list[str] = []
        used_model: str | None = None
        try:
            for kind, payload_text in client.stream(
                messages,
                model_order=model_order,
                reasoning_effort=reasoning_effort,
            ):
                if kind == "token":
                    assistant_buf.append(payload_text)
                elif kind == "model":
                    used_model = payload_text
        except Exception as exc:
            log.exception("Skipper stream failed")
            yield _sse({"type": "error", "message": str(exc)})
            return

        full = sandlot_skipper.repair_reply("".join(assistant_buf), user_text, snapshot)
        if full:
            yield _sse({"type": "token", "text": full})
            try:
                sandlot_db.append_chat_message(
                    session_id, "assistant", full, tier=tier, model=used_model
                )
            except Exception as exc:
                log.exception("Failed to persist assistant message")
        yield _sse({
            "type": "done",
            "tier": tier,
            "model": used_model,
            "selected_model": selected_model,
            "reasoning": bool(reasoning_effort),
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering for live streams
        },
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _snapshot_payload(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data") or {}
    roster_meta = data.get("roster") or {}
    standings = data.get("standings") or {}
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
        "matchup": data.get("matchup"),
        "player_index": _player_index(data),
        "errors": row.get("errors") or data.get("errors") or [],
    }


def _player_index(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat list of every player the snapshot knows about.

    Frontend uses this two ways:
    1. Skipper chat replies: lowercased-name -> fantrax_id map so full-name
       mentions become profile links even when the model forgets to emit a
       [[name|id]] tag.
    2. Trade tab pickers: filter by `source` ("mine" / "league" / "free_agent")
       to populate the give/get autocompletes.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(rows: Any, *, source: str, team_id: str | None = None) -> None:
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            pid = r.get("id")
            name = r.get("name")
            if not pid or not name or pid in seen:
                continue
            seen.add(pid)
            out.append({
                "id": pid,
                "name": name,
                "team": r.get("team"),
                "slot": r.get("slot"),
                "positions": r.get("positions"),
                "fppg": r.get("fppg"),
                "age": r.get("age"),
                "source": source,
                "team_id": team_id,
            })

    add((data.get("roster") or {}).get("rows"), source="mine",
        team_id=data.get("team_id"))
    for tid, team in (data.get("all_team_rosters") or {}).items():
        if not isinstance(team, dict):
            continue
        if team.get("is_me"):
            continue
        add(team.get("rows"), source="league", team_id=tid)
    add((data.get("free_agents") or {}).get("players"), source="free_agent")
    return out


def _freshness(taken_at: Any) -> dict[str, Any]:
    if not isinstance(taken_at, datetime):
        return {"state": "missing", "age_minutes": None}
    now = datetime.now(timezone.utc)
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((now - taken_at).total_seconds() / 60))
    if age_minutes <= 30:
        state = "fresh"
    elif age_minutes <= 24 * 60:
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


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="sandlot")
