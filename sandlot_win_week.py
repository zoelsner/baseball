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
from urllib.parse import quote

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
        # Preserve the complete deterministic card frontier until remaining-
        # week opportunity is applied. Rate-first truncation can otherwise
        # erase a lower-rate streamer whose extra games win the week.
        waiver_cards, _expanded_diagnostics = sandlot_waivers.build_waiver_cards(
            roster_rows=roster_rows,
            fa_players=free_agent_rows,
            snapshot_id=int(snapshot.get("snapshot_id") or 0),
            limit=None,
            allow_nonpositive_rate=True,
        )
        waiver_cards.sort(
            key=lambda card: (
                _weekly_candidate_ceiling(snapshot, card),
                _number(card.get("sort_score")) or 0.0,
                str(card.get("id") or ""),
            ),
            reverse=True,
        )
        waiver_cards = waiver_cards[:8]

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

    lineup_bundle = _lineup_bundle(
        snapshot=snapshot,
        base_projection=base_projection,
        now=now,
    )
    if lineup_bundle:
        rankable.append(lineup_bundle)

    best_lineup_points = max(
        (
            _number((action.get("expected_points") or {}).get("estimate")) or 0.0
            for action in rankable
            if action.get("kind") in {"lineup", "lineup_plan"}
        ),
        default=0.0,
    )

    if base_projection is not None:
        for card in waiver_cards:
            action, monitor, diagnostic = _waiver_action(
                snapshot=snapshot,
                base_projection=base_projection,
                card=card,
                now=now,
                best_lineup_points=best_lineup_points,
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
    no_action = None
    if no_action_reason:
        no_action = {
            "reason": no_action_reason,
            "alternatives": _no_action_alternatives(lineup_payload, considered),
        }

    return {
        "model_version": MODEL_VERSION,
        "state": state,
        "snapshot_id": snapshot.get("snapshot_id"),
        "taken_at": snapshot.get("snapshot_taken_at"),
        "read_only": True,
        "writes_enabled": False,
        "handoffs": _fantrax_handoffs(snapshot),
        "schedule_optimizer": _schedule_optimizer_status(quality),
        "matchup": _matchup_context(matchup, base_projection),
        "summary": _summary(matchup, base_projection, primary, no_action_reason),
        "primary_action_id": primary.get("id") if primary else None,
        "actions": actions,
        "monitoring_actions": monitoring,
        "no_action": no_action,
        "diagnostics": {
            "considered": considered,
            "lineup_action_count": sum(1 for action in actions if action.get("kind") in {"lineup", "lineup_plan"}),
            "waiver_action_count": sum(1 for action in actions if action.get("kind") == "waiver"),
            "waiver_message": waiver_payload.get("message"),
            "probability_calibrated": bool(
                isinstance(base_projection, dict)
                and base_projection.get("probability_calibrated") is True
            ),
        },
    }


def _schedule_optimizer_status(data_quality: dict[str, Any]) -> dict[str, Any]:
    policy = (
        data_quality.get("lineup_change_policy")
        if isinstance(data_quality.get("lineup_change_policy"), dict)
        else {}
    )
    observed = policy.get("state") == "observed_unclassified"
    return {
        "state": "policy_unclassified" if observed else "policy_missing",
        "read_only": True,
        "writes_enabled": False,
        "reason": (
            policy.get("reason") or "Fantrax lineup cadence and lock semantics are not trusted yet."
        ),
        "policy": {
            "state": policy.get("state"),
            "cadence": policy.get("cadence"),
            "lock_scope": policy.get("lock_scope"),
            "change_limit": policy.get("change_limit"),
            "source": policy.get("source"),
            "candidate_count": policy.get("candidate_count") or 0,
        },
    }


def _fantrax_handoffs(snapshot: dict[str, Any]) -> dict[str, Any]:
    league_id = str(snapshot.get("league_id") or "").strip()
    team_id = str(snapshot.get("team_id") or "").strip()
    if not league_id or not team_id:
        return {}
    url = (
        "https://www.fantrax.com/fantasy/league/"
        f"{quote(league_id, safe='')}/team/roster;teamId={quote(team_id, safe='')}"
    )
    return {
        "lineup": {
            "label": "Open Fantrax lineup",
            "url": url,
            "method": "GET",
            "read_only": True,
            "writes_enabled": False,
        },
    }


def _no_action_alternatives(
    lineup_payload: dict[str, Any],
    considered: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Expose concise rejected options without leaking planner diagnostics."""
    alternatives: list[dict[str, Any]] = []
    lineup_no_action = lineup_payload.get("no_action") if isinstance(lineup_payload.get("no_action"), dict) else {}
    lineup_alternative = lineup_no_action.get("best_alternative")
    if isinstance(lineup_alternative, dict):
        alternatives.append(copy.deepcopy(lineup_alternative))
    else:
        rejected_delta = _number(lineup_no_action.get("best_rejected_delta"))
        if rejected_delta is not None:
            threshold = _number(lineup_no_action.get("threshold"))
            alternatives.append({
                "id": "rejected-lineup:best",
                "kind": "lineup",
                "title": "Best legal lineup change",
                "steps": [],
                "expected_points": {"estimate": round(rejected_delta, 1), "comparable": True},
                "status": "below_threshold",
                "reason": (
                    f"The estimated {rejected_delta:+.1f}-point gain is below Sandlot's "
                    f"{threshold:.1f}-point meaningful-gain threshold."
                    if threshold is not None
                    else str(lineup_no_action.get("reason") or "The move does not create meaningful positive value.")
                ),
            })

    for diagnostic in considered:
        if not isinstance(diagnostic, dict) or not diagnostic.get("reason"):
            continue
        if diagnostic.get("status") in {"movable", "provisionally_legal"}:
            continue
        alternatives.append({
            "id": str(diagnostic.get("id") or "rejected-option"),
            "kind": str(diagnostic.get("kind") or "unknown"),
            "title": str(diagnostic.get("title") or "Considered alternative"),
            "steps": copy.deepcopy(diagnostic.get("steps") or []),
            "expected_points": copy.deepcopy(
                diagnostic.get("expected_points")
                if isinstance(diagnostic.get("expected_points"), dict)
                else {"estimate": None, "comparable": False}
            ),
            "status": str(diagnostic.get("status") or "rejected"),
            "reason": str(diagnostic.get("reason")),
        })

    deduped: dict[str, dict[str, Any]] = {}
    for alternative in alternatives:
        key = str(alternative.get("id") or alternative.get("title") or "")
        if key and key not in deduped:
            deduped[key] = alternative
    status_order = {
        "below_threshold": 0,
        "dominated": 1,
        "nonpositive": 2,
        "deadline_unknown": 3,
        "move_out_locked": 4,
        "unknown": 5,
        "rejected": 6,
    }
    ranked = list(deduped.values())
    def sort_key(alternative: dict[str, Any]) -> tuple[Any, ...]:
        points = _number((alternative.get("expected_points") or {}).get("estimate"))
        return (
            points is None,
            -(points if points is not None else 0.0),
            status_order.get(str(alternative.get("status") or "rejected"), 7),
            str(alternative.get("title") or ""),
        )

    ranked.sort(key=sort_key)
    return ranked[: max(0, limit)]


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
    title = f"Start {move_in.get('name') or 'the better option'} over {move_out.get('name') or 'the current starter'}"
    diagnostic = {
        "id": action_id,
        "kind": "lineup",
        "title": title,
        "status": movability.get("state") or "unknown",
        "reason": movability.get("reason"),
        "steps": (recommendation.get("action") or {}).get("chain") or [],
        "expected_points": {"estimate": round(points, 1), "comparable": True},
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
        "title": title,
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
            "verified": ["slot eligibility", "remaining-game provenance", "Fantrax destination eligibility", "MLB start timing"],
            "requires_live_preflight": True,
        },
        "writes_enabled": False,
        "source": {"type": "matchup_recommendation", "proposal_id": proposal.get("id")},
    }
    return action, None, diagnostic


def _lineup_bundle(
    *,
    snapshot: dict[str, Any],
    base_projection: dict[str, Any] | None,
    now: datetime,
    max_moves: int = 3,
) -> dict[str, Any] | None:
    """Sequentially apply and re-simulate independent legal lineup gains."""
    base_points = _number((base_projection or {}).get("projected_my"))
    if base_points is None:
        return None
    optimization = _optimize_lineup_snapshot(snapshot, now=now, max_moves=max_moves)
    segments = optimization["segments"]
    steps = optimization["steps"]
    deadline = optimization["deadline"]
    projected_points = optimization["projected_points"]
    if len(segments) < 2 or deadline is None:
        return None
    total_gain = round(projected_points - base_points, 1)
    if total_gain <= 0:
        return None
    proposal_ids = [str(segment.get("proposal_id") or "unknown") for segment in segments]
    return {
        "id": "lineup-plan:" + ":".join(proposal_ids),
        "kind": "lineup_plan",
        "state": "act_now",
        "title": f"Make {len(segments)} lineup changes",
        "steps": steps,
        "segments": segments,
        "expected_points": {
            "estimate": total_gain,
            "basis": "sequential legal lineup simulation with the roster re-projected after every slot chain",
            "comparable": True,
        },
        "win_probability_delta": None,
        "probability_calibrated": False,
        "deadline": deadline,
        "confidence": optimization["confidence"],
        "dynasty_cost": {
            "level": "none",
            "reason": "Lineup-only plan; no player leaves the roster.",
        },
        "legality": {
            "state": "snapshot_verified",
            "verified": ["sequential slot eligibility", "remaining-game provenance", "Fantrax destination eligibility", "MLB start timing"],
            "requires_live_preflight": True,
        },
        "writes_enabled": False,
        "source": {"type": "sequential_matchup_plan", "proposal_ids": proposal_ids},
    }


def _optimize_lineup_snapshot(
    snapshot: dict[str, Any],
    *,
    now: datetime,
    max_moves: int,
) -> dict[str, Any]:
    """Return a fully re-simulated lineup state after up to ``max_moves``."""
    working = copy.deepcopy(snapshot)
    working["movability_now"] = now.isoformat()
    starting_quality = sandlot_data_quality.snapshot_data_quality(working)
    starting_projection = sandlot_matchup.compute_projection(working, starting_quality)
    projected_points = _number((starting_projection or {}).get("projected_my"))
    segments: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    deadline: dict[str, Any] | None = None
    confidence_values: list[str] = []
    if projected_points is None:
        return {
            "snapshot": working,
            "projected_points": None,
            "segments": segments,
            "steps": steps,
            "deadline": deadline,
            "confidence": "unknown",
        }

    for _index in range(max(0, max_moves)):
        quality = sandlot_data_quality.snapshot_data_quality(working)
        recommendations = sandlot_matchup.rank_matchup_improvement_actions(working, quality, limit=8)
        chosen = next(
            (
                recommendation
                for recommendation in recommendations.get("recommendations") or []
                if ((recommendation.get("replacement_card") or {}).get("movability") or {}).get("state") == "movable"
                and ((recommendation.get("replacement_card") or {}).get("deadline") or {}).get("state") == "known"
            ),
            None,
        )
        if not chosen:
            break
        chain = (chosen.get("action") or {}).get("chain") or []
        next_snapshot = _apply_lineup_chain(working, chain)
        if next_snapshot is None:
            break
        next_quality = sandlot_data_quality.snapshot_data_quality(next_snapshot)
        next_projection = sandlot_matchup.compute_projection(next_snapshot, next_quality)
        next_points = _number((next_projection or {}).get("projected_my"))
        if next_points is None or next_points <= projected_points + 0.05:
            break
        card = chosen.get("replacement_card") if isinstance(chosen.get("replacement_card"), dict) else {}
        proposal = card.get("proposal") if isinstance(card.get("proposal"), dict) else {}
        segments.append({
            "proposal_id": proposal.get("id"),
            "points_delta": round(next_points - projected_points, 1),
        })
        steps.extend(copy.deepcopy(chain))
        candidate_deadline = card.get("deadline") if isinstance(card.get("deadline"), dict) else {}
        deadline = candidate_deadline if deadline is None else _earlier_deadline(deadline, candidate_deadline)
        confidence_values.append(str(chosen.get("confidence") or "unknown").lower())
        working = next_snapshot
        projected_points = next_points

    return {
        "snapshot": working,
        "projected_points": projected_points,
        "segments": segments,
        "steps": steps,
        "deadline": deadline,
        "confidence": _lowest_confidence(confidence_values),
    }


def _apply_lineup_chain(
    snapshot: dict[str, Any],
    chain: list[dict[str, Any]],
) -> dict[str, Any] | None:
    moves = {
        str(step.get("player_id") or ""): str(step.get("to_slot") or "").strip().upper()
        for step in chain
        if isinstance(step, dict) and step.get("player_id") and step.get("to_slot")
    }
    if not moves:
        return None
    updated = copy.deepcopy(snapshot)
    roster = updated.get("roster") if isinstance(updated.get("roster"), dict) else {}
    rows = roster.get("rows") if isinstance(roster.get("rows"), list) else []
    seen: set[str] = set()
    new_rows = []
    for row in rows:
        if not isinstance(row, dict):
            new_rows.append(row)
            continue
        player_id = str(row.get("id") or row.get("player_id") or "")
        if player_id in moves:
            new_rows.append({**row, "slot": moves[player_id]})
            seen.add(player_id)
        else:
            new_rows.append(row)
    if seen != set(moves):
        return None
    updated["roster"] = {**roster, "rows": new_rows}
    return updated


def _lowest_confidence(values: list[str]) -> str:
    order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    return max(values or ["unknown"], key=lambda value: order.get(value, 3))


def _waiver_action(
    *,
    snapshot: dict[str, Any],
    base_projection: dict[str, Any],
    card: dict[str, Any],
    now: datetime,
    best_lineup_points: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    add = card.get("add") if isinstance(card.get("add"), dict) else {}
    move_out = card.get("move_out") if isinstance(card.get("move_out"), dict) else {}
    action_id = str(card.get("id") or f"waiver:{add.get('id')}:{move_out.get('id')}")
    title = f"Add {add.get('name') or 'the free agent'} and move out {move_out.get('name') or 'the roster player'}"
    diagnostic = {
        "id": action_id,
        "kind": "waiver",
        "title": title,
        "status": "rejected",
        "reason": None,
        "expected_points": {"estimate": None, "comparable": False},
    }
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

    move_out_movability = sandlot_matchup.player_roster_exit_availability(move_row, now=now)
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
    post_lineup_segments: list[dict[str, Any]] = []
    optimized_snapshot = post_snapshot
    remaining_lineup_moves = 3
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
        verified_matching = [
            recommendation
            for recommendation in matching
            if ((recommendation.get("replacement_card") or {}).get("movability") or {}).get("state") == "movable"
            and ((recommendation.get("replacement_card") or {}).get("deadline") or {}).get("state") == "known"
        ]
        if not verified_matching:
            rejected_card = (max(matching, key=lambda item: _number(item.get("points_delta")) or 0.0).get("replacement_card") or {})
            movability = rejected_card.get("movability") or {}
            lineup_deadline = rejected_card.get("deadline") or {}
            if movability.get("state") != "movable":
                reason = movability.get("reason") or "The post-add lineup bridge is not currently movable in Fantrax."
                suffix = "movability"
            else:
                reason = lineup_deadline.get("reason") or "The post-add lineup bridge has no exact known deadline."
                suffix = "deadline"
            diagnostic["status"] = "post_add_lineup_unverified"
            diagnostic["reason"] = reason
            return None, {
                "id": f"monitor:{action_id}:post-add-{suffix}",
                "kind": "monitor",
                "state": "needs_refresh",
                "title": f"Recheck the lineup path for {add.get('name') or 'the added player'}",
                "reason": reason,
                "deadline": lineup_deadline if lineup_deadline else {"state": "unknown", "at": None, "reason": reason},
                "expected_points": {"estimate": None, "basis": "impact withheld until the full post-add lineup path is verified"},
            }, diagnostic
        best_lineup = max(verified_matching, key=lambda item: _number(item.get("points_delta")) or 0.0)
        lineup_steps = (best_lineup.get("action") or {}).get("chain") or []
        optimized_snapshot = _apply_lineup_chain(post_snapshot, lineup_steps)
        if optimized_snapshot is None:
            diagnostic["reason"] = "The required post-add lineup chain could not be applied to the simulated roster."
            return None, None, diagnostic
        optimized_quality = sandlot_data_quality.snapshot_data_quality(optimized_snapshot)
        optimized_projection = sandlot_matchup.compute_projection(optimized_snapshot, optimized_quality)
        optimized_points = _number((optimized_projection or {}).get("projected_my"))
        proposal = ((best_lineup.get("replacement_card") or {}).get("proposal") or {})
        post_lineup_segments.append({
            "proposal_id": proposal.get("id"),
            "points_delta": round(_number(best_lineup.get("points_delta")) or 0.0, 1),
        })
        lineup_deadline = ((best_lineup.get("replacement_card") or {}).get("deadline") or {})
        deadline = _earlier_deadline(deadline, lineup_deadline)
        remaining_lineup_moves -= 1

    additional = _optimize_lineup_snapshot(
        optimized_snapshot,
        now=now,
        max_moves=remaining_lineup_moves,
    )
    if additional.get("projected_points") is not None:
        optimized_points = additional["projected_points"]
    lineup_steps.extend(additional.get("steps") or [])
    post_lineup_segments.extend(additional.get("segments") or [])
    if additional.get("deadline"):
        deadline = _earlier_deadline(deadline, additional["deadline"])

    base_points = _number(base_projection.get("projected_my"))
    if optimized_points is None or base_points is None:
        diagnostic["reason"] = "Projected team points are unavailable for the waiver comparison."
        return None, None, diagnostic
    impact = round(optimized_points - base_points, 1)
    diagnostic["expected_points"] = {"estimate": impact, "comparable": True}
    if impact <= 0:
        diagnostic["status"] = "nonpositive"
        diagnostic["reason"] = "The legal post-add roster does not improve remaining-week projected points."
        return None, None, diagnostic
    incremental_over_lineup = round(impact - max(0.0, best_lineup_points), 1)
    if incremental_over_lineup <= 0:
        diagnostic["status"] = "dominated"
        diagnostic["reason"] = (
            "The waiver path does not beat the best legal lineup-only plan, so the transaction and dynasty cost are unnecessary."
        )
        return None, None, diagnostic

    dynasty_cost = _dynasty_cost(move_row, card)
    diagnostic["status"] = "provisionally_legal"
    diagnostic["reason"] = "Post-add lineup path and remaining-week impact were recomputed from the snapshot."
    action = {
        "id": action_id,
        "kind": "waiver",
        "state": "review_now",
        "title": title,
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
        "lineup_segments": post_lineup_segments,
        "expected_points": {
            "estimate": impact,
            "incremental_over_best_lineup": incremental_over_lineup,
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


def _weekly_candidate_ceiling(snapshot: dict[str, Any], card: dict[str, Any]) -> float:
    """Cheap upper-bound ranking before expensive post-add lineup simulation."""
    add = card.get("add") if isinstance(card.get("add"), dict) else {}
    move_out = card.get("move_out") if isinstance(card.get("move_out"), dict) else {}
    add_row = _find_free_agent(snapshot, add.get("id")) or {}
    move_row = _find_roster_player(snapshot, move_out.get("id")) or {}
    add_points = (_number(add.get("fpg")) or 0.0) * _countable_games(add_row)
    move_slot = str(move_row.get("slot") or "").strip().upper()
    move_points = 0.0
    if move_slot not in BENCH_SLOTS:
        move_points = (_number(move_row.get("fppg")) or 0.0) * _countable_games(move_row)
    return round(add_points - move_points, 3)


def _countable_games(row: dict[str, Any]) -> int:
    games = row.get("future_games") if isinstance(row.get("future_games"), list) else []
    if not games:
        return 0
    if not sandlot_matchup.player_can_play_slot(row, "P"):
        return len([game for game in games if isinstance(game, dict)])
    return len([
        game
        for game in games
        if isinstance(game, dict)
        and any(game.get(key) for key in ("probable_start", "confirmed_start", "scheduled_start", "probable_pitcher"))
    ])


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
    kind_priority = 0 if action.get("kind") in {"lineup", "lineup_plan"} else 1
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
        "opportunity_completeness": (projection or {}).get("opportunity_completeness"),
        "pitchers_without_probable_start": (projection or {}).get("pitchers_without_probable_start") or 0,
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
    projected_margin = _projected_margin(projection)
    projected_margin_after_action = None
    if primary:
        points = _number((primary.get("expected_points") or {}).get("estimate")) or 0.0
        if projected_margin is not None:
            projected_margin_after_action = round(projected_margin + points, 1)
        if margin is not None and margin < 0:
            headline = f"Down {abs(margin):.1f}; the best current path adds about {points:.1f} projected points."
        elif margin is not None and margin > 0:
            headline = f"Up {margin:.1f}; the best current path adds about {points:.1f} projected points to protect the lead."
        else:
            headline = f"The best current path adds about {points:.1f} projected points."
    else:
        headline = no_action_reason or "No action plan is available."
    outlook_margin = projected_margin_after_action if primary else projected_margin
    outlook = None
    if outlook_margin is not None and not matchup.get("complete"):
        prefix = "After this move, the remaining-week estimate" if primary else "The current remaining-week estimate"
        if outlook_margin < 0:
            outlook = f"{prefix} leaves you {abs(outlook_margin):.1f} points behind."
        elif outlook_margin > 0:
            outlook = f"{prefix} puts you {outlook_margin:.1f} points ahead."
        else:
            outlook = f"{prefix} has the matchup tied."
    return {
        "headline": headline,
        "outlook": outlook,
        "best_action_id": primary.get("id") if primary else None,
        "best_action_points": (primary.get("expected_points") or {}).get("estimate") if primary else None,
        "projected_margin_before_action": projected_margin,
        "projected_margin_after_action": projected_margin_after_action,
        "win_probability_excluded_reason": None
        if (projection or {}).get("probability_calibrated") is True
        else "Win probability is not calibrated; actions are ranked by projected remaining-week points.",
        "projection_caveat": (
            f"Known-opportunity lower bound: {(projection or {}).get('pitchers_without_probable_start')} pitcher(s) have no posted probable start and contribute zero until that changes."
            if (projection or {}).get("opportunity_completeness") == "known_opportunities_lower_bound"
            else None
        ),
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
