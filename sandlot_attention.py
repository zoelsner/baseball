"""Deterministic Attention Queue shared with machine consumers.

Python port of the Today page's queue (web/sandlot/v2-pages.jsx:
v2NormalizeRosterRow + v2RosterHealth + v2AttentionQueue) so
GET /api/attention and the UI derive the same read from one snapshot.
The frontend keeps its own copy for now (converging it onto the endpoint
is a follow-up); tests/test_sandlot_attention.py pins this port to the
same fixtures as tests/playwright/specs/today-attention.spec.ts so the
two implementations can't silently drift.

Items that map to an executable move carry ready-to-submit payloads for
POST /api/actions: `action` is a single request body when one call covers
the move, and `actions` is the ordered list (multi-step lineup chains need
one call per step).
"""

from __future__ import annotations

import math
from typing import Any

import sandlot_data_quality
import sandlot_matchup

MAX_ITEMS = 6
MAX_CHIPS = 3

# Mirrors STATUS_LABEL in web/sandlot/atoms.jsx.
STATUS_LABEL = {"ok": "Active", "dtd": "Day-to-day", "il10": "IL-10", "il60": "IL-60"}
# Mirrors v2PlayerState's injured statuses in web/sandlot/v2-pages.jsx.
INJURED_STATUSES = {"il10", "il60", "ir", "out", "dtd", "susp"}
RESERVED_SLOTS = {"BN", "BE", "BENCH", "IL", "IR", "RES", "RESERVE", "MIN", "MINORS"}
# Lowercased mirror of sandlot_actions.IL_STATUSES: a move_to_il payload is
# only attached when the executor's own guard would accept it (a SUSP player
# still surfaces as a status item, but suspension is not IL-eligible).
IL_ELIGIBLE_STATUSES = {
    "il",
    "ir",
    "inj",
    "injured",
    "dtd",
    "day-to-day",
    "day to day",
    "out",
    "10-day il",
    "15-day il",
    "60-day il",
}

_KIND_META = {
    "status": {"priority": 300, "severity": "urgent", "label": "Status"},
    "lineup": {"priority": 200, "severity": "check", "label": "Role"},
    "output": {"priority": 100, "severity": "review", "label": "Output"},
}

_UNSET = object()


def _number(value: Any) -> float:
    """Mirror of v2Number: tolerate strings with thousands separators."""
    if value is None or value == "":
        return 0.0
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


def _normalize_row(raw: dict[str, Any], idx: int) -> dict[str, Any]:
    """Mirror of v2NormalizeRosterRow, limited to the fields the queue reads.

    `vs_exp`, `trend`, `alert`, `opp`, and `mlb_starting` are carried with the
    same defaults the frontend hardcodes — the snapshot doesn't supply them
    yet, so the branches that read them are inert in both implementations,
    but porting them keeps the two in lockstep if the data ever appears.
    """
    all_positions = raw.get("all_positions")
    if isinstance(all_positions, list) and all_positions:
        positions = "/".join(str(p) for p in all_positions if p)
    else:
        positions = raw.get("positions") or raw.get("pos") or "UT"
    fppg = _number(raw.get("fppg"))
    return {
        "id": raw.get("id") or f"{raw.get('name') or 'player'}-{idx}",
        "name": raw.get("name") or "Unknown player",
        "pos": positions,
        "team": raw.get("team") or "",
        "slot": raw.get("slot") or raw.get("slot_full") or "BN",
        "fppg": fppg,
        "fpts": _number(raw.get("fpts")),
        "proj": fppg or 0.0,
        "status": str(raw.get("injury") or raw.get("status") or "").lower(),
        "injury": raw.get("injury") or None,
        "vs_exp": 0.0,
        "trend": "steady",
        "alert": None,
        "opp": "",
        "mlb_starting": None,
    }


def _player_metric(p: dict[str, Any]) -> float:
    return p.get("proj") or p.get("fppg") or p.get("fpts") or 0.0


def _player_state(p: dict[str, Any]) -> str:
    slot = str(p.get("slot") or "").upper()
    if p.get("status") in INJURED_STATUSES:
        return "injured"
    if slot in ("IL", "IR"):
        return "injured"
    if slot in ("BN", "BE", "BENCH", "RES", "RESERVE", "MIN", "MINORS"):
        return "bench"
    return "ok"


