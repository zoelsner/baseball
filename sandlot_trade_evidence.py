"""Decision-time evidence contracts for future trade outcome measurement.

This module is deliberately deterministic and has no database or network I/O.
Refresh code supplies Fantrax periods plus one MLB schedule response; receipt
code freezes the resulting horizon and player-role evidence before outcomes
exist.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import sandlot_scoring
import sandlot_data_quality


ET = ZoneInfo("America/New_York")
PERIOD_CALENDAR_VERSION = "fantrax_mlb_period_calendar_v1"
IDENTITY_INDEX_VERSION = "fantrax_trade_player_identity_v1"
ROLE_POLICY_VERSION = "fantrax_asset_scoring_entities_v1"
OUTCOME_CONTRACT_VERSION = "trade_first_complete_period_asset_production_v1"
SELECTION_RULE = "first_period_first_game_after_cutoff_v1"
SCORING_RULES_VERSION = "sandlot_scoring_2026_v1"
SCORING_SOURCE_VERSION = "fantrax_by_period_fpts_v1"
CALENDAR_SOURCE = "fantrax.scoring_period_results+statsapi.schedule"

HITTER_TOKENS = {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF", "UT", "UTIL", "MI", "CI", "DH"}
PITCHER_TOKENS = {"SP", "RP", "P"}
GENERIC_SLOT_TOKENS = {"RES", "BN", "BENCH", "IL", "IR", "INJ", "MIN", "NA"}
EXCLUDED_GAME_STATES = {"cancelled", "canceled", "postponed"}


def build_period_calendar(
    *,
    league_id: str,
    periods: list[dict[str, Any]],
    schedule_payload: dict[str, Any],
    captured_at: datetime | str,
) -> dict[str, Any]:
    """Normalize a regular-season Fantrax calendar and exact first game times."""
    captured = _utc_datetime(captured_at, "calendar captured_at")
    normalized_periods = _normalize_periods(periods)
    season_values = {item["season"] for item in normalized_periods}
    structural_reasons = _period_structure_reasons(normalized_periods)
    games = _normalize_schedule_games(schedule_payload)
    schedule_content_hash = _sha256({"games": games})

    calendar_periods = []
    for period in normalized_periods:
        candidates = [
            game for game in games
            if period["start"] <= _utc_datetime(game["game_at"], "game_at").astimezone(ET).date().isoformat() <= period["end"]
        ]
        first = candidates[0] if candidates else None
        status = "exact" if first else "missing"
        reason = None if first else "period_first_scoring_event_missing"
        calendar_periods.append({
            **period,
            "status": status,
            "reason": reason,
            "first_scoring_event_at": first["game_at"] if first else None,
            "first_game": first,
            "candidate_game_count": len(candidates),
            "candidate_games_hash": _sha256({"games": candidates}),
            "candidate_games": candidates,
        })

    status = "invalid" if structural_reasons else (
        "ready" if calendar_periods and all(item["status"] == "exact" for item in calendar_periods) else "partial"
    )
    reason = structural_reasons[0] if structural_reasons else (
        None if status == "ready" else "one_or_more_period_deadlines_missing"
    )
    content = {
        "version": PERIOD_CALENDAR_VERSION,
        "source": CALENDAR_SOURCE,
        "league_id": _required_text(league_id, "league_id"),
        "season": next(iter(season_values)) if len(season_values) == 1 else None,
        "regular_season_only": True,
        "timezone": "America/New_York",
        "status": status,
        "reason": reason,
        "structural_reasons": structural_reasons,
        "schedule_content_hash": schedule_content_hash,
        "periods": calendar_periods,
    }
    content_hash = _sha256(content)
    return {
        **content,
        "captured_at": captured.isoformat(),
        "content_hash": content_hash,
        "capture_hash": _sha256({**content, "captured_at": captured.isoformat()}),
    }


def unavailable_period_calendar(
    *, league_id: str, captured_at: datetime | str, reason: str,
) -> dict[str, Any]:
    captured = _utc_datetime(captured_at, "calendar captured_at")
    safe_reason = reason if reason in {
        "fantrax_periods_missing", "mlb_schedule_fetch_failed", "calendar_normalization_failed",
    } else "calendar_unavailable"
    content = {
        "version": PERIOD_CALENDAR_VERSION,
        "source": CALENDAR_SOURCE,
        "league_id": _required_text(league_id, "league_id"),
        "season": None,
        "regular_season_only": True,
        "timezone": "America/New_York",
        "status": "unavailable",
        "reason": safe_reason,
        "structural_reasons": [],
        "schedule_content_hash": None,
        "periods": [],
    }
    return {
        **content,
        "captured_at": captured.isoformat(),
        "content_hash": _sha256(content),
        "capture_hash": _sha256({**content, "captured_at": captured.isoformat()}),
    }


def validate_period_calendar(calendar: dict[str, Any]) -> None:
    if calendar.get("version") != PERIOD_CALENDAR_VERSION or calendar.get("source") != CALENDAR_SOURCE:
        raise ValueError("trade period calendar version or source is unsupported")
    captured_at = _utc_datetime(calendar.get("captured_at"), "calendar captured_at")
    content = {key: value for key, value in calendar.items() if key not in {"captured_at", "content_hash", "capture_hash"}}
    if calendar.get("content_hash") != _sha256(content):
        raise ValueError("trade period calendar content hash is invalid")
    if calendar.get("capture_hash") != _sha256({**content, "captured_at": captured_at.isoformat()}):
        raise ValueError("trade period calendar capture hash is invalid")
    periods = calendar.get("periods") if isinstance(calendar.get("periods"), list) else []
    structural = _period_structure_reasons(periods)
    if structural != sorted(set(calendar.get("structural_reasons") or [])):
        raise ValueError("trade period calendar structure is inconsistent")
    for period in periods:
        if period.get("status") != "exact":
            continue
        first = period.get("first_game") if isinstance(period.get("first_game"), dict) else {}
        if first.get("game_at") != period.get("first_scoring_event_at") or int(period.get("candidate_game_count") or 0) < 1:
            raise ValueError("trade period first scoring evidence is inconsistent")
        candidates = period.get("candidate_games") if isinstance(period.get("candidate_games"), list) else []
        if (
            len(candidates) != int(period.get("candidate_game_count") or 0)
            or period.get("candidate_games_hash") != _sha256({"games": candidates})
            or not candidates
            or candidates[0] != first
            or candidates != sorted(candidates, key=lambda item: (item.get("game_at") or "", item.get("game_pk") or 0))
        ):
            raise ValueError("trade period candidate game evidence is inconsistent")
        start = date.fromisoformat(str(period.get("start")))
        end = date.fromisoformat(str(period.get("end")))
        observed_date = _utc_datetime(first.get("game_at"), "first game time").astimezone(ET).date()
        if not start <= observed_date <= end:
            raise ValueError("trade period first scoring evidence is outside its period")
        for game in candidates:
            game_date = _utc_datetime(game.get("game_at"), "candidate game time").astimezone(ET).date()
            if not start <= game_date <= end:
                raise ValueError("trade period candidate game evidence is outside its period")


def build_player_identity_index(
    *,
    snapshot: dict[str, Any],
    observed_at: datetime | str | None,
    resolver: Callable[[str, str | None, int], dict[str, Any]],
    season: int | None,
) -> dict[str, Any]:
    """Freeze exact Fantrax scorer identities, roles, and optional MLB links."""
    rows: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    sources = []
    roster = snapshot.get("roster") if isinstance(snapshot.get("roster"), dict) else {}
    sources.extend(roster.get("rows") or [])
    all_rosters = snapshot.get("all_team_rosters") if isinstance(snapshot.get("all_team_rosters"), dict) else {}
    for team in all_rosters.values():
        if isinstance(team, dict):
            sources.extend(team.get("rows") or [])
    for raw in sources:
        if not isinstance(raw, dict):
            continue
        player_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not player_id or not name:
            continue
        fingerprint = (name, str(raw.get("team") or "").strip(), str(raw.get("positions") or "").strip())
        existing = rows.get(player_id)
        if existing and existing["_fingerprint"] != fingerprint:
            conflicts.add(player_id)
            continue
        rows.setdefault(player_id, {"_fingerprint": fingerprint, "row": raw})

    players = []
    for player_id, item in sorted(rows.items()):
        raw = item["row"]
        role = scoring_entity_evidence(raw)
        if player_id in conflicts:
            identity = {"status": "conflict", "mlb_id": None, "source": "snapshot_conflict", "version": IDENTITY_INDEX_VERSION}
        else:
            if season is None:
                resolved = {"status": "season_unavailable", "mlb_id": None, "source": "mlb_stats_active_players_v1"}
            else:
                try:
                    resolved = resolver(
                        str(raw.get("name") or ""),
                        str(raw.get("team") or "").strip() or None,
                        int(season),
                    )
                except Exception:
                    resolved = {"status": "unavailable", "mlb_id": None, "source": "mlb_stats_active_players_v1"}
            try:
                mlb_id = int(resolved["mlb_id"]) if resolved.get("mlb_id") is not None else None
            except (TypeError, ValueError):
                mlb_id = None
                resolved = {**resolved, "status": "invalid_mlb_id"}
            identity = {
                "status": str(resolved.get("status") or "unavailable"),
                "mlb_id": mlb_id,
                "source": str(resolved.get("source") or "mlb_stats_active_players_v1"),
                "version": IDENTITY_INDEX_VERSION,
            }
        players.append({
            "fantrax_id": player_id,
            "player_name": str(raw.get("name") or "").strip(),
            "mlb_identity": identity,
            "scoring_role": role,
        })

    mlb_to_assets: dict[int, list[dict[str, Any]]] = {}
    for player in players:
        mlb_id = player["mlb_identity"].get("mlb_id")
        if mlb_id is not None:
            mlb_to_assets.setdefault(int(mlb_id), []).append(player)
    for duplicates in mlb_to_assets.values():
        if len(duplicates) > 1:
            for player in duplicates:
                player["mlb_identity"] = {**player["mlb_identity"], "status": "duplicate_mapping"}

    observed = _utc_datetime(observed_at or datetime.now(timezone.utc), "identity observed_at")
    content = {
        "version": IDENTITY_INDEX_VERSION,
        "source": "fantrax_snapshot+mlb_stats_active_players_v1",
        "season": int(season) if season is not None else None,
        "role_policy_version": ROLE_POLICY_VERSION,
        "players": players,
    }
    return {
        **content,
        "observed_at": observed.isoformat(),
        "content_hash": _sha256(content),
        "capture_hash": _sha256({**content, "observed_at": observed.isoformat()}),
    }


def validate_player_identity_index(index: dict[str, Any]) -> None:
    if index.get("version") != IDENTITY_INDEX_VERSION or index.get("role_policy_version") != ROLE_POLICY_VERSION:
        raise ValueError("trade player identity evidence version is unsupported")
    observed_at = _utc_datetime(index.get("observed_at"), "identity observed_at")
    content = {key: value for key, value in index.items() if key not in {"observed_at", "content_hash", "capture_hash"}}
    if index.get("content_hash") != _sha256(content):
        raise ValueError("trade player identity content hash is invalid")
    if index.get("capture_hash") != _sha256({**content, "observed_at": observed_at.isoformat()}):
        raise ValueError("trade player identity capture hash is invalid")


def scoring_entity_evidence(row: dict[str, Any]) -> dict[str, Any]:
    positions = _position_tokens(row)
    unknown = sorted(token for token in positions if token not in HITTER_TOKENS | PITCHER_TOKENS)
    eligibility_roles = set()
    if not unknown:
        if positions & HITTER_TOKENS:
            eligibility_roles.add("hitting")
        if positions & PITCHER_TOKENS:
            eligibility_roles.add("pitching")
    slot = str(row.get("slot") or "").strip().upper()
    slot_source = str(row.get("slot_source") or "").strip()
    trusted_slot = bool(
        slot
        and slot not in GENERIC_SLOT_TOKENS
        and slot_source.casefold() not in sandlot_data_quality.UNTRUSTED_SLOT_SOURCES
    )
    slot_role = "hitting" if slot in HITTER_TOKENS else "pitching" if slot in PITCHER_TOKENS else None
    roles: list[str] = []
    status = "ambiguous"
    reason = "unknown_position_token" if unknown else "eligible_positions_missing"
    if trusted_slot and slot_role and not unknown:
        if eligibility_roles and slot_role not in eligibility_roles:
            status, reason = "conflict", "assigned_slot_eligibility_conflict"
        else:
            roles, status, reason = [slot_role], "resolved", "trusted_assigned_slot"
    elif len(eligibility_roles) == 1:
        roles, status, reason = sorted(eligibility_roles), "resolved", "unique_eligible_role"
    elif len(eligibility_roles) > 1:
        # Preserve every candidate entity, but do not declare a scoreable role
        # until Fantrax's exact two-way inclusion semantics are archived.
        roles, status, reason = sorted(eligibility_roles), "ambiguous", "multiple_eligible_roles"
    player_id = str(row.get("id") or "").strip()
    return {
        "status": status,
        "reason": reason,
        "version": ROLE_POLICY_VERSION,
        "assigned_slot": slot or None,
        "slot_source": slot_source or None,
        "eligible_positions": sorted(positions),
        "unknown_positions": unknown,
        "scoring_entities": [
            {"fantrax_scorer_id": player_id, "scoring_role": role}
            for role in roles
        ],
    }


def build_trade_outcome_contract(
    *,
    league_id: str,
    team_id: str,
    snapshot_id: int,
    snapshot_taken_at: datetime | str,
    generated_at: datetime | str,
    give_ids: list[str],
    get_ids: list[str],
    origin: dict[str, Any],
    calendar: dict[str, Any] | None,
    identity_index: dict[str, Any] | None,
) -> dict[str, Any]:
    """Freeze one reproducible future trade horizon, or bounded ineligibility."""
    cutoff = _utc_datetime(generated_at, "feature cutoff")
    snapshot_at = _utc_datetime(snapshot_taken_at, "snapshot taken_at")
    if snapshot_at > cutoff:
        raise ValueError("trade snapshot evidence is after the assessment cutoff")
    blocking: list[dict[str, Any]] = []
    target = None
    calendar_binding = None
    if not isinstance(calendar, dict):
        blocking.append({"code": "period_calendar_missing"})
    else:
        try:
            validate_period_calendar(calendar)
            calendar_at = _utc_datetime(calendar.get("captured_at"), "calendar captured_at")
            if calendar_at > snapshot_at or calendar_at > cutoff:
                raise ValueError("calendar observation is after decision evidence")
            calendar_binding = {
                "version": calendar.get("version"), "status": calendar.get("status"),
                "season": calendar.get("season"), "captured_at": calendar_at.isoformat(),
                "source": calendar.get("source"), "content_hash": calendar.get("content_hash"),
                "capture_hash": calendar.get("capture_hash"), "regular_season_only": True,
            }
            target, target_reason = _select_target_period(calendar, cutoff)
            if target_reason:
                blocking.append({"code": target_reason})
        except ValueError:
            blocking.append({"code": "period_calendar_invalid"})

    identities: dict[str, dict[str, Any]] = {}
    identity_binding = None
    identity_season_mismatch = False
    if not isinstance(identity_index, dict):
        blocking.append({"code": "player_identity_index_missing"})
    else:
        try:
            validate_player_identity_index(identity_index)
            identity_at = _utc_datetime(identity_index.get("observed_at"), "identity observed_at")
            if identity_at > snapshot_at or identity_at > cutoff:
                raise ValueError("identity observation is after decision evidence")
            identity_binding = {
                "version": identity_index.get("version"), "observed_at": identity_at.isoformat(),
                "content_hash": identity_index.get("content_hash"), "capture_hash": identity_index.get("capture_hash"),
                "role_policy_version": identity_index.get("role_policy_version"),
                "season": identity_index.get("season"),
            }
            calendar_season = (calendar_binding or {}).get("season")
            identity_season = identity_index.get("season")
            season_matches = (
                None
                if calendar_season is None or identity_season is None
                else int(calendar_season) == int(identity_season)
            )
            identity_season_mismatch = season_matches is False
            identity_binding["calendar_season_matches"] = season_matches
            identities = {str(item.get("fantrax_id")): item for item in identity_index.get("players") or [] if isinstance(item, dict)}
        except ValueError:
            blocking.append({"code": "player_identity_index_invalid"})

    assets = []
    for side, ids in (("give", give_ids), ("get", get_ids)):
        for player_id in sorted(ids):
            frozen = identities.get(player_id)
            if not frozen:
                blocking.append({"code": "fantrax_scoring_identity_missing", "fantrax_id": player_id})
                assets.append({"side": side, "fantrax_id": player_id, "mlb_identity": None, "scoring_role": None})
                continue
            role = frozen.get("scoring_role") if isinstance(frozen.get("scoring_role"), dict) else {}
            if role.get("status") != "resolved" or not role.get("scoring_entities"):
                blocking.append({"code": "fantrax_scoring_role_ambiguous", "fantrax_id": player_id})
            mlb_identity = frozen.get("mlb_identity")
            if identity_season_mismatch and isinstance(mlb_identity, dict):
                mlb_identity = {**mlb_identity, "status": "season_mismatch", "mlb_id": None}
            assets.append({
                "side": side,
                "fantrax_id": player_id,
                "mlb_identity": mlb_identity,
                "scoring_role": role,
            })

    rules = scoring_rules_evidence(league_id=league_id, season=(target or {}).get("season"))
    if rules["status"] != "verified":
        blocking.append({"code": "scoring_rules_unverified"})
    blocking = _unique_blocking(blocking)
    cluster = offer_cluster_key(
        league_id=league_id, team_id=team_id, origin=origin,
        give_ids=give_ids, get_ids=get_ids, generated_at=cutoff,
    )
    return {
        "version": OUTCOME_CONTRACT_VERSION,
        "eligible": not blocking,
        "blocking_reasons": blocking,
        "selection_rule": SELECTION_RULE,
        "feature_cutoff": {
            "feature_cutoff_at": cutoff.isoformat(), "assessment_available_at": cutoff.isoformat(),
            "snapshot_id": int(snapshot_id), "snapshot_taken_at": snapshot_at.isoformat(),
        },
        "calendar": calendar_binding,
        "identity_index": identity_binding,
        "target_period": target,
        "offer_cluster_key": cluster,
        "sampling_rule": "earliest_assessment_per_offer_cluster_and_horizon_v1",
        "scoring_basis": rules,
        "assets": sorted(assets, key=lambda item: (item["side"], item["fantrax_id"])),
        "limitations": ([{"code": "mlb_identity_season_mismatch"}] if identity_season_mismatch else []),
        "measurement_scope": "retrospective_static_package_asset_production",
        "target_metric": "static_package_asset_points_delta",
        "metric_unit": "league_fantasy_points",
        "causal_lift_claimed": False,
        "execution_claimed": False,
        "lineup_lift_claimed": False,
        "ros_claimed": False,
        "dynasty_claimed": False,
        "autopilot_eligible": False,
    }


def scoring_rules_evidence(*, league_id: str, season: int | None) -> dict[str, Any]:
    verified = league_id == "lydahdo6mhcvnob7" and season == 2026
    content = {
        "version": SCORING_RULES_VERSION,
        "league_id": league_id,
        "season": season,
        "source": "fantrax_league_rules_summary_manual_verification",
        "hitting": sandlot_scoring.HITTING,
        "pitching": sandlot_scoring.PITCHING,
        "fantrax_points_source_version": SCORING_SOURCE_VERSION,
        "role_policy_version": ROLE_POLICY_VERSION,
    }
    return {**content, "status": "verified" if verified else "unverified", "rules_hash": _sha256(content)}


def offer_cluster_key(
    *, league_id: str, team_id: str, origin: dict[str, Any],
    give_ids: list[str], get_ids: list[str], generated_at: datetime,
) -> str:
    if origin.get("kind") == "incoming_fantrax_offer" and origin.get("fantrax_trade_id"):
        raw = f"{league_id}:{team_id}:incoming:{origin['fantrax_trade_id']}"
    else:
        monday = generated_at.astimezone(ET).date() - timedelta(days=generated_at.astimezone(ET).date().weekday())
        raw = f"{league_id}:{team_id}:manual:{','.join(sorted(give_ids))}:{','.join(sorted(get_ids))}:{monday.isoformat()}"
    return "trade-opportunity:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _select_target_period(calendar: dict[str, Any], cutoff: datetime) -> tuple[dict[str, Any] | None, str | None]:
    if calendar.get("status") in {"unavailable", "invalid"}:
        return None, "period_calendar_unavailable"
    for period in calendar.get("periods") or []:
        start = date.fromisoformat(str(period.get("start")))
        end = date.fromisoformat(str(period.get("end")))
        close = datetime.combine(end + timedelta(days=1), time.min, tzinfo=ET).astimezone(timezone.utc)
        if close <= cutoff:
            continue
        if period.get("status") != "exact" or not period.get("first_scoring_event_at"):
            return None, "next_period_first_scoring_event_missing"
        first = _utc_datetime(period["first_scoring_event_at"], "first scoring event")
        if not (datetime.combine(start, time.min, tzinfo=ET).astimezone(timezone.utc) <= first < close):
            return None, "next_period_first_scoring_event_invalid"
        if first <= cutoff:
            continue
        return {
            "period_number": period.get("period_number"), "period_name": period.get("period_name"),
            "season": period.get("season"), "regular_season": True,
            "start": period.get("start"), "end": period.get("end"),
            "first_scoring_event_at": first.isoformat(),
            "deadline_source": CALENDAR_SOURCE,
            "calendar_content_hash": calendar.get("content_hash"),
            "first_game": period.get("first_game"),
            "candidate_game_count": period.get("candidate_game_count"),
            "candidate_games_hash": period.get("candidate_games_hash"),
            "candidate_games": period.get("candidate_games") or [],
            "period_close_at": close.isoformat(),
            "maturity_at": (close + timedelta(hours=24)).isoformat(),
            "correction_grace_hours": 24,
        }, None
    return None, "no_complete_regular_season_period"


def _normalize_periods(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for raw in periods:
        try:
            start = date.fromisoformat(str(raw.get("start")))
            end = date.fromisoformat(str(raw.get("end")))
        except Exception:
            normalized.append({
                "period_number": str(raw.get("period_number") or ""), "period_name": str(raw.get("period_name") or "") or None,
                "start": str(raw.get("start") or ""), "end": str(raw.get("end") or ""), "season": None,
                "regular_season": raw.get("regular_season") is True, "malformed": True,
            })
            continue
        normalized.append({
            "period_number": _required_text(raw.get("period_number"), "period number"),
            "period_name": str(raw.get("period_name") or "").strip() or None,
            "start": start.isoformat(), "end": end.isoformat(), "season": start.year,
            "regular_season": raw.get("regular_season") is True,
            "malformed": end < start,
        })
    return sorted(normalized, key=lambda item: (item["start"], item["end"], item["period_number"]))


def _period_structure_reasons(periods: list[dict[str, Any]]) -> list[str]:
    reasons = []
    if not periods:
        reasons.append("fantrax_periods_missing")
    if any(item.get("malformed") for item in periods):
        reasons.append("period_boundary_invalid")
    if any(item.get("regular_season") is not True for item in periods):
        reasons.append("non_regular_season_period_present")
    numbers = [item.get("period_number") for item in periods]
    if len(numbers) != len(set(numbers)):
        reasons.append("duplicate_period_number")
    ranges = [(item.get("start"), item.get("end")) for item in periods]
    if len(ranges) != len(set(ranges)):
        reasons.append("duplicate_period_range")
    for previous, current in zip(periods, periods[1:]):
        if previous.get("end") >= current.get("start"):
            reasons.append("overlapping_periods")
            break
    seasons = {item.get("season") for item in periods}
    if None in seasons or len(seasons) != 1:
        reasons.append("mixed_or_missing_season")
    return sorted(set(reasons))


def _normalize_schedule_games(payload: dict[str, Any]) -> list[dict[str, Any]]:
    games = []
    for day in payload.get("dates") or []:
        for raw in day.get("games") or []:
            value = raw.get("gameDate")
            state = str(((raw.get("status") or {}).get("detailedState") or "")).strip()
            if not value or state.casefold() in EXCLUDED_GAME_STATES:
                continue
            try:
                game_at = _utc_datetime(value, "MLB gameDate")
            except ValueError:
                continue
            games.append({
                "game_pk": int(raw["gamePk"]) if raw.get("gamePk") is not None else None,
                "game_at": game_at.isoformat(), "state": state or None,
            })
    return sorted(games, key=lambda item: (item["game_at"], item["game_pk"] or 0))


def _position_tokens(row: dict[str, Any]) -> set[str]:
    raw = []
    raw.extend(re.split(r"[,/]", str(row.get("positions") or "")))
    for value in row.get("all_positions") or []:
        raw.extend(re.split(r"[,/]", str(value or "")))
    return {token.strip().upper() for token in raw if token.strip()}


def _unique_blocking(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        key = (str(item.get("code")), str(item.get("fantrax_id") or ""))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return sorted(out, key=lambda item: (item.get("code") or "", item.get("fantrax_id") or ""))


def _utc_datetime(value: datetime | str | Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception as exc:
            raise ValueError(f"{label} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
