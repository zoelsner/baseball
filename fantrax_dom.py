"""Read-only Fantrax roster DOM helpers.

These helpers parse saved Fantrax roster HTML. They never drive a browser and
never click controls; the goal is to prove whether the roster page exposes the
real lineup slot text that the JSON API may omit.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

import auth
import fantrax_data


LINEUP_SLOT_VALUES = {
    "C",
    "1B",
    "2B",
    "3B",
    "SS",
    "OF",
    "UT",
    "SP",
    "RP",
    "P",
    "CI",
    "MI",
    "BN",
    "RES",
    "IR",
    "IL",
    "MIN",
}
PLAYER_ID_ATTRS = {"data-player-id", "data-scorer-id", "data-playerid", "data-scorerid"}
HEADSHOT_RE = re.compile(
    r"\bhs([A-Za-z0-9_-]+?)_(?=(?:\d+|large|small)(?:_\d+)?\.(?:png|jpe?g|webp)\b)",
    re.IGNORECASE,
)


class VisibleRosterIdentityError(ValueError):
    """Visible roster rows cannot be joined to the exact expected roster."""


def roster_url(league_id: str, team_id: str, *, override: str | None = None) -> str:
    if override:
        return override
    return f"https://www.fantrax.com/fantasy/league/{league_id}/team/roster;teamId={team_id}"


def capture_roster_html(
    cookies: list[dict[str, Any]],
    *,
    league_id: str,
    team_id: str,
    headful: bool = False,
    url: str | None = None,
    wait_seconds: float = 20,
    driver_factory: Any | None = None,
) -> str:
    """Read roster page HTML through Selenium without clicking Fantrax controls."""
    if not cookies:
        raise RuntimeError("Fantrax cookies are required to capture roster DOM")
    driver = driver_factory(headful=headful) if driver_factory else auth._build_driver(headful=headful)
    try:
        driver.get(auth.HOME_URL)
        _install_cookies(driver, cookies)
        driver.get(roster_url(league_id, team_id, override=url))
        _wait_for_ready_state(driver, wait_seconds)
        return str(getattr(driver, "page_source", "") or "")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _install_cookies(driver: Any, cookies: list[dict[str, Any]]) -> None:
    for cookie in cookies:
        prepared = {
            key: value
            for key, value in cookie.items()
            if key in {"name", "value", "path", "domain", "expiry", "secure", "httpOnly"}
        }
        if not prepared.get("name") or prepared.get("value") is None:
            continue
        if prepared.get("domain"):
            prepared["domain"] = str(prepared["domain"]).lstrip(".")
        try:
            driver.add_cookie(prepared)
        except Exception:
            continue


def _wait_for_ready_state(driver: Any, wait_seconds: float) -> None:
    deadline = time.time() + max(0, wait_seconds)
    while True:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                return
        except Exception:
            return
        if time.time() >= deadline:
            return
        time.sleep(0.25)


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


class _RosterHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(
            tag=tag.lower(),
            attrs={str(name).lower(): str(value or "") for name, value in attrs},
            parent=self.stack[-1],
        )
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(
            tag=tag.lower(),
            attrs={str(name).lower(): str(value or "") for name, value in attrs},
            parent=self.stack[-1],
        )
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data and self.stack:
            self.stack[-1].text.append(data)


def lineup_slots_from_html(html: str) -> dict[str, dict[str, Any]]:
    """Return `{player_id: slot_info}` from saved Fantrax roster HTML."""
    parser = _RosterHtmlParser()
    parser.feed(html or "")
    slots: dict[str, dict[str, Any]] = {}
    for marker in _walk(parser.root):
        player_ids = _player_ids(marker)
        if not player_ids:
            continue
        row = _nearest_row(marker)
        if row is None:
            continue
        slot_node = _find_lineup_button(row)
        if slot_node is None:
            continue
        raw_text = _text_content(slot_node)
        slot = _slot_from_node(slot_node)
        if not slot:
            continue
        for player_id in player_ids:
            existing = slots.get(player_id)
            if existing and existing.get("slot") != slot:
                existing.setdefault("conflicts", []).append({"slot": slot, "text": raw_text})
                continue
            slots[player_id] = {
                "slot": slot,
                "slot_source": "dom.lineup-btn",
                "text": raw_text,
                "lineup_control_enabled": _control_enabled(slot_node),
            }
    return slots


def visible_roster_rows_from_html(html: str) -> list[dict[str, Any]]:
    """Extract visible roster identity evidence without clicking Fantrax.

    A row may lack a player id when Fantrax has no headshot.  Call
    `reconcile_visible_roster_rows` with the server-derived roster to resolve
    those rows only when a unique name-initial/surname/team match exists.
    """
    parser = _RosterHtmlParser()
    parser.feed(html or "")
    rows: list[dict[str, Any]] = []
    for row in _walk(parser.root):
        if row.tag != "tr" and not _class_has(row, ("i-table__row", "player-row", "roster-row")):
            continue
        name = _visible_player_name(row)
        slot_node = _find_lineup_button(row)
        slot = _slot_from_node(slot_node) if slot_node is not None else None
        if not name or not slot:
            continue
        ids: list[str] = []
        for node in _walk(row):
            ids.extend(_player_ids(node))
        unique_ids = list(dict.fromkeys(ids))
        rows.append({
            "player_id": unique_ids[0] if len(unique_ids) == 1 else None,
            "name": name,
            "team": _visible_player_team(row),
            "slot": slot,
            "lineup_control_enabled": _control_enabled(slot_node),
            "identity_source": "visible_headshot_or_attribute" if len(unique_ids) == 1 else "visible_identity_missing",
            "identity_conflict": unique_ids if len(unique_ids) > 1 else [],
        })
    return rows


def reconcile_visible_roster_rows(
    visible_rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fail closed unless every visible row maps one-to-one to expected ids."""
    expected_by_id = {
        str(row.get("id")): row
        for row in expected_rows
        if isinstance(row, dict) and row.get("id") and row.get("name")
    }
    if not expected_by_id or len(visible_rows) != len(expected_by_id):
        raise VisibleRosterIdentityError(
            f"Visible roster count {len(visible_rows)} does not match expected count {len(expected_by_id)}"
        )

    reconciled: list[dict[str, Any]] = []
    matched: set[str] = set()
    unresolved: list[dict[str, Any]] = []
    for visible in visible_rows:
        if visible.get("identity_conflict"):
            raise VisibleRosterIdentityError(f"Visible row has conflicting player ids: {visible['identity_conflict']}")
        player_id = str(visible.get("player_id") or "")
        if not player_id:
            unresolved.append(visible)
            continue
        expected = expected_by_id.get(player_id)
        if not expected or player_id in matched:
            raise VisibleRosterIdentityError(f"Visible player id {player_id!r} is missing or duplicated")
        if not _visible_identity_matches(visible, expected):
            raise VisibleRosterIdentityError(f"Visible identity does not match expected player id {player_id}")
        matched.add(player_id)
        reconciled.append({**visible, "player_id": player_id, "identity_source": "visible_player_id"})

    for visible in unresolved:
        candidates = [
            player_id
            for player_id, expected in expected_by_id.items()
            if player_id not in matched and _visible_identity_matches(visible, expected)
        ]
        if len(candidates) != 1:
            raise VisibleRosterIdentityError(
                f"Visible row {visible.get('name')!r} / {visible.get('team')!r} has {len(candidates)} safe matches"
            )
        player_id = candidates[0]
        matched.add(player_id)
        reconciled.append({
            **visible,
            "player_id": player_id,
            "identity_source": "visible_initial_surname_team_unique",
        })

    if matched != set(expected_by_id):
        missing = sorted(set(expected_by_id) - matched)
        raise VisibleRosterIdentityError(f"Visible roster is missing expected player ids: {missing}")
    return sorted(reconciled, key=lambda row: str(row["player_id"]))