def _status_text(p: dict[str, Any]) -> str:
    raw = str(p.get("injury") or p.get("status") or "").strip()
    key = raw.lower()
    if not raw or key in ("ok", "active"):
        return "Active"
    return STATUS_LABEL.get(key, raw)


def _player_context(p: dict[str, Any]) -> str:
    parts = [p.get("slot"), p.get("pos"), p.get("team")]
    return " · ".join(str(part) for part in parts if part) or "Roster"


def _metric_chip(p: dict[str, Any]) -> str | None:
    metric = _player_metric(p)
    return f"{metric:.1f} FP/G" if metric else None


def _vs_exp_chip(p: dict[str, Any]) -> str | None:
    vs_exp = _number(p.get("vs_exp"))
    if not vs_exp:
        return None
    return f"{'+' if vs_exp > 0 else ''}{vs_exp:.1f} vs exp"


def _starter_rows(roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in roster if str(p.get("slot") or "").upper() not in RESERVED_SLOTS]


def _low_output_cutoff(starters: list[dict[str, Any]]) -> float:
    values = sorted(m for m in (_player_metric(p) for p in starters) if m > 0)
    if not values:
        return 0.0
    median = values[len(values) // 2]
    return max(1.0, median * 0.55)


def roster_health(roster: list[dict[str, Any]]) -> dict[str, Any]:
    """Mirror of v2RosterHealth: bucket starters into injury/lineup/cold rows."""
    starters = _starter_rows(roster)
    cutoff = _low_output_cutoff(starters)
    seen: set[Any] = set()

    def add_row(bucket: list[dict[str, Any]], p: dict[str, Any], reason: str, chips: list[str | None]) -> None:
        if p.get("id") in seen:
            return
        seen.add(p.get("id"))
        bucket.append({"player": p, "reason": reason, "chips": [c for c in chips if c]})

    injury_rows: list[dict[str, Any]] = []
    cold_rows: list[dict[str, Any]] = []
    lineup_rows: list[dict[str, Any]] = []

    for p in starters:
        state = _player_state(p)
        metric = _player_metric(p)
        raw_status = p.get("status") or ""
        alert = p.get("alert") if isinstance(p.get("alert"), dict) else None
        is_cold = (
            p.get("trend") == "cold"
            or _number(p.get("vs_exp")) <= -1.5
            or (cutoff > 0 and 0 < metric <= cutoff)
        )
        lineup_flag = (
            (alert or {}).get("kind") in ("not-pitching", "opp-pitcher-tough")
            or p.get("mlb_starting") is False
            or str(p.get("opp") or "").upper() == "OFF"
            or metric == 0
        )

        if state == "injured" or raw_status in ("dtd", "out", "susp"):
            add_row(injury_rows, p, _status_text(p), [_status_text(p), _vs_exp_chip(p)])
            continue
        if lineup_flag:
            if alert and alert.get("msg"):
                reason = str(alert["msg"])
            elif str(p.get("opp") or "").upper() == "OFF":
                reason = "Off today"
            elif metric == 0:
                reason = "No projected output"
            else:
                reason = "Lineup check"
            add_row(lineup_rows, p, reason, [
                str(alert["kind"]).replace("-", " ") if alert and alert.get("kind") else None,
                f"{metric:.1f} FP/G" if metric else None,
            ])
            continue
        if is_cold:
            reason = "Cold streak" if p.get("trend") == "cold" else "Low FP/G for active slot"
            add_row(cold_rows, p, reason, [
                f"{metric:.1f} FP/G" if metric else None,
                _vs_exp_chip(p),
            ])

    return {
        "starters": starters,
        "injury_rows": injury_rows,
        "cold_rows": cold_rows,
        "lineup_rows": lineup_rows,
    }


def _attention_reason(kind: str, row: dict[str, Any]) -> str:
    p = row["player"]
    if kind == "status":
        return f"{row['reason']} on {p.get('slot') or 'active roster'}. Inspect replacement risk before lock."
    if kind == "lineup":
        return f"{row['reason']}. Confirm the active slot before leaving this player in."
    return f"{row['reason']}. Check whether this active spot needs a replacement."


def _move_chain_text(chain: list[dict[str, Any]]) -> str:
    if not chain:
        return "No move detail"
    return "; ".join(
        f"{step.get('player_name') or step.get('player_id') or 'Player'} "
        f"{step.get('from_slot') or '?'} -> {step.get('to_slot') or '?'}"
        for step in chain
    )


def _status_action_payloads(p: dict[str, Any]) -> list[dict[str, Any]]:
    if p.get("status") not in IL_ELIGIBLE_STATUSES:
        return []
    return [{"action": "move_to_il", "player_id": str(p["id"])}]


def _chain_action_payloads(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One change_slot call per chain step — all steps or nothing, so a
    consumer can never execute half a swap."""
    payloads = []
    for step in chain:
        if not step.get("player_id") or not step.get("to_slot"):
            return []
        payloads.append({
            "action": "change_slot",
            "player_id": str(step["player_id"]),
            "to_slot": str(step["to_slot"]),
        })
    return payloads


def build_queue(health: dict[str, Any], recommendations: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Mirror of v2AttentionQueue, plus executable /api/actions payloads."""
    items: list[dict[str, Any]] = []

    def add_player_item(kind: str, row: dict[str, Any], index: int) -> None:
        p = row["player"]
        metric = _player_metric(p)
        meta = _KIND_META[kind]
        status_text = _status_text(p)
        chips: list[str] = []
        for chip in [status_text if status_text != "Active" else None, *row.get("chips", []), _metric_chip(p)]:
            if chip and chip not in chips:
                chips.append(chip)
        actions = _status_action_payloads(p) if kind == "status" else []
        items.append({
            "id": f"{kind}-{p.get('id') or p.get('name') or index}",
            "kind": kind,
            "priority": meta["priority"] + metric,
            "severity": meta["severity"],
            "label": meta["label"],
            "player_id": p.get("id"),
            "title": p.get("name"),
            "context": _player_context(p),
            "reason": _attention_reason(kind, row),
            "chips": chips[:MAX_CHIPS],
            "action": actions[0] if len(actions) == 1 else None,
            "actions": actions,
        })

    for index, row in enumerate(health["injury_rows"]):
        add_player_item("status", row, index)
    for index, row in enumerate(health["lineup_rows"]):
        add_player_item("lineup", row, index)
    for index, row in enumerate(health["cold_rows"]):
        add_player_item("output", row, index)

    top_list = (recommendations or {}).get("recommendations") or []
    top = top_list[0] if top_list else None
    if top:
        points = _number(top.get("points_delta"))
        confidence = top.get("confidence") or "medium"
        chain = (top.get("action") or {}).get("chain") or []
        chain_text = _move_chain_text(chain)
        actions = _chain_action_payloads(chain)
        items.append({
            "id": f"replacement-{top.get('id') or chain_text}",
            "kind": "replacement",
            "priority": 50 + max(0.0, points),
            "severity": "review",
            "label": "Replacement",
            "player_id": None,
            "title": "Review lineup move",
            "context": "Roster decision",
            "reason": f"{chain_text}. Projected gain {'+' if points >= 0 else ''}{points:.1f} points.",
            "chips": [f"{confidence} confidence", *(top.get("reason_chips") or [])][:MAX_CHIPS],
            "action": actions[0] if len(actions) == 1 else None,
            "actions": actions,
        })

    items.sort(key=lambda item: item["priority"], reverse=True)
    return items[:MAX_ITEMS]


def _matchup_recommendations(data: dict[str, Any]) -> dict[str, Any] | None:
    """Same gating as sandlot_api._snapshot_payload."""
    matchup = data.get("matchup")
    if not isinstance(matchup, dict) or not matchup:
        return None
    data_quality = sandlot_data_quality.snapshot_data_quality(data)
    return sandlot_matchup.rank_matchup_improvement_actions(data, data_quality)


def attention_items(data: dict[str, Any], recommendations: Any = _UNSET) -> list[dict[str, Any]]:
    """Ordered Attention Queue items for a raw snapshot blob.

    `recommendations` is injectable for tests; by default it is derived from
    the snapshot exactly like the /api/snapshot/latest payload does.
    """
    raw_rows = (data.get("roster") or {}).get("rows") or []
    roster = [_normalize_row(r, i) for i, r in enumerate(raw_rows) if isinstance(r, dict)]
    health = roster_health(roster)
    if recommendations is _UNSET:
        recommendations = _matchup_recommendations(data)
    return build_queue(health, recommendations)
