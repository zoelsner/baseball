"""Deterministic, read-only action plan for maximizing the current matchup.

The planner only ranks actions whose remaining-week point impact can be
recomputed from the persisted Fantrax snapshot. AI may explain this payload,
but it does not select, score, or legalize actions.
"""

from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from typing import Any

import sandlot_data_quality
import sandlot_future_games
import sandlot_matchup
import sandlot_waivers


MODEL_VERSION = "win_this_week_v1"
BENCH_SLOTS = {"BN", "BE", "BENCH", "RES", "RESERVE"}
DYNASTY_COST_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "unknown": 4}


def build_plan(
    snapshot_row: dict[str, Any],
    *,
    now: datetime | None = None,
    limit: int = 5,
    data_quality: dict[str, Any] | None = None,
    lineup_recommendations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one ranked, read-only current-matchup plan from a stored snapshot."""
    now = _aware_now(now)
    raw_data = snapshot_row.get("data") if isinstance(snapshot_row.get("data"), dict) else {}
    snapshot = {
        **raw_data,
        "snapshot_id": snapshot_row.get("id") or raw_data.get("snapshot_id"),
        "snapshot_taken_at": snapshot_row.get("taken_at") or raw_data.get("snapshot_taken_at"),
        "movability_now": now.isoformat(),
    }
    quality = data_quality or sandlot_data_quality.snapshot_data_quality(snapshot)
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
    base_projection = sandlot_matchup.compute_projection(snapshot, quality)
    lineup_payload = lineup_recommendations or sandlot_matchup.rank_matchup_improvement_actions(
        snapshot,
        quality,
    )
    waiver_payload = sandlot_waivers.payload_for_snapshot(
        {**snapshot_row, "data": snapshot},
        overlay_cached_ai=False,
    )
    waiver_cards = waiver_payload.get("cards") or []
    if quality.get("add_drop_recommendations_ready") is True:
        roster_rows = ((snapshot.get("roster") or {}).get("rows") or [])
        free_agent_rows = ((snapshot.get("free_agents") or {}).get("players") or [])
        # Re-score a bounded, schedule-ranked candidate frontier. Exact
        # post-add simulation is intentionally more expensive than card math.
        expanded_limit = min(30, max(10, len(free_agent_rows)))
        waiver_cards, _expanded_diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster_rows,
            fa_players=free_agent_rows,
            snapshot_id=int(snapshot.get("snapshot_id") or 0),
            limit=expanded_limit,
            allow_nonpositive_rate=True,
        )

    considered: list[dict[str, Any]] = []
    rankable: list[dict[str, Any]] = []
    monitoring: list[dict[str, Any]] = []

    for recommendation in lineup_payload.get("recommendations") or []:
        action, monitor, diagnostic = _lineup_action(recommendation)
        if diagnostic:
            considered.append(diagnostic)
        if action:
            rankable.append(action)
        if monitor:
            monitoring.append(monitor)

    if base_projection is not None:
        for card in waiver_cards:
            action, monitor, diagnostic = _waiver_action(
                snapshot=snapshot,
                base_projection=base_projection,
                card=card,
                now=now,
            )
            if diagnostic:
                considered.append(diagnostic)
            if action:
                rankable.append(action)
            if monitor:
                monitoring.append(monitor)

    rankable.sort(key=_action_sort_key)
    actions = rankable[: max(0, limit)]
    for index, action in enumerate(actions, start=1):
        action["rank"] = index

    primary = actions[0] if actions else None
    if primary and isinstance(primary.get("deadline"), dict):
        deadline = primary["deadline"]
        if deadline.get("state") == "known":
            monitoring.append(_deadline_monitor(primary, deadline))
    monitoring = _dedupe_monitoring(monitoring)

    complete = bool(matchup.get("complete"))
    if complete:
        state = "complete"
    elif actions:
        state = "ready"
    elif base_projection is None:
        state = "paused"
    else:
        state = "no_action"

    no_action_reason = None
    if not actions:
        if complete:
            no_action_reason = "The matchup is complete."
        elif base_projection is None:
            no_action_reason = "Win This Week is paused because remaining-week projection inputs are incomplete."
        else:
            no_action_reason = (
                (lineup_payload.get("no_action") or {}).get("reason")
                or waiver_payload.get("message")
                or "No legal action has a positive, provenance-backed remaining-week impact."
            )

    return {
        "model_version": MODEL_VERSION,
        "state": state,
        "snapshot_id": snapshot.get("snapshot_id"),
        "taken_at": snapshot.get("snapshot_taken_at"),
        "read_only": True,
        "writes_enabled": False,
        "matchup": _matchup_context(matchup, base_projection),
        "summary": _summary(matchup, base_projection, primary, no_action_reason),
        "primary_action_id": primary.get("id") if primary else None,
        "actions": actions,
        "monitoring_actions": monitoring,
        "no_action": {"reason": no_action_reason} if no_action_reason else None,
        "diagnostics": {
            "considered": considered,
            "lineup_action_count": sum(1 for action in actions if action.get("kind") == "lineup"),
            "waiver_action_count": sum(1 for action in actions if action.get("kind") == "waiver"),
            "waiver_message": waiver_payload.get("message"),
            "probability_calibrated": bool(
                isinstance(base_projection, dict)
                and base_projection.get("probability_calibrated") is True
            ),
        },
    }


def _lineup_action(
    recommendation: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    card = recommendation.get("replacement_card") if isinstance(recommendation.get("replacement_card"), dict) else {}
    move_in = card.get("move_in") if isinstance(card.get("move_in"), dict) else {}
    move_out = card.get("move_out") if isinstance(card.get("move_out"), dict) else {}
    movability = card.get("movability") if isinstance(card.get("movability"), dict) else {}
    deadline = card.get("deadline") if isinstance(card.get("deadline"), dict) else {
        "state": "unknown",
        "at": None,
        "reason": "Lineup deadline is unavailable.",
    }
    proposal = card.get("proposal") if isinstance(card.get("proposal"), dict) else {}
    points = _number(recommendation.get("points_delta")) or 0.0
    action_id = str(proposal.get("id") or f"lineup:{move_out.get('id')}:{move_in.get('id')}")
    diagnostic = {
        "id": action_id,
        "kind": "lineup",
        "status": movability.get("state") or "unknown",
        "reason": movability.get("reason"),
    }
    if points <= 0:
        diagnostic["status"] = "nonpositive"
        return None, None, diagnostic
    if movability.get("state") != "movable":
        monitor = {
            "id": f"monitor:{action_id}",
            "kind": "monitor",
            "state": "needs_refresh",
            "title": f"Recheck whether {move_in.get('name') or 'the move-in player'} can be activated",
            "reason": movability.get("reason") or "Fantrax movability is not proven.",
            "deadline": deadline,
            "expected_points": {
                "estimate": round(points, 1),
                "basis": "points available only if movability becomes verified; not additive",
            },
        }
        return None, monitor, diagnostic
    if deadline.get("state") != "known":
        diagnostic["status"] = "deadline_unknown"
        diagnostic["reason"] = deadline.get("reason") or "The action deadline is not known."
        return None, {
            "id": f"monitor:{action_id}:deadline",
            "kind": "monitor",
            "state": "needs_refresh",
            "title": f"Confirm the lineup deadline for {move_in.get('name') or 'the move-in player'}",
            "reason": diagnostic["reason"],
            "deadline": deadline,
            "expected_points": {
                "estimate": round(points, 1),
                "basis": "points available only after an exact deadline is known; not additive",
            },
        }, diagnostic

    action = {
        "id": action_id,
        "kind": "lineup",
        "state": "act_now",
        "title": f"Start {move_in.get('name') or 'the better option'} over {move_out.get('name') or 'the current starter'}",
        "steps": (recommendation.get("action") or {}).get("chain") or [],
        "expected_points": {
            "estimate": round(points, 1),
            "basis": "snapshot FP/G × remaining usable games from the legal lineup simulation",
            "comparable": True,
        },
        "win_probability_delta": recommendation.get("win_probability_delta"),
        "probability_calibrated": recommendation.get("probability_calibrated") is True,
        "deadline": deadline,
        "confidence": str(recommendation.get("confidence") or "unknown").lower(),
        "dynasty_cost": {
            "level": "none",
            "reason": "Lineup-only move; no player leaves the roster.",
        },
        "legality": {
            "state": "snapshot_verified",
            "verified": ["slot eligibility", "remaining-game provenance", "Fantrax movability flag", "MLB start timing"],
            "requires_live_preflight": True,
        },
        "writes_enabled": False,
        "source": {"type": "matchup_recommendation", "proposal_id": proposal.get("id")},
    }
    return action, None, diagnostic


def _waiver_action(
    *,
    snapshot: dict[str, Any],
    base_projection: dict[str, Any],
    card: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    add = card.get("add") if isinstance(card.get("add"), dict) else {}
    move_out = card.get("move_out") if isinstance(card.get("move_out"), dict) else {}
    action_id = str(card.get("id") or f"waiver:{add.get('id')}:{move_out.get('id')}")
    diagnostic = {"id": action_id, "kind": "waiver", "status": "rejected", "reason": None}
    add_row = _find_free_agent(snapshot, add.get("id"))
    move_row = _find_roster_player(snapshot, move_out.get("id"))
    if not add_row or not move_row:
        diagnostic["reason"] = "The add or move-out player is missing from the persisted snapshot."
        return None, None, diagnostic

    schedule_reason = _schedule_rejection(add_row)
    if schedule_reason:
        diagnostic["reason"] = schedule_reason
        monitor = {
            "id": f"monitor:{action_id}:schedule",
            "kind": "monitor",
            "state": "needs_refresh",
            "title": f"Refresh {add.get('name') or 'the free agent'} remaining schedule",
            "reason": schedule_reason,
            "deadline": {"state": "unknown", "at": None, "reason": schedule_reason},
            "expected_points": {"estimate": None, "basis": "impact withheld until schedule provenance is trusted"},
        }
        return None, monitor, diagnostic

    deadline = _player_deadline(add_row, now)
    if deadline.get("state") != "known":
        diagnostic["reason"] = deadline.get("reason")
        return None, {
            "id": f"monitor:{action_id}:deadline",
            "kind": "monitor",
            "state": "needs_refresh",
            "title": f"Confirm the transaction deadline for {add.get('name') or 'the free agent'}",
            "reason": deadline.get("reason"),
            "deadline": deadline,
            "expected_points": {"estimate": None, "basis": "impact withheld until an exact action deadline is known"},
        }, diagnostic

    move_out_movability = sandlot_matchup.player_movability(move_row, now=now)
    if move_out_movability.get("state") != "movable":
        diagnostic["status"] = "move_out_locked"
        diagnostic["reason"] = move_out_movability.get("reason") or "Move-out player availability is not proven."
        return None, {
            "id": f"monitor:{action_id}:move-out",
            "kind": "monitor",
            "state": "needs_refresh",
            "title": f"Recheck whether {move_out.get('name') or 'the move-out player'} can leave the roster",
            "reason": diagnostic["reason"],
            "deadline": {"state": "unknown", "at": None, "reason": diagnostic["reason"]},
            "expected_points": {"estimate": None, "basis": "impact withheld until move-out legality is proven"},
        }, diagnostic
    move_out_deadline = _player_deadline(move_row, now)
    deadline = _earlier_deadline(deadline, move_out_deadline)

    post_snapshot, path = _post_add_snapshot(snapshot, add_row, move_row, add)
    if post_snapshot is None:
        diagnostic["reason"] = path.get("reason")
        return None, None, diagnostic

    post_quality = sandlot_data_quality.snapshot_data_quality(post_snapshot)
    post_projection = sandlot_matchup.compute_projection(post_snapshot, post_quality)
    if post_projection is None:
        diagnostic["reason"] = "The hypothetical post-add roster does not pass projection provenance gates."
        return None, None, diagnostic

    optimized_points = _number(post_projection.get("projected_my"))
    lineup_steps: list[dict[str, Any]] = []
    if path.get("mode") == "bench_then_lineup":
        post_lineup = sandlot_matchup.rank_matchup_improvement_actions(post_snapshot, post_quality, limit=8)
        matching = [
            recommendation
            for recommendation in post_lineup.get("recommendations") or []
            if str((((recommendation.get("replacement_card") or {}).get("move_in") or {}).get("id") or ""))
            == str(add.get("id") or "")
        ]
        if not matching:
            diagnostic["reason"] = "The added player has no proven bench-to-active lineup path."
            return None, None, diagnostic
        best_lineup = max(matching, key=lambda item: _number(item.get("points_delta")) or 0.0)
        optimized_points = (optimized_points or 0.0) + (_number(best_lineup.get("points_delta")) or 0.0)
        lineup_steps = (best_lineup.get("action") or {}).get("chain") or []
        lineup_deadline = ((best_lineup.get("replacement_card") or {}).get("deadline") or {})
        deadline = _earlier_deadline(deadline, lineup_deadline)

    base_points = _number(base_projection.get("projected_my"))
    if optimized_points is None or base_points is None:
        diagnostic["reason"] = "Projected team points are unavailable for the waiver comparison."
        return None, None, diagnostic
    impact = round(optimized_points - base_points, 1)
    if impact <= 0:
        diagnostic["status"] = "nonpositive"
        diagnostic["reason"] = "The legal post-add roster does not improve remaining-week projected points."
        return None, None, diagnostic

    dynasty_cost = _dynasty_cost(move_row, card)
    diagnostic["status"] = "provisionally_legal"
    diagnostic["reason"] = "Post-add lineup path and remaining-week impact were recomputed from the snapshot."
    action = {
        "id": action_id,
        "kind": "waiver",
        "state": "review_now",
        "title": f"Add {add.get('name') or 'the free agent'} and move out {move_out.get('name') or 'the roster player'}",
        "steps": [
            {
                "action": "add",
                "player_id": add.get("id"),
                "player_name": add.get("name"),
                "to_slot": path.get("initial_slot"),
            },
            {"action": "move_out", "player_id": move_out.get("id"), "player_name": move_out.get("name")},
            *([{
                "action": "start",
                "player_id": add.get("id"),
                "player_name": add.get("name"),
                "to_slot": path.get("initial_slot"),
            }] if path.get("mode") == "direct_replacement" else []),
            *lineup_steps,
        ],
        "expected_points": {
            "estimate": impact,
            "basis": "original projection versus a provenance-checked hypothetical post-add roster and legal lineup path",
            "comparable": True,
        },
        "win_probability_delta": None,
        "probability_calibrated": False,
        "deadline": deadline,
        "confidence": str(card.get("confidence") or "unknown").lower(),
        "dynasty_cost": dynasty_cost,
        "legality": {
            "state": "provisionally_legal",
            "path": path,
            "verified": ["positive trusted FP/G", "remaining schedule", "position eligibility", "drop protection", "move-out movability", "post-add projection"],
            "blocked_by": ["live_fantrax_availability_and_transaction_preflight"],
            "requires_live_preflight": True,
        },
        "writes_enabled": False,
        "source": {"type": "waiver_card", "card_id": card.get("id")},
    }
    return action, None, diagnostic


def _post_add_snapshot(
    snapshot: dict[str, Any],
    add_row: dict[str, Any],
    move_row: dict[str, Any],
    add_card: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    move_slot = str(move_row.get("slot") or "").strip().upper()
    if move_slot in BENCH_SLOTS:
        target_slot = "BN"
        mode = "bench_then_lineup"
    elif sandlot_matchup.player_can_play_slot(add_row, move_slot):
        target_slot = move_slot
        mode = "direct_replacement"
    else:
        return None, {
            "mode": "unproven",
            "reason": f"{add_card.get('name') or 'The free agent'} cannot directly fill {move_slot or 'the vacated slot'}, and no complete slot chain is proven.",
        }

    synthetic = {
        **copy.deepcopy(add_row),
        "id": str(add_card.get("id") or add_row.get("id") or "planned-add"),
        "name": add_card.get("name") or add_row.get("name") or "Planned add",
        "fppg": _number(add_card.get("fpg")),
        "slot": target_slot,
        "slot_source": f"planned_add.{mode}",
        "all_positions": _position_values(add_row),
    }
    if synthetic.get("fppg") is None:
        return None, {"mode": "unproven", "reason": "The free agent is missing a trusted FP/G value."}

    post_snapshot = copy.deepcopy(snapshot)
    roster = post_snapshot.get("roster") if isinstance(post_snapshot.get("roster"), dict) else {}
    rows = roster.get("rows") if isinstance(roster.get("rows"), list) else []
    move_id = str(move_row.get("id") or "")
    replaced = False
    post_rows = []
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == move_id and not replaced:
            post_rows.append(synthetic)
            replaced = True
        else:
            post_rows.append(row)
    if not replaced:
        return None, {"mode": "unproven", "reason": "The move-out player could not be replaced in the roster simulation."}
    post_snapshot["roster"] = {**roster, "rows": post_rows}
    return post_snapshot, {"mode": mode, "initial_slot": target_slot}


def _schedule_rejection(row: dict[str, Any]) -> str | None:
    status = str(row.get("future_games_status") or "").strip()
    source = str(row.get("future_games_source") or "").strip()
    if source != sandlot_future_games.SCHEDULE_SOURCE:
        return "Free-agent schedule source is not MLB schedule-backed."
    if status not in sandlot_future_games.OK_FUTURE_GAME_STATUSES:
        return str(row.get("future_games_reason") or f"Free-agent schedule status is {status or 'missing'}.")
    if not isinstance(row.get("future_games"), list):
        return "Free-agent remaining games are missing."
    return None


def _player_deadline(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    future: list[tuple[datetime, dict[str, Any]]] = []
    unknown = False
    for game in row.get("future_games") or []:
        if not isinstance(game, dict):
            continue
        starts_at = _parse_datetime(game.get("gameDate") or game.get("game_date") or game.get("start_time"))
        if starts_at is None:
            unknown = True
            continue
        if starts_at > now:
            future.append((starts_at, game))
    if future:
        starts_at, _game = min(future, key=lambda item: item[0])
        return {
            "state": "known",
            "at": starts_at.isoformat(),
            "source": "mlb_schedule.gameDate",
            "reason": f"Complete the transaction before {row.get('name') or 'the player'} starts.",
        }
    if unknown:
        return {
            "state": "unknown",
            "at": None,
            "source": "mlb_schedule.gameDate",
            "reason": "At least one remaining game is missing an exact start time.",
        }
    return {
        "state": "none_scheduled",
        "at": None,
        "source": "mlb_schedule.gameDate",
        "reason": "The player has no remaining scheduled game with a future start time.",
    }


def _earlier_deadline(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    first_at = _parse_datetime(first.get("at"))
    second_at = _parse_datetime(second.get("at"))
    if first_at and second_at:
        return first if first_at <= second_at else second
    if second_at:
        return second
    return first


def _dynasty_cost(move_row: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    age = _number(move_row.get("age"))
    note = str(card.get("dynasty_note") or "Dynasty context is unavailable.")
    if age is None:
        level = "unknown"
    elif age <= 27:
        level = "medium"
    elif age <= 30:
        level = "low"
    else:
        level = "none"
    return {"level": level, "player_age": age, "reason": note}


def _find_free_agent(snapshot: dict[str, Any], player_id: Any) -> dict[str, Any] | None:
    free_agents = snapshot.get("free_agents") if isinstance(snapshot.get("free_agents"), dict) else {}
    return _find_player(free_agents.get("players") or [], player_id)


def _find_roster_player(snapshot: dict[str, Any], player_id: Any) -> dict[str, Any] | None:
    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    return _find_player(roster.get("rows") or [], player_id)


def _find_player(rows: list[Any], player_id: Any) -> dict[str, Any] | None:
    target = str(player_id or "")
    return next(
        (row for row in rows if isinstance(row, dict) and str(row.get("id") or "") == target),
        None,
    )


def _position_values(row: dict[str, Any]) -> list[str]:
    raw = row.get("all_positions") or row.get("multi_positions") or row.get("positions") or row.get("pos") or []
    values = raw if isinstance(raw, list) else str(raw).replace("/", ",").split(",")
    return [str(value).strip() for value in values if str(value).strip()]


def _action_sort_key(action: dict[str, Any]) -> tuple[Any, ...]:
    points = _number((action.get("expected_points") or {}).get("estimate")) or 0.0
    dynasty_level = str((action.get("dynasty_cost") or {}).get("level") or "unknown")
    kind_priority = 0 if action.get("kind") == "lineup" else 1
    return (-points, DYNASTY_COST_ORDER.get(dynasty_level, 4), kind_priority, str(action.get("id") or ""))


def _deadline_monitor(action: dict[str, Any], deadline: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"monitor:{action.get('id')}:preflight",
        "kind": "monitor",
        "state": "scheduled_check",
        "title": "Recheck Fantrax and MLB status before the action deadline",
        "reason": "Availability, lineup confirmation, and Fantrax locks can change after the snapshot.",
        "deadline": deadline,
        "expected_points": {
            "estimate": (action.get("expected_points") or {}).get("estimate"),
            "basis": "protects the primary action estimate; not additive",
        },
    }


def _dedupe_monitoring(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for action in actions:
        key = str(action.get("id") or action.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out


def _matchup_context(matchup: dict[str, Any], projection: dict[str, Any] | None) -> dict[str, Any]:
    my_score = _number(matchup.get("my_score"))
    opponent_score = _number(matchup.get("opponent_score"))
    return {
        "my_score": my_score,
        "opponent_score": opponent_score,
        "margin": round(my_score - opponent_score, 1) if my_score is not None and opponent_score is not None else None,
        "projected_my": (projection or {}).get("projected_my"),
        "projected_opponent": (projection or {}).get("projected_opp"),
        "projected_margin": _projected_margin(projection),
        "win_probability": (projection or {}).get("win_probability") if (projection or {}).get("probability_calibrated") is True else None,
        "probability_calibrated": (projection or {}).get("probability_calibrated") is True,
        "period_end": matchup.get("end"),
        "complete": bool(matchup.get("complete")),
    }


def _summary(
    matchup: dict[str, Any],
    projection: dict[str, Any] | None,
    primary: dict[str, Any] | None,
    no_action_reason: str | None,
) -> dict[str, Any]:
    my_score = _number(matchup.get("my_score"))
    opponent_score = _number(matchup.get("opponent_score"))
    margin = (my_score - opponent_score) if my_score is not None and opponent_score is not None else None
    if primary:
        points = _number((primary.get("expected_points") or {}).get("estimate")) or 0.0
        if margin is not None and margin < 0:
            headline = f"Down {abs(margin):.1f}; the best current path adds about {points:.1f} projected points."
        elif margin is not None and margin > 0:
            headline = f"Up {margin:.1f}; the best current path adds about {points:.1f} projected points to protect the lead."
        else:
            headline = f"The best current path adds about {points:.1f} projected points."
    else:
        headline = no_action_reason or "No action plan is available."
    return {
        "headline": headline,
        "best_action_id": primary.get("id") if primary else None,
        "best_action_points": (primary.get("expected_points") or {}).get("estimate") if primary else None,
        "projected_margin_before_action": _projected_margin(projection),
        "win_probability_excluded_reason": None
        if (projection or {}).get("probability_calibrated") is True
        else "Win probability is not calibrated; actions are ranked by projected remaining-week points.",
    }


def _projected_margin(projection: dict[str, Any] | None) -> float | None:
    my_points = _number((projection or {}).get("projected_my"))
    opponent_points = _number((projection or {}).get("projected_opp"))
    if my_points is None or opponent_points is None:
        return None
    return round(my_points - opponent_points, 1)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _aware_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
