"""Machine-to-machine Fantrax roster actions for Sandlot.

This module is deliberately not a user-facing product surface. It gives Zo
Computer a token-gated executor layer for applying already-decided moves in
Fantrax, with deterministic guardrails before any Selenium write is attempted.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import requests
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import auth
import fantrax_data
import sandlot_db

log = logging.getLogger(__name__)

ACTION_LOCK_ID = 2026060802
SESSION_EXPIRED_MESSAGE = "Session expired — run a refresh to re-authenticate."
SUPPORTED_ACTIONS = {"move_to_il", "add_free_agent", "drop_player", "change_slot"}
IL_STATUSES = {
    "IL",
    "IR",
    "INJ",
    "INJURED",
    "DTD",
    "DAY-TO-DAY",
    "DAY TO DAY",
    "OUT",
    "10-DAY IL",
    "15-DAY IL",
    "60-DAY IL",
}


@dataclass
class SessionContext:
    session: requests.Session
    cookies: list[dict[str, Any]]
    source: str


@dataclass
class ActionResult:
    ok: bool
    action: str
    player_name: str | None = None
    detail: dict[str, Any] | None = None
    error: str | None = None
    selenium_state: dict[str, Any] = field(default_factory=dict)

    def api_detail(self) -> dict[str, Any] | None:
        return self.detail if self.detail else None


class ActionFailure(Exception):
    def __init__(
        self,
        error: str,
        *,
        detail: dict[str, Any] | None = None,
        status_code: int = 400,
        player_name: str | None = None,
        selenium_state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(error)
        self.error = error
        self.detail = detail or {}
        self.status_code = status_code
        self.player_name = player_name
        self.selenium_state = selenium_state or {}


def move_to_il(player_fantrax_id: str, *, snapshot_row: dict[str, Any] | None = None) -> dict[str, Any]:
    result = execute_action("move_to_il", player_fantrax_id, snapshot_row=snapshot_row)
    return _result_dict(result)


def add_free_agent(
    player_fantrax_id: str,
    *,
    move_out_player_id: str | None = None,
    snapshot_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = execute_action(
        "add_free_agent",
        player_fantrax_id,
        move_out_player_id=move_out_player_id,
        snapshot_row=snapshot_row,
    )
    return _result_dict(result)


def drop_player(
    player_fantrax_id: str,
    *,
    confirm_player_name: str | None = None,
    snapshot_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = execute_action(
        "drop_player",
        player_fantrax_id,
        confirm_player_name=confirm_player_name,
        snapshot_row=snapshot_row,
    )
    return _result_dict(result)


def change_slot(
    player_fantrax_id: str,
    to_slot: str,
    *,
    snapshot_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = execute_action(
        "change_slot",
        player_fantrax_id,
        to_slot=to_slot,
        snapshot_row=snapshot_row,
    )
    return _result_dict(result)


def execute_action(
    action: str,
    player_id: str,
    *,
    to_slot: str | None = None,
    confirm_player_name: str | None = None,
    move_out_player_id: str | None = None,
    snapshot_row: dict[str, Any] | None = None,
    executor: Any | None = None,
) -> ActionResult:
    """Validate safety constraints, verify the Fantrax session, then execute once.

    There is intentionally no retry wrapper here. If Selenium reaches an
    ambiguous state, the caller gets the error and decides what to do next.
    """
    if action not in SUPPORTED_ACTIONS:
        raise ActionFailure("Invalid action type", detail={"action": action})

    snapshot_row = snapshot_row or _latest_snapshot_row()
    snapshot = snapshot_row.get("data") or {}
    if not isinstance(snapshot, dict) or not snapshot:
        raise ActionFailure("No successful Fantrax snapshot is available", status_code=409)

    player_context = _validate_action_context(
        action,
        str(player_id),
        snapshot,
        to_slot=to_slot,
        confirm_player_name=confirm_player_name,
        move_out_player_id=move_out_player_id,
    )
    session_context = validate_session_fresh()
    executor = executor or FantraxActionExecutor(cookies=session_context.cookies)

    try:
        if action == "move_to_il":
            return executor.move_to_il(str(player_id), player_row=player_context["player"])
        if action == "add_free_agent":
            return executor.add_free_agent(
                str(player_id),
                player_row=player_context["player"],
                move_out_player_id=move_out_player_id,
                move_out_player_row=player_context.get("move_out_player"),
            )
        if action == "drop_player":
            return executor.drop_player(str(player_id), player_row=player_context["player"])
        if action == "change_slot":
            return executor.change_slot(str(player_id), str(to_slot), player_row=player_context["player"])
    except ActionFailure:
        raise
    except Exception as exc:
        state = executor.debug_state() if hasattr(executor, "debug_state") else {}
        raise ActionFailure(
            "selenium_action_failed",
            detail={"message": str(exc)},
            status_code=502,
            player_name=(player_context.get("player") or {}).get("name"),
            selenium_state=state,
        ) from exc

    raise ActionFailure("Invalid action type", detail={"action": action})


def _validate_action_context(
    action: str,
    player_id: str,
    snapshot: dict[str, Any],
    *,
    to_slot: str | None,
    confirm_player_name: str | None,
    move_out_player_id: str | None,
) -> dict[str, Any]:
    if action == "move_to_il":
        player = find_roster_player(snapshot, player_id)
        if not player:
            raise ActionFailure("Player is not on the current roster", detail={"player_id": player_id})
        injury = injury_status(player)
        if not injury:
            raise ActionFailure(
                "Player is not IL-eligible in the latest snapshot",
                detail={"player_id": player_id, "player_name": player.get("name"), "injury": None},
                player_name=player.get("name"),
            )
        return {"player": player, "injury": injury}

    if action == "add_free_agent":
        player = find_free_agent(snapshot, player_id) or {"id": player_id, "name": None}
        move_out = ensure_roster_room(snapshot, move_out_player_id=move_out_player_id)
        return {"player": player, "move_out_player": move_out}

    if action == "drop_player":
        player = find_roster_player(snapshot, player_id)
        if not player:
            raise ActionFailure("Player is not on the current roster", detail={"player_id": player_id})
        expected_name = player.get("name")
        if not confirm_player_name or confirm_player_name != expected_name:
            raise ActionFailure(
                "Drop confirmation name does not match roster player",
                detail={
                    "player_id": player_id,
                    "expected_name": expected_name,
                    "provided_name": confirm_player_name,
                },
                player_name=expected_name,
            )
        return {"player": player}

    if action == "change_slot":
        if not to_slot:
            raise ActionFailure("to_slot is required for change_slot", detail={"player_id": player_id})
        player = find_roster_player(snapshot, player_id)
        if not player:
            raise ActionFailure("Player is not on the current roster", detail={"player_id": player_id})
        return {"player": player}

    raise ActionFailure("Invalid action type", detail={"action": action})


def find_roster_player(snapshot: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    target = str(player_id)
    for row in _my_roster_rows(snapshot):
        if isinstance(row, dict) and str(row.get("id") or "") == target:
            return row
    return None


def find_free_agent(snapshot: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    target = str(player_id)
    for row in (snapshot.get("free_agents") or {}).get("players") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == target:
            return row
    return None


def injury_status(player: dict[str, Any]) -> str | None:
    for value in _candidate_status_values(player):
        normalized = _normalize_status(value)
        if normalized in IL_STATUSES:
            return normalized
    return None


def ensure_roster_room(snapshot: dict[str, Any], *, move_out_player_id: str | None) -> dict[str, Any] | None:
    if move_out_player_id:
        move_out = find_roster_player(snapshot, move_out_player_id)
        if not move_out:
            raise ActionFailure(
                "move_out_player_id is not on the current roster",
                detail={"move_out_player_id": move_out_player_id},
            )
        return move_out

    roster = snapshot.get("roster") or {}
    active = _to_int(roster.get("active"))
    active_max = _to_int(roster.get("active_max"))
    reserve = _to_int(roster.get("reserve"))
    reserve_max = _to_int(roster.get("reserve_max"))
    if None in (active, active_max, reserve, reserve_max):
        raise ActionFailure(
            "Roster capacity is unavailable; provide move_out_player_id for add+drop",
            detail={
                "active": roster.get("active"),
                "active_max": roster.get("active_max"),
                "reserve": roster.get("reserve"),
                "reserve_max": roster.get("reserve_max"),
            },
        )
    if int(active) + int(reserve) >= int(active_max) + int(reserve_max):
        raise ActionFailure(
            "Roster is full; provide move_out_player_id for add+drop",
            detail={
                "active": active,
                "active_max": active_max,
                "reserve": reserve,
                "reserve_max": reserve_max,
            },
        )
    return None


def validate_session_fresh(
    *,
    cookies: list[dict[str, Any]] | None = None,
    source: str | None = None,
    session: requests.Session | None = None,
) -> SessionContext:
    loaded_cookies = cookies
    cookie_source = source or "provided"
    if loaded_cookies is None:
        loaded_cookies, cookie_source = _load_cached_cookies()
    if not loaded_cookies:
        raise ActionFailure(SESSION_EXPIRED_MESSAGE, status_code=502)
    if _cookies_expired(loaded_cookies):
        raise ActionFailure(SESSION_EXPIRED_MESSAGE, status_code=502)

    session = session or auth._build_session(loaded_cookies)
    league_id = os.environ.get("FANTRAX_LEAGUE_ID")
    try:
        if league_id:
            response = session.post(
                f"{fantrax_data.FXPA_URL}?leagueId={league_id}",
                json={"msgs": [{"method": "getFantasyTeams", "data": {"leagueId": league_id}}]},
                timeout=15,
            )
        else:
            response = session.get(auth.HOME_URL, timeout=15)
    except Exception as exc:
        raise ActionFailure(
            SESSION_EXPIRED_MESSAGE,
            detail={"message": str(exc), "source": cookie_source},
            status_code=502,
        ) from exc

    body = ""
    try:
        body = response.text[:1000].lower()
    except Exception:
        body = ""
    if response.status_code in (401, 403) or "login" in response.url.lower() or "unauthorized" in body:
        raise ActionFailure(
            SESSION_EXPIRED_MESSAGE,
            detail={"status_code": response.status_code, "source": cookie_source},
            status_code=502,
        )
    return SessionContext(session=session, cookies=loaded_cookies, source=cookie_source)


class FantraxActionExecutor:
    def __init__(
        self,
        *,
        cookies: list[dict[str, Any]],
        league_id: str | None = None,
        team_id: str | None = None,
        headful: bool | None = None,
    ) -> None:
        self.cookies = cookies
        self.league_id = league_id or os.environ.get("FANTRAX_LEAGUE_ID")
        self.team_id = team_id or os.environ.get("FANTRAX_TEAM_ID")
        self.headful = os.environ.get("SANDLOT_ACTIONS_HEADFUL") == "1" if headful is None else headful
        self._driver: WebDriver | None = None

    def move_to_il(self, player_id: str, *, player_row: dict[str, Any] | None = None) -> ActionResult:
        with self._browser() as driver:
            self._go(driver, self._roster_url())
            row = self._find_player_row(driver, player_id)
            player_name = (player_row or {}).get("name") or self._row_name(row)
            from_slot = (player_row or {}).get("slot") or self._row_slot(row)
            self._click_row_action(row, ("move", "slot", "lineup", "edit"))
            self._click_button_or_text(driver, ("IL", "IR", "Injured Reserve"))
            self._click_optional_confirmation(driver)
            return ActionResult(
                ok=True,
                action="move_to_il",
                player_name=player_name,
                detail={"from_slot": from_slot, "to_slot": "IL"},
                selenium_state=self._state(driver),
            )

    def add_free_agent(
        self,
        player_id: str,
        *,
        player_row: dict[str, Any] | None = None,
        move_out_player_id: str | None = None,
        move_out_player_row: dict[str, Any] | None = None,
    ) -> ActionResult:
        with self._browser() as driver:
            self._go(driver, self._free_agents_url())
            row = self._find_player_row(driver, player_id)
            player_name = (player_row or {}).get("name") or self._row_name(row)
            self._click_row_action(row, ("add", "claim", "+"))
            detail: dict[str, Any] = {}
            if move_out_player_id:
                self._select_move_out_player(driver, move_out_player_id)
                detail["move_out_player_id"] = move_out_player_id
                detail["move_out_player_name"] = (move_out_player_row or {}).get("name")
            self._click_button_or_text(driver, ("Confirm", "Submit", "Add", "Claim"))
            return ActionResult(
                ok=True,
                action="add_free_agent",
                player_name=player_name,
                detail=detail or None,
                selenium_state=self._state(driver),
            )

    def drop_player(self, player_id: str, *, player_row: dict[str, Any] | None = None) -> ActionResult:
        with self._browser() as driver:
            self._go(driver, self._roster_url())
            row = self._find_player_row(driver, player_id)
            player_name = (player_row or {}).get("name") or self._row_name(row)
            self._click_row_action(row, ("drop", "release", "remove"))
            self._click_optional_confirmation(driver)
            return ActionResult(
                ok=True,
                action="drop_player",
                player_name=player_name,
                selenium_state=self._state(driver),
            )

    def change_slot(
        self,
        player_id: str,
        to_slot: str,
        *,
        player_row: dict[str, Any] | None = None,
    ) -> ActionResult:
        with self._browser() as driver:
            self._go(driver, self._roster_url())
            row = self._find_player_row(driver, player_id)
            player_name = (player_row or {}).get("name") or self._row_name(row)
            from_slot = (player_row or {}).get("slot") or self._row_slot(row)
            self._click_row_action(row, ("move", "slot", "lineup", "edit"))
            self._click_button_or_text(driver, (to_slot,))
            self._click_optional_confirmation(driver)
            return ActionResult(
                ok=True,
                action="change_slot",
                player_name=player_name,
                detail={"from_slot": from_slot, "to_slot": to_slot},
                selenium_state=self._state(driver),
            )

    def debug_state(self) -> dict[str, Any]:
        if not self._driver:
            return {}
        return self._state(self._driver)

    @contextmanager
    def _browser(self):
        driver = auth._build_driver(headful=self.headful)
        self._driver = driver
        try:
            driver.get(auth.HOME_URL)
            self._install_cookies(driver)
            yield driver
        finally:
            try:
                driver.quit()
            finally:
                self._driver = None

    def _install_cookies(self, driver: WebDriver) -> None:
        for cookie in self.cookies:
            prepared = {k: v for k, v in cookie.items() if k in {"name", "value", "path", "domain", "expiry", "secure", "httpOnly"}}
            if not prepared.get("name") or prepared.get("value") is None:
                continue
            if prepared.get("domain"):
                prepared["domain"] = str(prepared["domain"]).lstrip(".")
            try:
                driver.add_cookie(prepared)
            except Exception as exc:
                log.debug("Skipping Fantrax cookie %s during Selenium install: %s", prepared.get("name"), exc)

    def _go(self, driver: WebDriver, url: str) -> None:
        driver.get(url)
        WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")

    def _roster_url(self) -> str:
        override = os.environ.get("SANDLOT_FANTRAX_ROSTER_URL")
        if override:
            return override
        return f"https://www.fantrax.com/fantasy/league/{self.league_id}/team/roster;teamId={self.team_id}"

    def _free_agents_url(self) -> str:
        override = os.environ.get("SANDLOT_FANTRAX_FREE_AGENTS_URL")
        if override:
            return override
        return f"https://www.fantrax.com/fantasy/league/{self.league_id}/players;statusOrTeamFilter=ALL_AVAILABLE"

    def _find_player_row(self, driver: WebDriver, player_id: str) -> WebElement:
        target = _xpath_literal(str(player_id))
        xpaths = [
            f"//*[@data-player-id={target} or @data-scorer-id={target} or @data-playerid={target} or @data-scorerid={target}]",
            f"//*[contains(@href, {target})]/ancestor::tr[1]",
            f"//*[contains(@href, {target})]/ancestor::*[contains(concat(' ', @class, ' '), ' player ') or contains(concat(' ', @class, ' '), ' row ')][1]",
            f"//*[contains(@data-testid, {target})]/ancestor::*[self::tr or contains(concat(' ', @class, ' '), ' row ')][1]",
        ]
        last_error: Exception | None = None
        for xpath in xpaths:
            try:
                element = WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.XPATH, xpath)))
                row = self._nearest_row(element)
                return row or element
            except Exception as exc:
                last_error = exc
        raise ActionFailure(
            "player_row_not_found",
            detail={"player_id": player_id, "message": str(last_error) if last_error else None},
            status_code=502,
            selenium_state=self._state(driver),
        )

    def _nearest_row(self, element: WebElement) -> WebElement | None:
        try:
            return element.find_element(By.XPATH, "./ancestor::tr[1]")
        except NoSuchElementException:
            pass
        try:
            return element.find_element(By.XPATH, "./ancestor::*[contains(concat(' ', @class, ' '), ' row ')][1]")
        except NoSuchElementException:
            return None

    def _click_row_action(self, row: WebElement, labels: tuple[str, ...]) -> None:
        element = self._find_clickable_by_text(row, labels)
        if element is None:
            raise ActionFailure(
                "action_control_not_found",
                detail={"labels": list(labels), "row_text": row.text[:500]},
                status_code=502,
                selenium_state=self.debug_state(),
            )
        self._safe_click(element)

    def _click_button_or_text(self, driver: WebDriver, labels: tuple[str, ...]) -> None:
        element = self._find_clickable_by_text(driver, labels)
        if element is None:
            raise ActionFailure(
                "action_control_not_found",
                detail={"labels": list(labels)},
                status_code=502,
                selenium_state=self._state(driver),
            )
        self._safe_click(element)

    def _click_optional_confirmation(self, driver: WebDriver) -> None:
        element = self._find_clickable_by_text(driver, ("Confirm", "Submit", "Save", "OK", "Yes", "Drop", "Move"))
        if element is not None:
            self._safe_click(element)

    def _select_move_out_player(self, driver: WebDriver, player_id: str) -> None:
        try:
            row = self._find_player_row(driver, player_id)
            control = self._find_clickable_by_text(row, ("Drop", "Move out", "Select", "Remove"))
            if control:
                self._safe_click(control)
                return
        except ActionFailure:
            pass
        player_literal = _xpath_literal(str(player_id))
        selectors = [
            f"//option[contains(@value, {player_literal})]",
            f"//*[@data-player-id={player_literal} or @data-scorer-id={player_literal}]",
        ]
        for xpath in selectors:
            try:
                element = driver.find_element(By.XPATH, xpath)
                self._safe_click(element)
                return
            except NoSuchElementException:
                continue
        raise ActionFailure(
            "move_out_control_not_found",
            detail={"move_out_player_id": player_id},
            status_code=502,
            selenium_state=self._state(driver),
        )

    def _find_clickable_by_text(self, scope: WebDriver | WebElement, labels: tuple[str, ...]) -> WebElement | None:
        for label in labels:
            needle = label.strip().lower()
            if not needle:
                continue
            xpath = (
                ".//*[self::button or self::a or @role='button' or self::span or self::div]"
                f"[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), {_xpath_literal(needle)})]"
            )
            try:
                candidates = scope.find_elements(By.XPATH, xpath)
            except Exception:
                candidates = []
            for candidate in candidates:
                try:
                    if candidate.is_displayed() and candidate.is_enabled():
                        return candidate
                except Exception:
                    continue
        return None

    def _safe_click(self, element: WebElement) -> None:
        try:
            element.click()
            time.sleep(0.5)
        except WebDriverException as exc:
            raise ActionFailure(
                "selenium_click_failed",
                detail={"message": str(exc)},
                status_code=502,
                selenium_state=self.debug_state(),
            ) from exc

    def _row_name(self, row: WebElement) -> str | None:
        for attr in ("data-player-name", "data-name"):
            value = row.get_attribute(attr)
            if value:
                return value
        text = (row.text or "").strip()
        return text.splitlines()[0].strip() if text else None

    def _row_slot(self, row: WebElement) -> str | None:
        for attr in ("data-slot", "data-position"):
            value = row.get_attribute(attr)
            if value:
                return value
        return None

    def _state(self, driver: WebDriver) -> dict[str, Any]:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text[:1500]
        except Exception:
            body = None
        state: dict[str, Any] = {"url": None, "title": None, "body_excerpt": body}
        try:
            state["url"] = driver.current_url
            state["title"] = driver.title
        except Exception:
            pass
        return state


def _latest_snapshot_row() -> dict[str, Any]:
    row = sandlot_db.latest_successful_snapshot()
    if not row:
        raise ActionFailure("No successful Fantrax snapshot is available", status_code=409)
    return row


def _my_roster_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (snapshot.get("roster") or {}).get("rows") or []
    if rows:
        return list(rows)
    my_team_id = snapshot.get("team_id")
    for tid, team in (snapshot.get("all_team_rosters") or {}).items():
        if not isinstance(team, dict):
            continue
        team_id = team.get("team_id") or tid
        if bool(team.get("is_me")) or (my_team_id is not None and str(team_id) == str(my_team_id)):
            return list(team.get("rows") or [])
    return []


def _candidate_status_values(player: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key in ("injury", "status", "player_status", "status_short", "health_status"):
        values.append(player.get(key))
    for block in (player.get("raw"), (player.get("raw") or {}).get("player")):
        if not isinstance(block, dict):
            continue
        for key in ("injury", "status", "player_status", "status_short", "health_status"):
            values.append(block.get(key))
        if block.get("out"):
            values.append("OUT")
        if block.get("injured_reserve"):
            values.append("IR")
        if block.get("day_to_day"):
            values.append("DTD")
    return values


def _normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("shortName", "short_name", "name", "code", "description"):
            normalized = _normalize_status(value.get(key))
            if normalized:
                return normalized
        return None
    text = str(value).strip().upper()
    return text or None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_cached_cookies() -> tuple[list[dict[str, Any]] | None, str]:
    try:
        db_cookies = sandlot_db.get_fantrax_cookies()
        if db_cookies:
            return db_cookies, "postgres"
    except Exception as exc:
        log.warning("Could not load Fantrax cookies from Postgres: %s", exc)

    raw = os.environ.get("FANTRAX_COOKIES_JSON")
    if raw:
        cookies = json.loads(raw)
        if not isinstance(cookies, list):
            raise ActionFailure(
                SESSION_EXPIRED_MESSAGE,
                detail={"message": "FANTRAX_COOKIES_JSON must be a JSON array"},
                status_code=502,
            )
        return cookies, "env"

    if auth.COOKIE_PATH.exists():
        cookies = json.loads(auth.COOKIE_PATH.read_text())
        if isinstance(cookies, list):
            return cookies, "local-file"
    return None, "missing"


def _cookies_expired(cookies: list[dict[str, Any]]) -> bool:
    now = time.time()
    expiries: list[float] = []
    for cookie in cookies:
        raw = cookie.get("expiry") or cookie.get("expires")
        if raw is None:
            continue
        try:
            expiries.append(float(raw))
        except (TypeError, ValueError):
            continue
        name = str(cookie.get("name") or "").upper()
        if name in {"JSESSIONID", "FX_SESSION"} and expiries[-1] <= now:
            return True
    return bool(expiries) and all(expiry <= now for expiry in expiries)


def _result_dict(result: ActionResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "player_name": result.player_name,
        "detail": result.detail,
        "error": result.error,
    }


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"
