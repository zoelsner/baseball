"""FastAPI app for Sandlot v1."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import sandlot_db
import sandlot_skipper
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
def refresh(request: Request) -> dict[str, Any]:
    _require_refresh_token(request)
    result = run_refresh(source="manual")
    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "status": result.status,
                "snapshot_id": result.snapshot_id,
                "duration_ms": result.duration_ms,
                "errors": result.errors,
            },
        )

    row = sandlot_db.latest_successful_snapshot()
    return jsonable_encoder(
        {
            "status": result.status,
            "snapshot_id": result.snapshot_id,
            "duration_ms": result.duration_ms,
            "snapshot": _snapshot_payload(row) if row else None,
        }
    )


class SkipperMessageIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


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
    context_block = sandlot_skipper.build_context(tier, snapshot)
    history = sandlot_db.list_chat_messages(session_id)
    messages = sandlot_skipper.build_messages(history, user_text, context_block)

    sandlot_db.append_chat_message(session_id, "user", user_text)

    def event_stream():
        try:
            client = sandlot_skipper.SkipperClient()
        except Exception as exc:
            log.exception("Skipper client init failed")
            yield _sse({"type": "error", "message": str(exc)})
            return

        assistant_buf: list[str] = []
        used_model: str | None = None
        try:
            for kind, payload_text in client.stream(messages):
                if kind == "token":
                    assistant_buf.append(payload_text)
                    yield _sse({"type": "token", "text": payload_text})
                elif kind == "model":
                    used_model = payload_text
        except Exception as exc:
            log.exception("Skipper stream failed")
            yield _sse({"type": "error", "message": str(exc)})
            return

        full = "".join(assistant_buf).strip()
        if full:
            try:
                sandlot_db.append_chat_message(
                    session_id, "assistant", full, tier=tier, model=used_model
                )
            except Exception as exc:
                log.exception("Failed to persist assistant message")
        yield _sse({"type": "done", "tier": tier, "model": used_model})

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
        "errors": row.get("errors") or data.get("errors") or [],
    }


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
