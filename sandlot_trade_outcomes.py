"""Immutable arbitrary-player period evidence and static trade package scoring.

The label produced here is deliberately narrow: frozen give/get assets' exact
Fantrax points in one predeclared future period.  It is not execution proof,
lineup lift, replacement-level value, rest-of-season value, or dynasty value.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import sandlot_trade_evidence


TRADE_PLAYER_PERIOD_EVIDENCE_VERSION = "fantrax_player_period_fpts_v1"
TRADE_PLAYER_PERIOD_QUERY_VERSION = "fantrax_get_player_stats_targeted_period_v1"
TRADE_STATIC_PACKAGE_SCORING_VERSION = "trade_static_package_asset_points_v1"
TRADE_ASSESSMENT_BUILDER_VERSION = "trade_assessment_v4"
TRADE_MISSING_EVIDENCE_GRACE_DAYS = 8
ROLE_FILTERS = {"hitting": "BASEBALL_HITTING", "pitching": "BASEBALL_PITCHING"}


def receipt_requirements(
    receipt: dict[str, Any], *, as_of: datetime | str,
) -> list[dict[str, Any]]:
    """Return exact mature source entities for one eligible V4 receipt."""
    if receipt.get("builder_version") != TRADE_ASSESSMENT_BUILDER_VERSION:
        return []
    if receipt.get("action_type") != "trade_assessment":
        return []
    recommendation = receipt.get("recommendation")
    if not isinstance(recommendation, dict):
        raise ValueError("trade receipt recommendation is missing")
    contract = recommendation.get("outcome_contract")
    if not isinstance(contract, dict):
        raise ValueError("trade outcome contract is missing")
    if contract.get("eligible") is not True:
        return []
    if contract.get("version") != sandlot_trade_evidence.OUTCOME_CONTRACT_VERSION:
        raise ValueError("trade outcome contract version is unsupported")
    fixed_false = (
        "causal_lift_claimed", "execution_claimed", "lineup_lift_claimed",
        "ros_claimed", "dynasty_claimed", "autopilot_eligible",
    )
    if any(contract.get(key) is not False for key in fixed_false):
        raise ValueError("trade outcome contract contains an unsupported claim")
    if contract.get("target_metric") != "static_package_asset_points_delta":
        raise ValueError("trade outcome target metric is unsupported")
    if contract.get("metric_unit") != "league_fantasy_points":
        raise ValueError("trade outcome metric unit is unsupported")

    target = contract.get("target_period")
    if not isinstance(target, dict):
        raise ValueError("trade outcome target period is missing")
    maturity_at = _utc_datetime(target.get("maturity_at"), "trade target maturity_at")
    if _utc_datetime(as_of, "trade evidence as_of") < maturity_at:
        return []
    period_number = _required_text(target.get("period_number"), "trade target period number")
    period_start = _iso_date_text(target.get("start"), "trade target period start")
    period_end = _iso_date_text(target.get("end"), "trade target period end")
    season = int(target.get("season") or period_start[:4])
    if period_end < period_start:
        raise ValueError("trade target period is invalid")

    scoring = contract.get("scoring_basis")
    if not isinstance(scoring, dict) or scoring.get("status") != "verified":
        raise ValueError("trade outcome scoring basis is unverified")
    if scoring.get("fantrax_points_source_version") != sandlot_trade_evidence.SCORING_SOURCE_VERSION:
        raise ValueError("trade outcome points source is unsupported")
    rules_hash = _sha256_text(scoring.get("rules_hash"), "trade scoring rules hash")

    offer = recommendation.get("offer") if isinstance(recommendation.get("offer"), dict) else {}
    names: dict[tuple[str, str], str] = {}
    for side, key in (("give", "give"), ("get", "get")):
        rows = offer.get(key)
        if not isinstance(rows, list):
            raise ValueError("trade receipt offer side is missing")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("trade receipt offer player is invalid")
            names[(side, _required_text(row.get("player_id"), "trade offer player id"))] = _required_text(
                row.get("player_name"), "trade offer player name"
            )

    requirements: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    assets = contract.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("trade outcome assets are missing")
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("trade outcome asset is invalid")
        side = _required_text(asset.get("side"), "trade asset side")
        if side not in {"give", "get"}:
            raise ValueError("trade asset side is invalid")
        asset_id = _required_text(asset.get("fantrax_id"), "trade asset Fantrax id")
        player_name = names.get((side, asset_id))
        if not player_name:
            raise ValueError("trade outcome asset does not match frozen offer")
        role = asset.get("scoring_role")
        if (
            not isinstance(role, dict)
            or role.get("status") != "resolved"
            or role.get("version") != sandlot_trade_evidence.ROLE_POLICY_VERSION
        ):
            raise ValueError("trade outcome asset role is unresolved")
        entities = role.get("scoring_entities")
        if not isinstance(entities, list) or len(entities) != 1:
            raise ValueError("trade outcome asset must freeze exactly one supported scoring entity")
        for entity in entities:
            if not isinstance(entity, dict):
                raise ValueError("trade outcome scoring entity is invalid")
            scorer_id = _required_text(entity.get("fantrax_scorer_id"), "trade scorer id")
            scoring_role = _required_text(entity.get("scoring_role"), "trade scoring role")
            if scorer_id != asset_id or scoring_role not in ROLE_FILTERS:
                raise ValueError("trade outcome scoring entity contradicts the frozen asset")
            identity = (scorer_id, scoring_role)
            if identity in seen:
                raise ValueError("trade outcome contract repeats a scoring entity")
            seen.add(identity)
            requirements.append({
                "evidence_version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
                "league_id": _required_text(receipt.get("league_id"), "trade receipt league id"),
                "season": season,
                "period_number": period_number,
                "period_start": period_start,
                "period_end": period_end,
                "period_close_at": _utc_datetime(target.get("period_close_at"), "period close").isoformat(),
                "maturity_at": maturity_at.isoformat(),
                "fantrax_scorer_id": scorer_id,
                "scoring_role": scoring_role,
                "role_filter": ROLE_FILTERS[scoring_role],
                "player_name": player_name,
                "rules_hash": rules_hash,
                "side": side,
                "offer_cluster_key": _cluster_key(contract.get("offer_cluster_key")),
            })
    return sorted(requirements, key=requirement_key)


def dedupe_requirements(receipts: list[dict[str, Any]], *, as_of: datetime | str) -> list[dict[str, Any]]:
    """Deduplicate shared entity-period fetches without selecting on outcomes or intent."""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for receipt in receipts:
        for requirement in receipt_requirements(receipt, as_of=as_of):
            key = requirement_key(requirement)
            prior = unique.get(key)
            comparable = {k: v for k, v in requirement.items() if k not in {"side", "offer_cluster_key"}}
            if prior is not None:
                prior_comparable = {k: v for k, v in prior.items() if k not in {"side", "offer_cluster_key"}}
                if prior_comparable != comparable:
                    raise ValueError("shared trade evidence requirement is contradictory")
                continue
            unique[key] = comparable
    return [unique[key] for key in sorted(unique)]


def requirement_key(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(value.get("league_id") or ""), int(value.get("season") or 0),
        str(value.get("period_number") or ""), str(value.get("period_start") or ""),
        str(value.get("period_end") or ""), str(value.get("fantrax_scorer_id") or ""),
        str(value.get("scoring_role") or ""), str(value.get("evidence_version") or ""),
    )


def build_player_period_evidence(
    *, requirement: dict[str, Any], period_fpts: Any, source_query: dict[str, Any],
    source_response: dict[str, Any], observed_at: datetime | str,
) -> dict[str, Any]:
    """Bind one exact targeted Fantrax response to one frozen scoring entity."""
    if requirement.get("evidence_version") != TRADE_PLAYER_PERIOD_EVIDENCE_VERSION:
        raise ValueError("trade player-period evidence version is unsupported")
    observed = _utc_datetime(observed_at, "trade evidence observed_at")
    maturity = _utc_datetime(requirement.get("maturity_at"), "trade evidence maturity_at")
    if observed < maturity:
        raise ValueError("trade player-period evidence was captured before maturity")
    points = _decimal(period_fpts, "trade player period FPts")
    _validate_source_lineage(
        requirement=requirement,
        period_fpts=points,
        source_query=source_query,
        source_response=source_response,
    )
    content = {
        "evidence_version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
        "league_id": _required_text(requirement.get("league_id"), "trade evidence league id"),
        "season": int(requirement.get("season")),
        "period": {
            "number": _required_text(requirement.get("period_number"), "trade evidence period number"),
            "start": _iso_date_text(requirement.get("period_start"), "trade evidence period start"),
            "end": _iso_date_text(requirement.get("period_end"), "trade evidence period end"),
            "period_close_at": _utc_datetime(requirement.get("period_close_at"), "period close").isoformat(),
            "maturity_at": maturity.isoformat(),
        },
        "entity": {
            "fantrax_scorer_id": _required_text(requirement.get("fantrax_scorer_id"), "trade scorer id"),
            "player_name": _required_text(requirement.get("player_name"), "trade scorer name"),
            "scoring_role": _required_text(requirement.get("scoring_role"), "trade scoring role"),
        },
        "league_fantasy_points": _decimal_text(points),
        "source_status": "explicit_zero" if points == 0 else "observed",
        "scoring_rules_hash": _sha256_text(requirement.get("rules_hash"), "trade scoring rules hash"),
        "source_query": source_query,
        "source_response": source_response,
        "observed_at": observed.isoformat(),
        "measurement_scope": "retrospective_static_package_asset_production",
        "causal_lift_claimed": False,
        "execution_claimed": False,
        "lineup_lift_claimed": False,
        "ros_claimed": False,
        "dynasty_claimed": False,
        "autopilot_eligible": False,
    }
    return {**content, "evidence_hash": player_period_evidence_hash(content)}


def player_period_evidence_hash(evidence: dict[str, Any]) -> str:
    return _sha256({key: value for key, value in evidence.items() if key != "evidence_hash"})


def build_missing_player_period_observation(
    *, requirement: dict[str, Any], source_query: dict[str, Any],
    source_response: dict[str, Any], observed_at: datetime | str,
) -> dict[str, Any]:
    """Record one complete targeted query that did not contain the exact scorer."""
    observed = _utc_datetime(observed_at, "trade missing observation observed_at")
    if observed < _utc_datetime(requirement.get("maturity_at"), "trade evidence maturity_at"):
        raise ValueError("trade missing observation was captured before maturity")
    observation = {
        "observation_type": "exact_scorer_absent",
        "evidence_version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
        "league_id": requirement.get("league_id"), "season": requirement.get("season"),
        "period": {
            "number": requirement.get("period_number"), "start": requirement.get("period_start"),
            "end": requirement.get("period_end"), "maturity_at": requirement.get("maturity_at"),
        },
        "entity": {
            "fantrax_scorer_id": requirement.get("fantrax_scorer_id"),
            "player_name": requirement.get("player_name"),
            "scoring_role": requirement.get("scoring_role"),
        },
        "source_query": source_query, "source_response": source_response,
        "observed_at": observed.isoformat(), "reason": "targeted_exact_scorer_absent",
        "retryable": True,
    }
    validate_missing_player_period_observation({
        **observation, "observation_hash": missing_observation_hash(observation),
    })
    return {**observation, "observation_hash": missing_observation_hash(observation)}


def missing_observation_hash(observation: dict[str, Any]) -> str:
    return _sha256({key: value for key, value in observation.items() if key != "observation_hash"})


def validate_missing_player_period_observation(observation: dict[str, Any]) -> None:
    if not isinstance(observation, dict) or observation.get("observation_type") != "exact_scorer_absent":
        raise ValueError("trade missing observation type is invalid")
    if observation.get("observation_hash") != missing_observation_hash(observation):
        raise ValueError("trade missing observation hash is invalid")
    period = observation.get("period") if isinstance(observation.get("period"), dict) else {}
    entity = observation.get("entity") if isinstance(observation.get("entity"), dict) else {}
    requirement = {
        "league_id": observation.get("league_id"), "season": observation.get("season"),
        "period_number": period.get("number"), "period_start": period.get("start"),
        "period_end": period.get("end"), "maturity_at": period.get("maturity_at"),
        "fantrax_scorer_id": entity.get("fantrax_scorer_id"),
        "player_name": entity.get("player_name"), "scoring_role": entity.get("scoring_role"),
        "role_filter": ROLE_FILTERS.get(entity.get("scoring_role")),
    }
    source_query = observation.get("source_query")
    if (
        not isinstance(source_query, dict)
        or source_query.get("version") != TRADE_PLAYER_PERIOD_QUERY_VERSION
        or source_query.get("method") != "getPlayerStats"
    ):
        raise ValueError("trade missing observation query is invalid")
    query_content = {key: value for key, value in source_query.items() if key != "query_hash"}
    if source_query.get("query_hash") != _sha256(query_content):
        raise ValueError("trade missing observation query hash is invalid")
    request = source_query.get("request_identity") if isinstance(source_query.get("request_identity"), dict) else {}
    expected_request = {
        "league_id": str(requirement.get("league_id")), "period": str(requirement.get("period_number")),
        "period_start": str(requirement.get("period_start")), "period_end": str(requirement.get("period_end")),
        "fantrax_scorer_id": str(requirement.get("fantrax_scorer_id")),
        "scoring_role": str(requirement.get("scoring_role")), "population": "ALL",
        "role_filter": requirement.get("role_filter"), "timeframe_type_code": "BY_PERIOD",
        "time_start_type": "PERIOD_ONLY",
        "search_name": str(requirement.get("player_name")), "page": 1, "page_size": 50,
    }
    if any(request.get(key) != value for key, value in expected_request.items()):
        raise ValueError("trade missing observation request identity is contradictory")
    response = observation.get("source_response")
    if not isinstance(response, dict):
        raise ValueError("trade missing observation response is invalid")
    response_content = {key: value for key, value in response.items() if key != "response_hash"}
    if response.get("response_hash") != _sha256(response_content):
        raise ValueError("trade missing observation response hash is invalid")
    expected_response = {
        "displayed_period": str(requirement.get("period_number")),
        "displayed_period_start": str(requirement.get("period_start")),
        "displayed_period_end": str(requirement.get("period_end")),
        "displayed_season_or_projection": request.get("season_or_projection"),
        "displayed_timeframe_type_code": "BY_PERIOD", "displayed_time_start_type": "PERIOD_ONLY",
        "displayed_population": "ALL", "displayed_role_filter": requirement.get("role_filter"),
        "page_number": 1, "total_pages": 1, "exact_scorer_present": False,
    }
    if any(response.get(key) != value for key, value in expected_response.items()):
        raise ValueError("trade missing observation response identity is contradictory")
    returned_ids = response.get("returned_scorer_ids")
    if not isinstance(returned_ids, list) or str(requirement.get("fantrax_scorer_id")) in returned_ids:
        raise ValueError("trade missing observation contains the exact scorer")
    if int(response.get("total_results") or 0) != len(returned_ids):
        raise ValueError("trade missing observation response coverage is incomplete")
    for key in ("header_hash", "raw_response_hash"):
        _sha256_text(response.get(key), f"trade missing {key}")


def build_static_package_unavailable(
    *, receipt: dict[str, Any], missing_observations: list[dict[str, Any]],
    snapshot: dict[str, Any], snapshot_id: int, as_of: datetime | str,
) -> dict[str, Any]:
    """Terminalize only proven post-grace exact-scorer absence with a newer final period."""
    requirements = receipt_requirements(receipt, as_of=as_of)
    if not requirements:
        raise ValueError("trade receipt is not mature and eligible")
    if (
        str(snapshot.get("league_id") or "") != str(receipt.get("league_id") or "")
        or str(snapshot.get("team_id") or "") != str(receipt.get("team_id") or "")
    ):
        raise ValueError("trade unavailable snapshot does not match the receipt")
    target = receipt["recommendation"]["outcome_contract"]["target_period"]
    if not missing_evidence_terminal_ready(receipt=receipt, snapshot=snapshot, as_of=as_of):
        maturity = _utc_datetime(target.get("maturity_at"), "trade target maturity")
        evaluated = _utc_datetime(as_of, "trade unavailable as_of")
        if evaluated < maturity + timedelta(days=TRADE_MISSING_EVIDENCE_GRACE_DAYS):
            raise ValueError("trade missing evidence finalization grace is still open")
        raise ValueError("a newer authoritative Fantrax period is not available")
    matchup = snapshot.get("matchup")
    latest = matchup["latest_completed"]
    expected = {requirement_key(item): item for item in requirements}
    observed = set()
    for observation in missing_observations:
        validate_missing_player_period_observation(observation)
        if _utc_datetime(observation.get("observed_at"), "missing observation time") < (
            _utc_datetime(target.get("maturity_at"), "trade target maturity")
            + timedelta(days=TRADE_MISSING_EVIDENCE_GRACE_DAYS)
        ):
            raise ValueError("trade terminal absence observation predates finalization grace")
        period = observation["period"]
        entity = observation["entity"]
        key = requirement_key({
            "league_id": observation.get("league_id"), "season": observation.get("season"),
            "period_number": period.get("number"), "period_start": period.get("start"),
            "period_end": period.get("end"), "fantrax_scorer_id": entity.get("fantrax_scorer_id"),
            "scoring_role": entity.get("scoring_role"), "evidence_version": observation.get("evidence_version"),
        })
        frozen = expected.get(key)
        if frozen is None or entity.get("player_name") != frozen.get("player_name"):
            raise ValueError("trade missing observation does not match the frozen player")
        if key in observed:
            raise ValueError("trade missing observations repeat an entity")
        observed.add(key)
    if not observed or not observed.issubset(expected):
        raise ValueError("trade missing observations do not match the frozen package")
    terminal_proof = {
        "snapshot_id": int(snapshot_id),
        "snapshot_taken_at": _utc_datetime(snapshot.get("timestamp"), "snapshot timestamp").isoformat(),
        "latest_completed_period_number": str(latest.get("period_number") or ""),
        "latest_completed_period_end": str(latest.get("end")),
        "latest_completed_matchup_key": _required_text(latest.get("matchup_key"), "latest matchup key"),
        "grace_days": TRADE_MISSING_EVIDENCE_GRACE_DAYS,
    }
    absence_rows = sorted(
        [{"hash": item["observation_hash"], "entity": item["entity"]} for item in missing_observations],
        key=lambda item: (item["entity"]["fantrax_scorer_id"], item["entity"]["scoring_role"]),
    )
    source_hash = _sha256({
        "version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
        "absence_rows": absence_rows, "terminal_proof": terminal_proof,
    })
    contract = receipt["recommendation"]["outcome_contract"]
    evidence = {
        "receipt_id": receipt.get("receipt_id"), "input_hash": receipt.get("input_hash"),
        "league_id": receipt.get("league_id"), "team_id": receipt.get("team_id"),
        "scoring_version": TRADE_STATIC_PACKAGE_SCORING_VERSION,
        "outcome_contract_version": contract.get("version"),
        "offer_cluster_key": contract.get("offer_cluster_key"),
        "target_period": {
            "season": target.get("season"), "period_number": str(target.get("period_number")),
            "start": target.get("start"), "end": target.get("end"), "maturity_at": target.get("maturity_at"),
        },
        "measurement_scope": "retrospective_static_package_asset_production",
        "reason": "scoring_entity_missing_after_grace", "retryable": False,
        "missing_observations": missing_observations, "terminal_proof": terminal_proof,
        "source_evidence": {"version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION, "hash": source_hash},
        "execution_state": "unknown",
        "causal_lift_claimed": False, "execution_claimed": False,
        "lineup_lift_claimed": False, "ros_claimed": False,
        "dynasty_claimed": False, "autopilot_eligible": False,
        "metrics": {},
    }
    evidence = {**evidence, "evidence_hash": static_package_evaluation_hash(evidence)}
    return {
        "scoring_version": TRADE_STATIC_PACKAGE_SCORING_VERSION, "state": "unavailable",
        "source_evidence_version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
        "source_evidence_hash": source_hash, "metrics": {}, "evidence": evidence,
    }


def missing_evidence_terminal_ready(
    *, receipt: dict[str, Any], snapshot: dict[str, Any], as_of: datetime | str,
) -> bool:
    recommendation = receipt.get("recommendation") if isinstance(receipt.get("recommendation"), dict) else {}
    contract = recommendation.get("outcome_contract") if isinstance(recommendation.get("outcome_contract"), dict) else {}
    target = contract.get("target_period") if isinstance(contract.get("target_period"), dict) else {}
    try:
        maturity = _utc_datetime(target.get("maturity_at"), "trade target maturity")
        evaluated = _utc_datetime(as_of, "trade unavailable as_of")
    except ValueError:
        return False
    if evaluated < maturity + timedelta(days=TRADE_MISSING_EVIDENCE_GRACE_DAYS):
        return False
    matchup = snapshot.get("matchup") if isinstance(snapshot.get("matchup"), dict) else {}
    latest = matchup.get("latest_completed") if isinstance(matchup.get("latest_completed"), dict) else {}
    return bool(
        latest.get("source") == "fantrax_schedule"
        and latest.get("score_state") == "live_or_final"
        and latest.get("complete") is True
        and str(latest.get("end") or "") > str(target.get("end") or "")
    )


def validate_static_package_unavailable(
    *, receipt: dict[str, Any], evaluation: dict[str, Any],
) -> None:
    if (
        evaluation.get("scoring_version") != TRADE_STATIC_PACKAGE_SCORING_VERSION
        or evaluation.get("state") != "unavailable"
        or evaluation.get("source_evidence_version") != TRADE_PLAYER_PERIOD_EVIDENCE_VERSION
        or evaluation.get("metrics") != {}
    ):
        raise ValueError("trade unavailable evaluation contract is invalid")
    evidence = evaluation.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("evidence_hash") != static_package_evaluation_hash(evidence):
        raise ValueError("trade unavailable evaluation hash is invalid")
    expected_binding = {
        "receipt_id": receipt.get("receipt_id"), "input_hash": receipt.get("input_hash"),
        "league_id": receipt.get("league_id"), "team_id": receipt.get("team_id"),
    }
    if any(evidence.get(key) != value for key, value in expected_binding.items()):
        raise ValueError("trade unavailable evaluation does not match the receipt")
    contract = receipt.get("recommendation", {}).get("outcome_contract", {})
    target = contract.get("target_period", {})
    expected_target = {
        "season": target.get("season"), "period_number": str(target.get("period_number")),
        "start": target.get("start"), "end": target.get("end"), "maturity_at": target.get("maturity_at"),
    }
    if (
        evidence.get("target_period") != expected_target
        or evidence.get("offer_cluster_key") != contract.get("offer_cluster_key")
        or evidence.get("reason") != "scoring_entity_missing_after_grace"
        or evidence.get("retryable") is not False
        or evidence.get("execution_state") != "unknown"
        or evidence.get("metrics") != {}
    ):
        raise ValueError("trade unavailable evaluation semantics are invalid")
    for key in (
        "causal_lift_claimed", "execution_claimed", "lineup_lift_claimed",
        "ros_claimed", "dynasty_claimed", "autopilot_eligible",
    ):
        if evidence.get(key) is not False:
            raise ValueError("trade unavailable evaluation contains an unsupported claim")
    proof = evidence.get("terminal_proof")
    if not isinstance(proof, dict) or int(proof.get("grace_days") or 0) != TRADE_MISSING_EVIDENCE_GRACE_DAYS:
        raise ValueError("trade unavailable terminal proof is invalid")
    if str(proof.get("latest_completed_period_end") or "") <= str(target.get("end") or ""):
        raise ValueError("trade unavailable proof lacks a newer completed period")
    proof_at = _utc_datetime(proof.get("snapshot_taken_at"), "trade unavailable snapshot time")
    maturity = _utc_datetime(target.get("maturity_at"), "trade target maturity")
    if proof_at < maturity + timedelta(days=TRADE_MISSING_EVIDENCE_GRACE_DAYS):
        raise ValueError("trade unavailable proof predates finalization grace")
    observations = evidence.get("missing_observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("trade unavailable observations are missing")
    requirements = receipt_requirements(receipt, as_of=proof_at)
    expected_keys = {requirement_key(item) for item in requirements}
    observed_keys = set()
    for observation in observations:
        validate_missing_player_period_observation(observation)
        if _utc_datetime(observation.get("observed_at"), "missing observation time") < (
            maturity + timedelta(days=TRADE_MISSING_EVIDENCE_GRACE_DAYS)
        ):
            raise ValueError("trade unavailable observation predates finalization grace")
        period = observation["period"]
        entity = observation["entity"]
        observed_keys.add(requirement_key({
            "league_id": observation.get("league_id"), "season": observation.get("season"),
            "period_number": period.get("number"), "period_start": period.get("start"),
            "period_end": period.get("end"), "fantrax_scorer_id": entity.get("fantrax_scorer_id"),
            "scoring_role": entity.get("scoring_role"), "evidence_version": observation.get("evidence_version"),
        }))
    if len(observed_keys) != len(observations) or not observed_keys.issubset(expected_keys):
        raise ValueError("trade unavailable observations contradict the frozen package")
    absence_rows = sorted(
        [{"hash": item["observation_hash"], "entity": item["entity"]} for item in observations],
        key=lambda item: (item["entity"]["fantrax_scorer_id"], item["entity"]["scoring_role"]),
    )
    source_hash = _sha256({
        "version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
        "absence_rows": absence_rows, "terminal_proof": proof,
    })
    source = evidence.get("source_evidence")
    if (
        not isinstance(source, dict)
        or source != {"version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION, "hash": source_hash}
        or evaluation.get("source_evidence_hash") != source_hash
    ):
        raise ValueError("trade unavailable source evidence is invalid")


def build_static_package_evaluation(
    *, receipt: dict[str, Any], player_period_evidence: list[dict[str, Any]],
    as_of: datetime | str,
) -> dict[str, Any]:
    """Score exact future package production only when every frozen entity exists."""
    requirements = receipt_requirements(receipt, as_of=as_of)
    if not requirements:
        raise ValueError("trade receipt is not mature and eligible for static package scoring")
    expected = {requirement_key(item): item for item in requirements}
    actual: dict[tuple[Any, ...], dict[str, Any]] = {}
    for evidence in player_period_evidence:
        validate_player_period_evidence(evidence)
        period = evidence["period"]
        entity = evidence["entity"]
        key = requirement_key({
            "league_id": evidence.get("league_id"), "season": evidence.get("season"),
            "period_number": period.get("number"), "period_start": period.get("start"),
            "period_end": period.get("end"), "fantrax_scorer_id": entity.get("fantrax_scorer_id"),
            "scoring_role": entity.get("scoring_role"), "evidence_version": evidence.get("evidence_version"),
        })
        if key in actual:
            raise ValueError("trade player-period evidence repeats a scoring entity")
        actual[key] = evidence
    if set(actual) != set(expected):
        raise ValueError("trade player-period evidence does not cover the exact frozen package")

    contributions = []
    side_totals = {"give": Decimal("0"), "get": Decimal("0")}
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    source_rows = []
    for key in sorted(expected):
        requirement = expected[key]
        evidence = actual[key]
        if evidence.get("scoring_rules_hash") != requirement["rules_hash"]:
            raise ValueError("trade player-period scoring rules do not match the receipt")
        if _utc_datetime(evidence.get("observed_at"), "source observed_at") < _utc_datetime(
            requirement.get("maturity_at"), "requirement maturity_at"
        ):
            raise ValueError("trade player-period evidence predates receipt maturity")
        points = _decimal(evidence.get("league_fantasy_points"), "trade player period FPts")
        side = requirement["side"]
        side_totals[side] += points
        asset_key = (side, requirement["fantrax_scorer_id"])
        asset = assets.setdefault(asset_key, {
            "side": side,
            "fantrax_id": requirement["fantrax_scorer_id"],
            "player_name": requirement["player_name"],
            "asset_points": Decimal("0"),
            "entities": [],
        })
        asset["asset_points"] += points
        asset["entities"].append({
            "scoring_role": requirement["scoring_role"],
            "period_fpts": _decimal_number(points),
            "source_evidence_hash": evidence["evidence_hash"],
        })
        source_rows.append({
            "league_id": requirement["league_id"], "season": requirement["season"],
            "period_number": requirement["period_number"],
            "period_start": requirement["period_start"], "period_end": requirement["period_end"],
            "fantrax_scorer_id": requirement["fantrax_scorer_id"],
            "scoring_role": requirement["scoring_role"],
            "evidence_version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
            "hash": evidence["evidence_hash"],
        })
    for asset in assets.values():
        contributions.append({
            **asset,
            "asset_points": _decimal_number(asset["asset_points"]),
            "entities": sorted(asset["entities"], key=lambda item: item["scoring_role"]),
        })
    contributions.sort(key=lambda item: (item["side"], item["fantrax_id"]))
    give_assets = {item["fantrax_id"] for item in contributions if item["side"] == "give"}
    get_assets = {item["fantrax_id"] for item in contributions if item["side"] == "get"}
    delta = side_totals["get"] - side_totals["give"]
    metrics = {
        "give_package_points": _decimal_number(side_totals["give"]),
        "get_package_points": _decimal_number(side_totals["get"]),
        "static_package_asset_points_delta": _decimal_number(delta),
        "give_asset_count": len(give_assets), "get_asset_count": len(get_assets),
        "give_entity_count": sum(1 for item in requirements if item["side"] == "give"),
        "get_entity_count": sum(1 for item in requirements if item["side"] == "get"),
    }
    source_rows.sort(key=lambda item: (
        item["fantrax_scorer_id"], item["scoring_role"], item["hash"],
    ))
    source_hash = source_set_hash(source_rows)
    contract = receipt["recommendation"]["outcome_contract"]
    target = contract["target_period"]
    evidence = {
        "receipt_id": _required_text(receipt.get("receipt_id"), "trade receipt id"),
        "input_hash": _sha256_text(receipt.get("input_hash"), "trade receipt input hash"),
        "league_id": _required_text(receipt.get("league_id"), "trade receipt league id"),
        "team_id": _required_text(receipt.get("team_id"), "trade receipt team id"),
        "scoring_version": TRADE_STATIC_PACKAGE_SCORING_VERSION,
        "outcome_contract_version": contract["version"],
        "offer_cluster_key": contract["offer_cluster_key"],
        "target_period": {
            "season": target.get("season"), "period_number": str(target.get("period_number")),
            "start": target.get("start"), "end": target.get("end"),
            "maturity_at": target.get("maturity_at"),
        },
        "execution_state": "unknown",
        "package_shape": f"give_{len(give_assets)}_get_{len(get_assets)}",
        "contributions": contributions,
        "source_evidence": {
            "version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
            "hash": source_hash,
            "rows": source_rows,
        },
        "measurement_scope": "retrospective_static_package_asset_production",
        "limitations": {
            "replacement_level_included": False,
            "lineup_usage_included": False,
            "roster_slot_value_included": False,
            "execution_verified": False,
        },
        "causal_lift_claimed": False,
        "execution_claimed": False,
        "lineup_lift_claimed": False,
        "ros_claimed": False,
        "dynasty_claimed": False,
        "autopilot_eligible": False,
        "metrics": metrics,
    }
    evidence = {**evidence, "evidence_hash": static_package_evaluation_hash(evidence)}
    return {
        "scoring_version": TRADE_STATIC_PACKAGE_SCORING_VERSION,
        "state": "scored",
        "source_evidence_version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION,
        "source_evidence_hash": source_hash,
        "metrics": metrics,
        "evidence": evidence,
    }


def static_package_evaluation_hash(evidence: dict[str, Any]) -> str:
    return _sha256({key: value for key, value in evidence.items() if key != "evidence_hash"})


def source_set_hash(rows: list[dict[str, Any]]) -> str:
    return _sha256({"version": TRADE_PLAYER_PERIOD_EVIDENCE_VERSION, "rows": rows})


def validate_player_period_evidence(evidence: dict[str, Any]) -> None:
    if not isinstance(evidence, dict) or evidence.get("evidence_version") != TRADE_PLAYER_PERIOD_EVIDENCE_VERSION:
        raise ValueError("trade player-period evidence version is unsupported")
    if evidence.get("evidence_hash") != player_period_evidence_hash(evidence):
        raise ValueError("trade player-period evidence hash is invalid")
    if evidence.get("source_status") not in {"observed", "explicit_zero"}:
        raise ValueError("trade player-period source status is invalid")
    points = _decimal(evidence.get("league_fantasy_points"), "trade player period FPts")
    if (points == 0) != (evidence.get("source_status") == "explicit_zero"):
        raise ValueError("trade player-period explicit-zero state is contradictory")
    for key in (
        "causal_lift_claimed", "execution_claimed", "lineup_lift_claimed",
        "ros_claimed", "dynasty_claimed", "autopilot_eligible",
    ):
        if evidence.get(key) is not False:
            raise ValueError("trade player-period evidence contains an unsupported claim")
    period = evidence.get("period") if isinstance(evidence.get("period"), dict) else {}
    entity = evidence.get("entity") if isinstance(evidence.get("entity"), dict) else {}
    requirement = {
        "league_id": evidence.get("league_id"), "season": evidence.get("season"),
        "period_number": period.get("number"), "period_start": period.get("start"),
        "period_end": period.get("end"), "period_close_at": period.get("period_close_at"),
        "maturity_at": period.get("maturity_at"),
        "fantrax_scorer_id": entity.get("fantrax_scorer_id"),
        "player_name": entity.get("player_name"), "scoring_role": entity.get("scoring_role"),
        "role_filter": ROLE_FILTERS.get(entity.get("scoring_role")),
        "rules_hash": evidence.get("scoring_rules_hash"),
    }
    _validate_source_lineage(
        requirement=requirement,
        period_fpts=points,
        source_query=evidence.get("source_query"),
        source_response=evidence.get("source_response"),
    )


def _validate_source_lineage(
    *, requirement: dict[str, Any], period_fpts: Decimal,
    source_query: Any, source_response: Any,
) -> None:
    if (
        not isinstance(source_query, dict)
        or source_query.get("version") != TRADE_PLAYER_PERIOD_QUERY_VERSION
        or source_query.get("method") != "getPlayerStats"
    ):
        raise ValueError("trade player-period source query version is unsupported")
    query_content = {key: value for key, value in source_query.items() if key != "query_hash"}
    if source_query.get("query_hash") != _sha256(query_content):
        raise ValueError("trade player-period source query hash is invalid")
    request = source_query.get("request_identity")
    if not isinstance(request, dict):
        raise ValueError("trade player-period request identity is missing")
    expected_request = {
        "league_id": str(requirement.get("league_id")),
        "period": str(requirement.get("period_number")),
        "period_start": str(requirement.get("period_start")),
        "period_end": str(requirement.get("period_end")),
        "fantrax_scorer_id": str(requirement.get("fantrax_scorer_id")),
        "scoring_role": str(requirement.get("scoring_role")),
        "population": "ALL",
        "role_filter": requirement.get("role_filter"),
        "timeframe_type_code": "BY_PERIOD",
        "time_start_type": "PERIOD_ONLY",
        "page": 1,
        "page_size": 50,
    }
    if any(request.get(key) != value for key, value in expected_request.items()):
        raise ValueError("trade player-period request identity contradicts the evidence")
    _required_text(request.get("season_or_projection"), "trade period season selection")
    _required_text(request.get("search_name"), "trade period search name")

    if not isinstance(source_response, dict):
        raise ValueError("trade player-period source response is missing")
    response_content = {key: value for key, value in source_response.items() if key != "response_hash"}
    if source_response.get("response_hash") != _sha256(response_content):
        raise ValueError("trade player-period source response hash is invalid")
    expected_response = {
        "displayed_period": str(requirement.get("period_number")),
        "displayed_period_start": str(requirement.get("period_start")),
        "displayed_period_end": str(requirement.get("period_end")),
        "displayed_timeframe_type_code": "BY_PERIOD",
        "displayed_time_start_type": "PERIOD_ONLY",
        "displayed_population": "ALL",
        "displayed_role_filter": requirement.get("role_filter"),
        "matched_scorer_id": str(requirement.get("fantrax_scorer_id")),
        "matched_scorer_name": str(requirement.get("player_name")),
        "matched_scoring_role": str(requirement.get("scoring_role")),
        "matched_period_fpts": _decimal_text(period_fpts),
        "page_number": 1,
        "total_pages": 1,
    }
    if any(source_response.get(key) != value for key, value in expected_response.items()):
        raise ValueError("trade player-period source response contradicts the evidence")
    if source_response.get("displayed_season_or_projection") != request.get("season_or_projection"):
        raise ValueError("trade player-period source season selection is contradictory")
    if int(source_response.get("total_results") or -1) < 1:
        raise ValueError("trade player-period source response lacks the matched scorer")
    _required_text(source_response.get("period_fpts_source"), "trade period FPts source")
    for key in ("header_hash", "matched_source_slice_hash", "raw_matched_row_hash", "raw_response_hash"):
        _sha256_text(source_response.get(key), f"trade period {key}")
    source_slice = source_response.get("matched_source_slice")
    expected_slice = {
        "fantrax_scorer_id": expected_response["matched_scorer_id"],
        "player_name": expected_response["matched_scorer_name"],
        "scoring_role": expected_response["matched_scoring_role"],
        "period_fpts": expected_response["matched_period_fpts"],
        "period_fpts_source": source_response.get("period_fpts_source"),
    }
    if source_slice != expected_slice or source_response.get("matched_source_slice_hash") != _sha256(source_slice):
        raise ValueError("trade player-period matched source slice is invalid")


def _utc_datetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso_date_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc


def _decimal(value: Any, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{label} must be finite")
    return number


def _decimal_text(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _decimal_number(value: Decimal) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("trade package metric must be finite")
    return round(number, 4)


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _sha256_text(value: Any, label: str) -> str:
    text = _required_text(value, label)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return text


def _cluster_key(value: Any) -> str:
    text = _required_text(value, "trade offer cluster key")
    prefix = "trade-opportunity:"
    if not text.startswith(prefix):
        raise ValueError("trade offer cluster key is invalid")
    _sha256_text(text[len(prefix):], "trade offer cluster hash")
    return text


def _sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
