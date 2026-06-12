import os
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import sandlot_actions
import sandlot_api


@contextmanager
def fake_lock(_lock_id, locked=True):
    yield locked


class FakeExecutor:
    def __init__(self, *, cookies):
        self.cookies = cookies

    def move_to_il(self, player_id, *, player_row=None):
        return sandlot_actions.ActionResult(
            ok=True,
            action="move_to_il",
            player_name=(player_row or {}).get("name"),
            detail={"from_slot": (player_row or {}).get("slot"), "to_slot": "IL"},
            selenium_state={"mock": True, "player_id": player_id},
        )

    def add_free_agent(self, player_id, *, player_row=None, move_out_player_id=None, move_out_player_row=None):
        detail = {}
        if move_out_player_id:
            detail["move_out_player_id"] = move_out_player_id
            detail["move_out_player_name"] = (move_out_player_row or {}).get("name")
        return sandlot_actions.ActionResult(
            ok=True,
            action="add_free_agent",
            player_name=(player_row or {}).get("name"),
            detail=detail or None,
            selenium_state={"mock": True, "player_id": player_id},
        )

    def drop_player(self, player_id, *, player_row=None):
        return sandlot_actions.ActionResult(
            ok=True,
            action="drop_player",
            player_name=(player_row or {}).get("name"),
            selenium_state={"mock": True, "player_id": player_id},
        )

    def change_slot(self, player_id, to_slot, *, player_row=None):
        return sandlot_actions.ActionResult(
            ok=True,
            action="change_slot",
            player_name=(player_row or {}).get("name"),
            detail={"from_slot": (player_row or {}).get("slot"), "to_slot": to_slot},
            selenium_state={"mock": True, "player_id": player_id},
        )


def sample_snapshot(*, full=False):
    active = 10 if full else 8
    reserve = 5 if full else 4
    return {
        "id": 77,
        "data": {
            "team_id": "team-me",
            "roster": {
                "active": active,
                "active_max": 10,
                "reserve": reserve,
                "reserve_max": 5,
                "rows": [
                    {"id": "injured-1", "name": "Injured Player", "slot": "BN", "injury": "IL"},
                    {"id": "healthy-1", "name": "Healthy Player", "slot": "2B", "injury": None},
                    {"id": "drop-1", "name": "Drop Candidate", "slot": "BN", "injury": None},
                ],
            },
            "free_agents": {
                "players": [
                    {"id": "fa-1", "name": "Free Agent", "team": "SEA", "positions": "OF"},
                ],
            },
        },
    }


class SandlotActionsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(sandlot_api.app, raise_server_exceptions=False)

    def post_action(
        self,
        body,
        *,
        token="secret",
        snapshot=None,
        locked=True,
        session_failure=None,
    ):
        env = dict(os.environ)
        env["SANDLOT_ACTIONS_TOKEN"] = "secret"
        headers = {"x-actions-token": token} if token is not None else {}
        session_context = sandlot_actions.SessionContext(
            session=Mock(),
            cookies=[{"name": "JSESSIONID", "value": "ok"}],
            source="test",
        )
        validate_session = Mock(return_value=session_context)
        if session_failure is not None:
            validate_session = Mock(side_effect=session_failure)

        with patch.dict(os.environ, env, clear=True), \
            patch("sandlot_api.sandlot_db.advisory_lock", lambda lock_id: fake_lock(lock_id, locked)), \
            patch("sandlot_api.sandlot_db.latest_successful_snapshot", return_value=snapshot or sample_snapshot()), \
            patch("sandlot_api.sandlot_db.insert_action_log") as insert_log, \
            patch("sandlot_actions.validate_session_fresh", validate_session), \
            patch("sandlot_actions.FantraxActionExecutor", FakeExecutor):
            response = self.client.post("/api/actions", json=body, headers=headers)
        return response, insert_log, validate_session

    def test_invalid_action_type_returns_400(self):
        response, insert_log, _ = self.post_action({"action": "trade_player", "player_id": "p1"})

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Invalid action type")
        self.assertIn("duration_ms", payload)
        self.assertTrue(insert_log.called)

    def test_missing_action_returns_400(self):
        response, insert_log, _ = self.post_action({"player_id": "p1"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "action is required")
        self.assertTrue(insert_log.called)

    def test_missing_player_id_returns_400(self):
        response, insert_log, _ = self.post_action({"action": "move_to_il"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "player_id is required")
        self.assertTrue(insert_log.called)

    def test_wrong_token_returns_401(self):
        response, _, _ = self.post_action({"action": "move_to_il", "player_id": "injured-1"}, token="wrong")

        self.assertEqual(response.status_code, 401)

    def test_locked_returns_409(self):
        response, insert_log, _ = self.post_action(
            {"action": "move_to_il", "player_id": "injured-1"},
            locked=False,
        )

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Refresh in progress, retry in 60s")
        self.assertTrue(insert_log.called)

    def test_il_move_on_healthy_player_returns_400(self):
        response, insert_log, validate_session = self.post_action(
            {"action": "move_to_il", "player_id": "healthy-1"},
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("IL-eligible", payload["error"])
        self.assertFalse(validate_session.called)
        self.assertTrue(insert_log.called)

    def test_add_when_roster_full_without_move_out_returns_400(self):
        response, insert_log, validate_session = self.post_action(
            {"action": "add_free_agent", "player_id": "fa-1"},
            snapshot=sample_snapshot(full=True),
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("Roster is full", payload["error"])
        self.assertFalse(validate_session.called)
        self.assertTrue(insert_log.called)

    def test_drop_with_wrong_confirm_name_returns_400(self):
        response, insert_log, validate_session = self.post_action(
            {
                "action": "drop_player",
                "player_id": "drop-1",
                "confirm_player_name": "Healthy Player",
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Drop confirmation name does not match roster player")
        self.assertEqual(payload["detail"]["expected_name"], "Drop Candidate")
        self.assertFalse(validate_session.called)
        self.assertTrue(insert_log.called)

    def test_stale_session_returns_502(self):
        response, insert_log, validate_session = self.post_action(
            {"action": "move_to_il", "player_id": "injured-1"},
            session_failure=sandlot_actions.ActionFailure(
                sandlot_actions.SESSION_EXPIRED_MESSAGE,
                status_code=502,
            ),
        )

        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], sandlot_actions.SESSION_EXPIRED_MESSAGE)
        self.assertTrue(validate_session.called)
        self.assertTrue(insert_log.called)

    def test_successful_il_move_returns_200(self):
        response, insert_log, validate_session = self.post_action(
            {"action": "move_to_il", "player_id": "injured-1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["player_name"], "Injured Player")
        self.assertEqual(payload["detail"]["from_slot"], "BN")
        self.assertEqual(payload["detail"]["to_slot"], "IL")
        self.assertTrue(validate_session.called)
        self.assertTrue(insert_log.called)

    def test_successful_add_returns_200(self):
        response, insert_log, validate_session = self.post_action(
            {"action": "add_free_agent", "player_id": "fa-1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "add_free_agent")
        self.assertEqual(payload["player_name"], "Free Agent")
        self.assertTrue(validate_session.called)
        self.assertTrue(insert_log.called)


if __name__ == "__main__":
    unittest.main()
