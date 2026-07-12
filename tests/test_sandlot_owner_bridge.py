import json
import threading
import unittest
from http.server import ThreadingHTTPServer

import requests

import sandlot_owner_bridge


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, responses=None):
        self.responses = list(responses or [FakeResponse(201, {
            "request_id": "xreq_abcdefghijklmnopqrstuvwxyz",
            "mode": "dry_run",
            "snapshot_id": 274,
            "proposal_id": "lineup:test",
            "input_hash": "a" * 64,
            "state": "pending",
            "writes_enabled": False,
        })])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class OwnerBridgeTests(unittest.TestCase):
    def make_bridge(self, http=None):
        return sandlot_owner_bridge.OwnerBridge(
            upstream="https://sandlot.example.test",
            owner_token="owner-secret-long-enough",
            allowed_origin="https://sandlot.example.test",
            http=http or FakeHttp(),
        )

    def test_upstream_request_keeps_owner_token_local_and_refuses_redirects(self):
        http = FakeHttp([FakeResponse(302, {})])
        bridge = self.make_bridge(http)
        payload = {
            "mode": "dry_run", "proposal_id": "lineup:test", "snapshot_id": 274, "input_hash": "a" * 64,
            "confirmation": {"proposal_id": "lineup:test", "snapshot_id": 274, "input_hash": "a" * 64, "target_period": {"period_number": 17}, "slot_moves": [{"player_id": "one"}]},
        }
        status, body = bridge.create(payload)

        self.assertEqual(status, 502)
        self.assertIn("redirect", body["detail"])
        _method, _url, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["authorization"], "Bearer owner-secret-long-enough")
        self.assertFalse(kwargs["allow_redirects"])

    def test_rejects_unsafe_upstream_and_short_owner_secret(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            sandlot_owner_bridge.OwnerBridge(
                upstream="http://sandlot.example.test",
                owner_token="owner-secret-long-enough",
                allowed_origin="https://sandlot.example.test",
            )
        with self.assertRaisesRegex(ValueError, "remain local"):
            sandlot_owner_bridge.OwnerBridge(
                upstream="https://sandlot.example.test",
                owner_token="short",
                allowed_origin="https://sandlot.example.test",
            )

    def test_loopback_http_surface_requires_exact_origin_and_nonce(self):
        http = FakeHttp()
        bridge = self.make_bridge(http)
        server = ThreadingHTTPServer(("127.0.0.1", 0), sandlot_owner_bridge.make_handler(bridge))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        allowed = {"Origin": "https://sandlot.example.test"}
        try:
            health = requests.get(f"{base}/health", headers=allowed, timeout=2)
            self.assertEqual(health.status_code, 200)
            nonce = health.json()["nonce"]
            self.assertFalse(health.json()["writes_enabled"])
            self.assertTrue(health.json()["recommendation_decisions_enabled"])
            self.assertNotIn("token", json.dumps(health.json()).casefold())

            health_preflight = requests.options(
                f"{base}/health",
                headers={
                    **allowed,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Private-Network": "true",
                },
                timeout=2,
            )
            self.assertEqual(health_preflight.status_code, 204)
            self.assertEqual(health_preflight.headers.get("Access-Control-Allow-Private-Network"), "true")

            blocked_origin = requests.get(
                f"{base}/health",
                headers={"Origin": "https://attacker.example"},
                timeout=2,
            )
            self.assertEqual(blocked_origin.status_code, 403)
            self.assertNotIn("Access-Control-Allow-Origin", blocked_origin.headers)

            missing_nonce = requests.post(
                f"{base}/execution-requests",
                headers={**allowed, "Content-Type": "application/json"},
                json={"mode": "dry_run"},
                timeout=2,
            )
            self.assertEqual(missing_nonce.status_code, 403)

            created = requests.post(
                f"{base}/execution-requests",
                headers={
                    **allowed,
                    "Content-Type": "application/json",
                    "X-Sandlot-Bridge-Nonce": nonce,
                },
                json={
                    "mode": "dry_run",
                    "proposal_id": "lineup:test",
                    "snapshot_id": 274,
                    "input_hash": "a" * 64,
                    "confirmation": {"proposal_id": "lineup:test", "snapshot_id": 274, "input_hash": "a" * 64, "target_period": {"period_number": 17}, "slot_moves": [{"player_id": "one"}]},
                },
                timeout=2,
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["request_id"], "xreq_abcdefghijklmnopqrstuvwxyz")
            self.assertEqual(http.calls[-1][0], "POST")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_decision_proxy_preserves_identity_and_no_write_boundary(self):
        receipt_id = f"monday-lineup:{'a' * 64}"
        payload = {"decision": "accepted", "input_hash": "a" * 64, "reason": "Using it"}
        upstream = {
            "receipt_id": receipt_id,
            "input_hash": "a" * 64,
            "source": "monday_lineup",
            "lifecycle_state": "active",
            "decision_state": "accepted",
            "decision_reason": "Using it",
            "fantrax_changed": False,
            "writes_enabled": False,
            "changed": True,
            "projection_inputs": [{"private": True}],
        }
        http = FakeHttp([FakeResponse(200, upstream)])
        bridge = self.make_bridge(http)

        status, body = bridge.decide(receipt_id, payload)

        self.assertEqual(status, 200)
        self.assertEqual(body["decision_state"], "accepted")
        self.assertNotIn("projection_inputs", body)
        method, url, kwargs = http.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"https://sandlot.example.test/api/recommendation-receipts/{receipt_id}/decision")
        self.assertEqual(kwargs["json"], payload)
        self.assertFalse(kwargs["allow_redirects"])

    def test_decision_proxy_rejects_malformed_input_and_mismatched_upstream(self):
        receipt_id = f"monday-lineup:{'a' * 64}"
        http = FakeHttp()
        bridge = self.make_bridge(http)
        invalid = [
            ("../execution-requests", {"decision": "accepted", "input_hash": "a" * 64}),
            (receipt_id, {"decision": "execute", "input_hash": "a" * 64}),
            (receipt_id, {"decision": "accepted", "input_hash": "short"}),
            (receipt_id, {"decision": "accepted", "input_hash": "a" * 64, "extra": True}),
        ]
        for candidate_id, payload in invalid:
            with self.subTest(candidate_id=candidate_id, payload=payload):
                status, _body = bridge.decide(candidate_id, payload)
                self.assertEqual(status, 400)
        self.assertEqual(http.calls, [])

        mismatch = FakeHttp([FakeResponse(200, {
            "receipt_id": receipt_id,
            "input_hash": "b" * 64,
            "lifecycle_state": "active",
            "decision_state": "accepted",
            "fantrax_changed": False,
            "writes_enabled": False,
        })])
        bridge = self.make_bridge(mismatch)
        status, body = bridge.decide(
            receipt_id,
            {"decision": "accepted", "input_hash": "a" * 64},
        )
        self.assertEqual(status, 502)
        self.assertIn("hash", body["detail"])

    def test_loopback_decision_route_requires_nonce_and_forwards_exact_body(self):
        receipt_id = f"monday-lineup:{'a' * 64}"
        upstream = {
            "receipt_id": receipt_id,
            "input_hash": "a" * 64,
            "lifecycle_state": "active",
            "decision_state": "rejected",
            "fantrax_changed": False,
            "writes_enabled": False,
        }
        http = FakeHttp([FakeResponse(200, upstream)])
        bridge = self.make_bridge(http)
        server = ThreadingHTTPServer(("127.0.0.1", 0), sandlot_owner_bridge.make_handler(bridge))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        allowed = {"Origin": "https://sandlot.example.test"}
        body = {"decision": "rejected", "input_hash": "a" * 64, "reason": "Prefer another plan"}
        try:
            browser_path = receipt_id.replace(":", "%3A")
            missing = requests.post(
                f"{base}/recommendation-receipts/{browser_path}/decision",
                headers={**allowed, "Content-Type": "application/json"},
                json=body,
                timeout=2,
            )
            self.assertEqual(missing.status_code, 403)
            nonce = requests.get(f"{base}/health", headers=allowed, timeout=2).json()["nonce"]
            decided = requests.post(
                f"{base}/recommendation-receipts/{browser_path}/decision",
                headers={
                    **allowed,
                    "Content-Type": "application/json",
                    "X-Sandlot-Bridge-Nonce": nonce,
                },
                json=body,
                timeout=2,
            )
            self.assertEqual(decided.status_code, 200)
            self.assertEqual(decided.json()["decision_state"], "rejected")
            self.assertEqual(http.calls[0][2]["json"], body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_status_rejects_path_traversal_and_unknown_request_ids_without_upstream_call(self):
        http = FakeHttp()
        bridge = self.make_bridge(http)
        for request_id in (
            "xreq_/../../../api/health",
            "xreq_%2f..%2fapi%2fhealth",
            "xreq_short",
            "xreq_abcdefghijklmnopqrst?query=1",
        ):
            with self.subTest(request_id=request_id):
                status, _body = bridge.status(request_id)
                self.assertEqual(status, 400)
        status, _body = bridge.status("xreq_abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(status, 404)
        self.assertEqual(http.calls, [])

    def test_passing_status_requires_explicit_zero_click_and_write_proof(self):
        request_id = "xreq_abcdefghijklmnopqrstuvwxyz"
        identity = {"proposal_id": "lineup:test", "snapshot_id": 274, "input_hash": "a" * 64}
        create_payload = {**identity, "mode": "dry_run", "confirmation": {**identity, "target_period": {"period_number": 17}, "slot_moves": [{"player_id": "one"}]}}
        http = FakeHttp([
            FakeResponse(201, {**identity, "request_id": request_id, "mode": "dry_run", "state": "pending", "writes_enabled": False}),
            FakeResponse(200, {**identity, "request_id": request_id, "mode": "dry_run", "state": "preflight_passed", "writes_enabled": False, "evidence": {}}),
        ])
        bridge = self.make_bridge(http)
        self.assertEqual(bridge.create(create_payload)[0], 201)
        status, body = bridge.status(request_id)
        self.assertEqual(status, 502)
        self.assertIn("zero Fantrax", body["detail"])

    def test_create_rejects_non_dry_run_and_mismatched_confirmation_locally(self):
        http = FakeHttp()
        bridge = self.make_bridge(http)
        status, _body = bridge.create({"mode": "execute"})
        self.assertEqual(status, 400)
        payload = {
            "mode": "dry_run", "proposal_id": "lineup:test", "snapshot_id": 274, "input_hash": "a" * 64,
            "confirmation": {"proposal_id": "other", "snapshot_id": 274, "input_hash": "a" * 64, "target_period": {"period_number": 17}, "slot_moves": [{"player_id": "one"}]},
        }
        status, _body = bridge.create(payload)
        self.assertEqual(status, 400)
        self.assertEqual(http.calls, [])


if __name__ == "__main__":
    unittest.main()
