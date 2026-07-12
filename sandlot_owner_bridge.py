#!/usr/bin/env python3
"""Local owner-confirmation bridge for Sandlot dry-run execution requests.

The production browser never receives the owner bearer.  It sends one exact
immutable confirmation to this loopback-only process, which authenticates the
request upstream and returns only Sandlot's sanitized public request state.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

import requests


DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ORIGIN = "https://web-production-90664.up.railway.app"
MAX_BODY_BYTES = 64 * 1024
REQUEST_ID_RE = re.compile(r"xreq_[A-Za-z0-9_-]{20,80}\Z")
RECEIPT_ID_RE = re.compile(r"monday-lineup:[a-f0-9]{64}\Z")
PUBLIC_REQUEST_FIELDS = {
    "request_id", "mode", "snapshot_id", "proposal_id", "input_hash", "state",
    "created_at", "expires_at", "claimed_at", "completed_at", "failure_reason",
    "evidence", "writes_enabled", "created", "request_enabled",
}
PUBLIC_DECISION_FIELDS = {
    "receipt_id", "input_hash", "source", "action_type", "period", "evaluation",
    "baseline_assignment", "proposed_assignment", "unfilled_slots", "evidence",
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

        def _authorized_browser(self) -> bool:
            if not self._host_is_loopback() or not self._origin_allowed():
                self._send(403, {"detail": "Origin or loopback host is not allowed"})
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
            if not self._authorized_browser():
                return
            decision_prefix = "/recommendation-receipts/"
            is_execution = self.path == "/execution-requests"
            is_decision = self.path.startswith(decision_prefix) and self.path.endswith("/decision")
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
                receipt_id = unquote(self.path[len(decision_prefix):-len("/decision")])
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
    return None


def _sanitize_public_decision(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in PUBLIC_DECISION_FIELDS if key in body}


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
