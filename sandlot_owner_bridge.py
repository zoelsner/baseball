#!/usr/bin/env python3
"""Local owner-confirmation bridge for Sandlot dry-run execution requests.

The production browser never receives the owner bearer.  It sends one exact
immutable confirmation to this loopback-only process, which authenticates the
request upstream and returns only Sandlot's sanitized public request state.
"""

from __future__ import annotations

import argparse
import hmac
import html
import json
import os
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

import requests


DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ORIGIN = "https://web-production-90664.up.railway.app"
MAX_BODY_BYTES = 64 * 1024
REQUEST_ID_RE = re.compile(r"xreq_[A-Za-z0-9_-]{20,80}\Z")
RECEIPT_ID_RE = re.compile(r"(?:monday-lineup|trade-assessment):[a-f0-9]{64}\Z")
PUBLIC_REQUEST_FIELDS = {
    "request_id", "mode", "snapshot_id", "proposal_id", "input_hash", "state",
    "created_at", "expires_at", "claimed_at", "completed_at", "failure_reason",
    "evidence", "writes_enabled", "created", "request_enabled",
}
PUBLIC_DECISION_FIELDS = {
    "receipt_id", "input_hash", "source", "action_type", "period", "evaluation",
    "baseline_assignment", "proposed_assignment", "unfilled_slots", "evidence",
    "trade",
    "lifecycle_state", "decision_state", "decision_reason", "decided_at",
    "outcome_state", "generated_at", "expires_at", "read_only",
    "fantrax_changed", "writes_enabled", "changed",
}


