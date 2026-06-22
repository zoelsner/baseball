"""Read-only Fantrax roster DOM helpers.

These helpers parse saved Fantrax roster HTML. They never drive a browser and
never click controls; the goal is to prove whether the roster page exposes the
real lineup slot text that the JSON API may omit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

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
HEADSHOT_RE = re.compile(r"\bhs([A-Za-z0-9_-]+)_")


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
            }
    return slots


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
    for node in _walk(row):
        if _class_has(node, ("lineup-btn",)):
            return node
        if not _is_buttonish(node):
            continue
        for key in ("data-testid", "aria-label", "title"):
            value = node.attrs.get(key, "")
            if "lineup" in value.lower():
                return node
    return None


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