def _walk(node: _Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _player_ids(node: _Node) -> list[str]:
    ids = []
    for key in PLAYER_ID_ATTRS:
        value = node.attrs.get(key)
        if value:
            ids.append(value)
    for value in node.attrs.values():
        for match in HEADSHOT_RE.finditer(value):
            ids.append(match.group(1))
    out = []
    seen = set()
    for value in ids:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _nearest_row(node: _Node) -> _Node | None:
    current = node
    fallback = None
    while current.parent is not None:
        if current.tag == "tr":
            return current
        if _class_has(current, ("player", "row", "mat-row", "roster-row")):
            fallback = fallback or current
        current = current.parent
    return fallback


def _find_lineup_button(row: _Node) -> _Node | None:
    known: list[_Node] = []
    for node in _walk(row):
        if _class_has(node, ("lineup-btn",)):
            known.append(node)
            continue
        if not _is_buttonish(node):
            continue
        for key in ("data-testid", "aria-label", "title"):
            value = node.attrs.get(key, "")
            if "lineup" in value.lower():
                known.append(node)
                break
    unique_known: list[_Node] = []
    seen_known: set[int] = set()
    for node in known:
        if id(node) in seen_known:
            continue
        seen_known.add(id(node))
        unique_known.append(node)
    known = unique_known
    if len(known) == 1:
        return known[0]
    if len(known) > 1:
        return None

    # Current Fantrax rows put the selected-slot button in the sticky player
    # cell before the <scorer> identity component.  Treat this as evidence only
    # when exactly one slot-like button exists in that bounded prefix.  A
    # position/action chip plus an unmarked slot control is ambiguous and fails.
    structural: list[_Node] = []
    for cell in _walk(row):
        if str(cell.attrs.get("itablecell") or "").casefold() != "player":
            continue
        for node in _walk(cell):
            if node.tag == "scorer":
                break
            if _is_buttonish(node) and _slot_from_node(node) in LINEUP_SLOT_VALUES:
                structural.append(node)
    return structural[0] if len(structural) == 1 else None


def _visible_player_name(row: _Node) -> str | None:
    for node in _walk(row):
        if _class_has(node, ("scorer__info__name",)):
            name = _text_content(node).strip()
            return name or None
    return None


def _visible_player_team(row: _Node) -> str | None:
    for node in _walk(row):
        if not _class_has(node, ("scorer__info__positions",)):
            continue
        match = re.search(r"-\s*([A-Za-z]{2,4})\b", _text_content(node))
        if match:
            return match.group(1).upper()
    return None


def _visible_identity_matches(visible: dict[str, Any], expected: dict[str, Any]) -> bool:
    visible_team = str(visible.get("team") or "").strip().upper()
    expected_team = str(expected.get("team") or "").strip().upper()
    if not visible_team or visible_team != expected_team:
        return False
    return _abbreviated_name_key(visible.get("name")) == _abbreviated_name_key(expected.get("name"))


def _abbreviated_name_key(value: Any) -> tuple[str, str] | None:
    ascii_name = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[A-Za-z0-9]+", ascii_name.casefold())
    if len(tokens) < 2:
        return None
    suffixes = {"jr", "sr", "ii", "iii", "iv"}
    surname_index = -2 if tokens[-1] in suffixes and len(tokens) >= 3 else -1
    return tokens[0][0], tokens[surname_index]


def _slot_from_node(node: _Node) -> str | None:
    candidates = [
        _text_content(node),
        node.attrs.get("data-slot", ""),
        node.attrs.get("aria-label", ""),
        node.attrs.get("title", ""),
    ]
    for candidate in candidates:
        slot = _slot_from_text(candidate)
        if slot:
            return slot
    return None


def _slot_from_text(value: str) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    candidates = [text]
    candidates.extend(re.split(r"[^A-Za-z0-9/]+", text))
    for candidate in candidates:
        normalized = fantrax_data._normalize_slot_label(candidate)
        if normalized in LINEUP_SLOT_VALUES:
            return normalized
    return None


def _text_content(node: _Node) -> str:
    parts: list[str] = []

    def collect(item: _Node) -> None:
        parts.extend(item.text)
        for child in item.children:
            collect(child)

    collect(node)
    return " ".join(" ".join(parts).split())


def _class_has(node: _Node, needles: tuple[str, ...]) -> bool:
    class_text = node.attrs.get("class", "").lower()
    tokens = {token for token in re.split(r"\s+", class_text) if token}
    for needle in needles:
        if needle in tokens or needle in class_text:
            return True
    return False


def _is_buttonish(node: _Node) -> bool:
    return node.tag in {"button", "a"} or node.attrs.get("role", "").lower() == "button"


def _control_enabled(node: _Node) -> bool:
    current: _Node | None = node
    while current is not None:
        if "disabled" in current.attrs:
            return False
        for attribute in ("aria-disabled", "data-disabled", "ng-reflect-disabled", "data-locked"):
            if current.attrs.get(attribute, "").strip().casefold() in {"true", "1", "yes"}:
                return False
        classes = {token.casefold() for token in current.attrs.get("class", "").split()}
        if any(marker in token for token in classes for marker in ("disabled", "unavailable", "locked")):
            return False
        current = current.parent
    return True