def validate_upstream(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Sandlot upstream must be an uncredentialed HTTPS origin")
    return raw


def validate_allowed_origin(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("Allowed browser origin must be an exact HTTP(S) origin")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Plain HTTP is allowed only for a loopback development origin")
    return raw


class OwnerBridge:
    def __init__(self, *, upstream: str, owner_token: str, allowed_origin: str, http: Any = requests):
        self.upstream = validate_upstream(upstream)
        self.allowed_origin = validate_allowed_origin(allowed_origin)
        self.owner_token = str(owner_token or "")
        if len(self.owner_token) < 16:
            raise ValueError("SANDLOT_OWNER_ACTION_TOKEN is required and must remain local")
        self.http = http
        self.nonce = secrets.token_urlsafe(24)
        self.requests: dict[str, dict[str, Any]] = {}

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "dry_run",
            "writes_enabled": False,
            "recommendation_decisions_enabled": True,
            "nonce": self.nonce,
        }

    def create(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        validation_error = _validate_create_payload(payload)
        if validation_error:
            return 400, {"detail": validation_error}
        status, body = self._request("POST", "/api/execution-requests", json=payload)
        if status not in {200, 201}:
            return status, body
        expected = _request_identity(payload)
        error = _validate_public_request(body, expected=expected)
        if error:
            return 502, {"detail": error}
        request_id = str(body["request_id"])
        self.requests[request_id] = expected
        return status, _sanitize_public_request(body)

    def status(self, request_id: str) -> tuple[int, dict[str, Any]]:
        if not REQUEST_ID_RE.fullmatch(request_id):
            return 400, {"detail": "Invalid execution request id"}
        expected = self.requests.get(request_id)
        if expected is None:
            return 404, {"detail": "Execution request was not created by this bridge process"}
        status, body = self._request("GET", f"/api/execution-requests/{request_id}")
        if status != 200:
            return status, body
        error = _validate_public_request(body, expected=expected, request_id=request_id)
        if error:
            return 502, {"detail": error}
        return status, _sanitize_public_request(body)

    def decide(self, receipt_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not RECEIPT_ID_RE.fullmatch(receipt_id):
            return 400, {"detail": "Invalid recommendation receipt id"}
        validation_error = _validate_decision_payload(payload)
        if validation_error:
            return 400, {"detail": validation_error}
        status, body = self._request(
            "POST",
            f"/api/recommendation-receipts/{receipt_id}/decision",
            json=payload,
        )
        if status != 200:
            return status, {"detail": str(body.get("detail") or "Recommendation decision failed")}
        error = _validate_public_decision(body, receipt_id=receipt_id, payload=payload)
        if error:
            return 502, {"detail": error}
        return status, _sanitize_public_decision(body)

    def recommendation(self, receipt_id: str, input_hash: str) -> tuple[int, dict[str, Any]]:
        if not RECEIPT_ID_RE.fullmatch(receipt_id) or not receipt_id.startswith("monday-lineup:"):
            return 400, {"detail": "Invalid lineup recommendation receipt id"}
        if not re.fullmatch(r"[A-Fa-f0-9]{64}", input_hash):
            return 400, {"detail": "Recommendation review hash is malformed"}
        status, body = self._request("GET", "/api/recommendation-receipts/latest")
        if status != 200:
            return 409 if status in {204, 404} else status, {
                "detail": str(body.get("detail") or "No active lineup recommendation is available")
            }
        error = _validate_review_receipt(body, receipt_id=receipt_id, input_hash=input_hash)
        if error:
            return 409, {"detail": error}
        return 200, _sanitize_public_decision(body)

    def _request(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        try:
            response = self.http.request(
                method,
                f"{self.upstream}{path}",
                headers={"authorization": f"Bearer {self.owner_token}"},
                timeout=20,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException:
            return 502, {"detail": "Sandlot upstream is unavailable"}
        status = int(getattr(response, "status_code", 502) or 502)
        if 300 <= status < 400:
            return 502, {"detail": "Owner bridge refused an upstream redirect"}
        try:
            body = response.json()
        except Exception:
            body = {"detail": f"Sandlot returned HTTP {status}"}
        if not isinstance(body, dict):
            body = {"detail": f"Sandlot returned HTTP {status}"}
        return status, body

    def nonce_matches(self, value: str | None) -> bool:
        return bool(value) and hmac.compare_digest(str(value), self.nonce)


def make_handler(bridge: OwnerBridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SandlotOwnerBridge/1"

        def _origin_allowed(self) -> bool:
            return self.headers.get("Origin", "").rstrip("/") == bridge.allowed_origin

        def _host_is_loopback(self) -> bool:
            try:
                host = (urlsplit(f"//{self.headers.get('Host', '')}").hostname or "").casefold()
            except ValueError:
                host = ""
            return host in {"localhost", "127.0.0.1", "::1"}

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", bridge.allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "content-type, x-sandlot-bridge-nonce")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            if self._origin_allowed():
                self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, status: int, markup: str) -> None:
            raw = markup.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized_browser(self) -> bool:
            if not self._host_is_loopback() or not self._origin_allowed():
                self._send(403, {"detail": "Origin or loopback host is not allowed"})
                return False
            return True

        def _authorized_local_navigation(self) -> bool:
            if not self._host_is_loopback():
                self._send_html(403, _review_error_page("This review page is available only on this Mac."))
                return False
            mode = self.headers.get("Sec-Fetch-Mode", "")
            destination = self.headers.get("Sec-Fetch-Dest", "")
            if mode not in {"", "navigate"} or destination not in {"", "document"}:
                self._send_html(403, _review_error_page("Open this review as a normal browser page."))
                return False
            return True

        def _authorized_local_form(self) -> bool:
            if not self._host_is_loopback():
                self._send_html(403, _review_error_page("This decision can be recorded only on this Mac."))
                return False
            expected_origin = f"http://{self.headers.get('Host', '')}".rstrip("/")
            if self.headers.get("Origin", "").rstrip("/") != expected_origin:
                self._send_html(403, _review_error_page("The local decision form origin did not match this bridge."))
                return False
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._authorized_browser():
                return
            requested_method = self.headers.get("Access-Control-Request-Method", "")
            requested_headers = self.headers.get("Access-Control-Request-Headers", "").casefold()
            health_probe = self.path == "/health" and requested_method == "GET"
            authenticated_request = (
                requested_method in {"GET", "POST"}
                and "x-sandlot-bridge-nonce" in requested_headers
            )
            if not health_probe and not authenticated_request:
                self._send(403, {"detail": "Bridge preflight is incomplete"})
                return
            self.send_response(204)
            self._cors()
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            review_prefix = "/recommendation-receipts/"
            if parsed.path.startswith(review_prefix) and parsed.path.endswith("/review"):
                if not self._authorized_local_navigation():
                    return
                receipt_id = unquote(parsed.path[len(review_prefix):-len("/review")])
                query = parse_qs(parsed.query, keep_blank_values=True)
                input_hash = str((query.get("input_hash") or [""])[0])
                status, payload = bridge.recommendation(receipt_id, input_hash)
                if status != 200:
                    self._send_html(status, _review_error_page(str(payload.get("detail") or "Review unavailable")))
                    return
                self._send_html(200, _review_page(payload, nonce=bridge.nonce))
                return
            if not self._authorized_browser():
                return
            if self.path == "/health":
                self._send(200, bridge.health())
                return
            if not bridge.nonce_matches(self.headers.get("X-Sandlot-Bridge-Nonce")):
                self._send(403, {"detail": "Bridge nonce is missing or stale"})
                return
            prefix = "/execution-requests/"
            if self.path.startswith(prefix):
                status, payload = bridge.status(self.path[len(prefix):])
                self._send(status, payload)
                return
            self._send(404, {"detail": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            review_prefix = "/recommendation-receipts/"
            is_review = parsed.path.startswith(review_prefix) and parsed.path.endswith("/review")
            if is_review:
                if not self._authorized_local_form():
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length < 2 or length > MAX_BODY_BYTES:
                    self._send_html(413, _review_error_page("Decision form size is invalid."))
                    return
                if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                    self._send_html(415, _review_error_page("Decision form encoding is invalid."))
                    return
                try:
                    form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                except Exception:
                    self._send_html(400, _review_error_page("Decision form is invalid."))
                    return
                nonce = str((form.get("nonce") or [""])[0])
                if not bridge.nonce_matches(nonce):
                    self._send_html(403, _review_error_page("This review expired. Reopen it from Sandlot."))
                    return
                receipt_id = unquote(parsed.path[len(review_prefix):-len("/review")])
                decision = str((form.get("decision") or [""])[0])
                input_hash = str((form.get("input_hash") or [""])[0])
                status, result = bridge.decide(receipt_id, {"decision": decision, "input_hash": input_hash})
                if status != 200:
                    self._send_html(status, _review_error_page(str(result.get("detail") or "Decision was not recorded.")))
                    return
                message = "Decision recorded. Fantrax was not changed."
                self._send_html(200, _review_page(result, nonce=bridge.nonce, message=message))
                return
            if not self._authorized_browser():
                return
            decision_prefix = "/recommendation-receipts/"
            is_execution = parsed.path == "/execution-requests"
            is_decision = parsed.path.startswith(decision_prefix) and parsed.path.endswith("/decision")
            if not is_execution and not is_decision:
                self._send(404, {"detail": "Not found"})
                return
            if not bridge.nonce_matches(self.headers.get("X-Sandlot-Bridge-Nonce")):
                self._send(403, {"detail": "Bridge nonce is missing or stale"})
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, {"detail": "JSON content type is required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 2 or length > MAX_BODY_BYTES:
                self._send(413, {"detail": "Request body size is invalid"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except Exception:
                self._send(400, {"detail": "Request body is not valid JSON"})
                return
            if not isinstance(payload, dict):
                self._send(400, {"detail": "Request body must be an object"})
                return
            if is_execution:
                status, result = bridge.create(payload)
            else:
                receipt_id = unquote(parsed.path[len(decision_prefix):-len("/decision")])
                status, result = bridge.decide(receipt_id, payload)
            self._send(status, result)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def _request_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": str(payload.get("proposal_id") or ""),
        "snapshot_id": payload.get("snapshot_id"),
        "input_hash": str(payload.get("input_hash") or ""),
    }


def _validate_create_payload(payload: dict[str, Any]) -> str | None:
    if payload.get("mode") != "dry_run":
        return "Owner bridge accepts dry_run requests only"
    identity = _request_identity(payload)
    if (
        not identity["proposal_id"]
        or not isinstance(identity["snapshot_id"], int)
        or not re.fullmatch(r"[A-Fa-f0-9]{64}", identity["input_hash"])
    ):
        return "Exact proposal identity is incomplete or malformed"
    confirmation = payload.get("confirmation") if isinstance(payload.get("confirmation"), dict) else {}
    if (
        str(confirmation.get("proposal_id") or "") != identity["proposal_id"]
        or confirmation.get("snapshot_id") != identity["snapshot_id"]
        or str(confirmation.get("input_hash") or "") != identity["input_hash"]
        or not isinstance(confirmation.get("target_period"), dict)
        or not isinstance(confirmation.get("slot_moves"), list)
        or not confirmation.get("slot_moves")
    ):
        return "Exact confirmation does not match the proposal identity"
    return None


def _validate_decision_payload(payload: dict[str, Any]) -> str | None:
    if set(payload) - {"decision", "input_hash", "reason"}:
        return "Recommendation decision contains unsupported fields"
    if payload.get("decision") not in {"accepted", "rejected"}:
        return "Recommendation decision must be accepted or rejected"
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", str(payload.get("input_hash") or "")):
        return "Recommendation decision hash is malformed"
    reason = payload.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 240):
        return "Recommendation decision reason is malformed"
    return None


def _validate_public_decision(
    body: dict[str, Any],
    *,
    receipt_id: str,
    payload: dict[str, Any],
) -> str | None:
    if body.get("fantrax_changed") is not False or body.get("writes_enabled") is not False:
        return "Sandlot response did not preserve the no-Fantrax-write boundary"
    if str(body.get("receipt_id") or "") != receipt_id:
        return "Sandlot response returned a mismatched recommendation receipt id"
    if str(body.get("input_hash") or "").casefold() != str(payload["input_hash"]).casefold():
        return "Sandlot response returned a mismatched recommendation hash"
    if body.get("lifecycle_state") != "active":
        return "Sandlot response returned a non-active recommendation receipt"
    if body.get("decision_state") != payload["decision"]:
        return "Sandlot response did not record the exact recommendation decision"
    if receipt_id.startswith("trade-assessment:"):
        trade = body.get("trade") if isinstance(body.get("trade"), dict) else {}
        guardrails = trade.get("guardrails") if isinstance(trade.get("guardrails"), dict) else {}
        if body.get("source") != "trade_cockpit" or body.get("action_type") != "trade_assessment":
            return "Sandlot response returned contradictory trade receipt identity"
        if guardrails.get("manual_execution_only") is not True or guardrails.get("fantrax_write_authorized") is not False:
            return "Sandlot response did not preserve manual-only trade guardrails"
    return None


def _sanitize_public_decision(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in PUBLIC_DECISION_FIELDS if key in body}


def _validate_review_receipt(body: dict[str, Any], *, receipt_id: str, input_hash: str) -> str | None:
    if body.get("fantrax_changed") is not False or body.get("writes_enabled") is not False:
        return "Sandlot did not preserve the no-Fantrax-write review boundary"
    if body.get("read_only") is not True:
        return "Sandlot did not return a read-only recommendation receipt"
    if str(body.get("receipt_id") or "") != receipt_id:
        return "A newer lineup recommendation is available. Reopen the review from Sandlot."
    if str(body.get("input_hash") or "").casefold() != input_hash.casefold():
        return "The lineup recommendation changed. Reopen the review from Sandlot."
    if body.get("source") != "monday_lineup" or body.get("lifecycle_state") != "active":
        return "This lineup recommendation is no longer active."
    if body.get("decision_state") not in {"pending", "accepted", "rejected"}:
        return "The lineup recommendation has an invalid decision state."
    if not isinstance(body.get("period"), dict) or not isinstance(body.get("evaluation"), dict):
        return "The lineup recommendation is missing its measurable period or impact."
    if not isinstance(body.get("baseline_assignment"), list) or not isinstance(body.get("proposed_assignment"), list):
        return "The lineup recommendation is missing its exact assignments."
    for label in ("baseline_assignment", "proposed_assignment"):
        seen: set[str] = set()
        for item in body[label]:
            if not isinstance(item, dict):
                return f"The lineup recommendation contains an invalid {label.replace('_', ' ')} row."
            player_id = str(item.get("player_id") or "").strip()
            player_name = str(item.get("player_name") or "").strip()
            slot = str(item.get("slot") or "").strip()
            if not player_id or not player_name or not slot:
                return f"The lineup recommendation contains an incomplete {label.replace('_', ' ')} row."
            if player_id in seen:
                return f"The lineup recommendation contains a duplicate player in its {label.replace('_', ' ')}."
            seen.add(player_id)
    unfilled = body.get("unfilled_slots")
    if not isinstance(unfilled, list) or any(not isinstance(slot, str) or not slot.strip() for slot in unfilled):
        return "The lineup recommendation is missing its exact unfilled slots."
    baseline_slots = {
        str(item["player_id"]): str(item["slot"])
        for item in body["baseline_assignment"]
    }
    proposed_slots = {
        str(item["player_id"]): str(item["slot"])
        for item in body["proposed_assignment"]
    }
    if baseline_slots == proposed_slots and not unfilled:
        return "The lineup recommendation does not contain a reviewable assignment change."
    return None


def _review_page(receipt: dict[str, Any], *, nonce: str, message: str | None = None) -> str:
    receipt_id = str(receipt.get("receipt_id") or "")
    input_hash = str(receipt.get("input_hash") or "")
    period = receipt.get("period") if isinstance(receipt.get("period"), dict) else {}
    evaluation = receipt.get("evaluation") if isinstance(receipt.get("evaluation"), dict) else {}
    baseline = receipt.get("baseline_assignment") if isinstance(receipt.get("baseline_assignment"), list) else []
    proposed = receipt.get("proposed_assignment") if isinstance(receipt.get("proposed_assignment"), list) else []
    unfilled = receipt.get("unfilled_slots") if isinstance(receipt.get("unfilled_slots"), list) else []
    baseline_by_player = {
        str(item.get("player_id") or ""): item
        for item in baseline if isinstance(item, dict) and item.get("player_id")
    }
    proposed_by_player = {
        str(item.get("player_id") or ""): item
        for item in proposed if isinstance(item, dict) and item.get("player_id")
    }
    starts: list[str] = []
    benches: list[str] = []
    slot_changes: list[str] = []
    inactive_slots = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "MIN", "MINORS"}
    for item in proposed:
        if not isinstance(item, dict) or not item.get("player_id"):
            continue
        player_id = str(item.get("player_id"))
        destination = str(item.get("slot") or "—")
        baseline_item = baseline_by_player.get(player_id)
        if baseline_item is None:
            starts.append(f"Start {str(item.get('player_name') or player_id)} in {destination}")
            continue
        origin = str(baseline_item.get("slot") or "—")
        if origin == destination:
            continue
        player_name = str(item.get("player_name") or player_id)
        if origin.upper() in inactive_slots and destination.upper() not in inactive_slots:
            starts.append(f"Start {player_name} in {destination}")
            continue
        if origin.upper() not in inactive_slots and destination.upper() in inactive_slots:
            benches.append(f"Bench {player_name} from {origin}")
            continue
        slot_changes.append(f"{player_name}: {origin} → {destination}")
    for item in baseline:
        if not isinstance(item, dict) or not item.get("player_id"):
            continue
        player_id = str(item.get("player_id"))
        if player_id in proposed_by_player:
            continue
        benches.append(f"Bench {str(item.get('player_name') or player_id)} from {str(item.get('slot') or 'lineup')}")
    lineup_holes = [f"Leave {str(slot)} unfilled (lineup hole)" for slot in unfilled]
    changes = starts + benches + slot_changes + lineup_holes
    gain = evaluation.get("projected_gain")
    try:
        gain_label = f"{float(gain):+.1f} projected points"
    except (TypeError, ValueError):
        gain_label = "Projected impact unavailable"
    period_label = " → ".join(
        value for value in (str(period.get("start") or ""), str(period.get("end") or "")) if value
    ) or "Current scoring period"
    change_markup = "".join(f"<li>{html.escape(change)}</li>" for change in changes)
    if not change_markup:
        change_markup = "<li>Keep the current assignment.</li>"
    decision_state = str(receipt.get("decision_state") or "pending")
    status_label = {
        "accepted": "Using this plan",
        "rejected": "Passed on this plan",
    }.get(decision_state, "Awaiting your call")
    action = f"/recommendation-receipts/{quote(receipt_id, safe='')}/review"
    controls = ""
    if decision_state == "pending":
        hidden = (
            f'<input type="hidden" name="nonce" value="{html.escape(nonce, quote=True)}">'
            f'<input type="hidden" name="input_hash" value="{html.escape(input_hash, quote=True)}">'
        )
        controls = f"""
          <div class="actions">
            <form method="post" action="{action}">{hidden}<input type="hidden" name="decision" value="accepted"><button class="primary" type="submit">I’ll use this lineup</button></form>
            <form method="post" action="{action}">{hidden}<input type="hidden" name="decision" value="rejected"><button class="secondary" type="submit">Pass</button></form>
          </div>
        """
    notice = f'<div class="success" role="status">{html.escape(message)}</div>' if message else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sandlot lineup review</title>
<style>
  :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background:#efe8dc; color:#0f172a; }}
  * {{ box-sizing:border-box; }} body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:24px; }}
  main {{ width:min(100%,560px); background:#fffaf2; border:1px solid #e2d7c6; border-radius:24px; padding:22px; box-shadow:0 18px 48px rgba(31,20,12,.10); }}
  .eyebrow {{ color:#df7042; font-size:11px; font-weight:850; letter-spacing:.11em; text-transform:uppercase; }}
  h1 {{ margin:8px 0 0; font-family:Georgia,'Times New Roman',serif; font-size:30px; line-height:1.02; letter-spacing:-.025em; }}
  .impact {{ margin-top:16px; display:flex; justify-content:space-between; gap:14px; padding:13px 14px; border-radius:16px; background:#f1e8da; font-weight:800; }}
  .impact span:last-child {{ color:#df7042; }}
  ul {{ margin:14px 0 0; padding:0; list-style:none; border-top:1px solid #eadfce; }}
  li {{ padding:11px 0; border-bottom:1px solid #eadfce; color:#334155; font-size:14px; font-weight:700; }}
    .boundary,.success {{ margin-top:14px; border-radius:14px; padding:12px 13px; font-size:13px; line-height:1.45; font-weight:750; }}
  .boundary {{ background:#f8dfce; color:#334155; }} .success {{ background:#dcf2e3; color:#14532d; }}
  .status {{ margin-top:13px; color:#64748b; font-size:12px; font-weight:800; }}
  .actions {{ margin-top:16px; display:grid; grid-template-columns:1fr 1fr; gap:9px; }} form {{ margin:0; }}
  button {{ width:100%; min-height:48px; border-radius:999px; padding:12px 14px; font:inherit; font-size:13px; font-weight:850; cursor:pointer; }}
  .primary {{ border:1px solid #0f172a; background:#0f172a; color:#fff; }} .secondary {{ border:1px solid #e2d7c6; background:#fffaf2; color:#334155; }}
  @media (max-width:440px) {{ body {{ padding:14px; }} main {{ padding:18px; }} .actions {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
  <div class="eyebrow">Local owner review</div><h1>Review this lineup plan</h1>
  <div class="impact"><span>{html.escape(period_label)}</span><span>{html.escape(gain_label)}</span></div>
  <ul>{change_markup}</ul>
  <div class="boundary">Recording your intent only. This page cannot change Fantrax, and the owner token stays on this Mac.</div>
  <div class="status">{html.escape(status_label)}</div>{notice}{controls}
</main></body></html>"""


def _review_error_page(message: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sandlot review unavailable</title>
<style>:root{{font-family:Inter,system-ui,sans-serif;background:#efe8dc;color:#0f172a}}body{{min-height:100vh;margin:0;display:grid;place-items:center;padding:24px}}main{{max-width:520px;background:#fffaf2;border:1px solid #e2d7c6;border-radius:24px;padding:22px}}h1{{margin:0;font-family:Georgia,serif}}p{{color:#334155;line-height:1.5;font-weight:650}}</style></head><body><main><h1>Review unavailable</h1><p>{html.escape(message)}</p><p>Return to Sandlot and reopen the latest lineup plan.</p></main></body></html>"""


def _validate_public_request(
    body: dict[str, Any],
    *,
    expected: dict[str, Any],
    request_id: str | None = None,
) -> str | None:
    if body.get("mode") != "dry_run" or body.get("writes_enabled") is not False:
        return "Sandlot response did not preserve the dry-run/write-disabled boundary"
    returned_id = str(body.get("request_id") or "")
    if not REQUEST_ID_RE.fullmatch(returned_id) or (request_id and returned_id != request_id):
        return "Sandlot response returned an invalid or mismatched request id"
    if (
        str(body.get("proposal_id") or "") != expected["proposal_id"]
        or body.get("snapshot_id") != expected["snapshot_id"]
        or str(body.get("input_hash") or "") != expected["input_hash"]
    ):
        return "Sandlot response identity did not match the exact confirmed proposal"
    if body.get("state") == "preflight_passed":
        report = body.get("evidence") if isinstance(body.get("evidence"), dict) else {}
        proof = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
        if (
            report.get("writes_attempted") is not False
            or proof.get("fantrax_click_count") != 0
            or proof.get("fantrax_write_count") != 0
        ):
            return "Passing preflight response did not prove zero Fantrax clicks and writes"
    return None


def _sanitize_public_request(body: dict[str, Any]) -> dict[str, Any]:
    sanitized = {key: body[key] for key in PUBLIC_REQUEST_FIELDS if key in body and key != "evidence"}
    report = body.get("evidence") if isinstance(body.get("evidence"), dict) else {}
    proof = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    sanitized["evidence"] = {
        "outcome": report.get("outcome"),
        "writes_attempted": report.get("writes_attempted"),
        "observed_at": report.get("observed_at"),
        "evidence": {
            "source": proof.get("source"),
            "fantrax_click_count": proof.get("fantrax_click_count"),
            "fantrax_write_count": proof.get("fantrax_write_count"),
        },
    } if report else {}
    return sanitized


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default=os.environ.get("SANDLOT_URL", DEFAULT_ORIGIN))
    parser.add_argument("--allowed-origin", default=os.environ.get("SANDLOT_BROWSER_ORIGIN", DEFAULT_ORIGIN))
    parser.add_argument("--bind", default=DEFAULT_BIND, choices=["127.0.0.1", "::1"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    bridge = OwnerBridge(
        upstream=args.upstream,
        owner_token=os.environ.get("SANDLOT_OWNER_ACTION_TOKEN", ""),
        allowed_origin=args.allowed_origin,
    )
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(bridge))
    print(f"Sandlot owner bridge ready on http://{args.bind}:{args.port}; dry-run only; writes disabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
