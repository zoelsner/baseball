"""Leakage-safe offline evaluation for Sandlot recommendation quality.

This module deliberately does not run in the refresh or request path. It turns
immutable decision receipts plus later counterfactual evaluations into a
versioned dataset and compares a naive projection baseline with a rolling,
interpretable affine calibrator trained only on labels available at prediction
time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import sandlot_db
import sandlot_receipts


DATASET_VERSION = "lineup_decision_features_v1"
BASELINE_VERSION = "projected_gain_identity_v1"
CANDIDATE_VERSION = "rolling_affine_gain_v1"
MIN_TRAIN_SAMPLES = 8
MIN_EVALUATION_SAMPLES = 4
MIN_LABEL_COVERAGE = 0.75
ET = ZoneInfo("America/New_York")


def build_lineup_dataset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create a stable feature/label split from trusted immutable rows."""
    dataset = []
    for raw in rows:
        builder_version = str(raw.get("builder_version") or "").strip()
        if builder_version == "monday_lineup_v1":
            continue
        if builder_version != sandlot_receipts.MONDAY_LINEUP_BUILDER_VERSION:
            raise ValueError("decision-science row has an unsupported receipt builder version")
        if raw.get("state") != "scored":
            continue
        if raw.get("scoring_version") != sandlot_receipts.COUNTERFACTUAL_LINEUP_SCORING_VERSION:
            raise ValueError("decision-science row has an unsupported scoring version")
        recommendation = raw.get("recommendation")
        metrics = raw.get("metrics")
        if not isinstance(recommendation, dict) or not isinstance(metrics, dict):
            raise ValueError("decision-science row is missing immutable recommendation or metrics")
        generated_at = _utc_datetime(raw.get("generated_at"), "generated_at")
        evaluated_at = _utc_datetime(raw.get("evaluated_at"), "evaluated_at")
        if evaluated_at <= generated_at:
            raise ValueError("outcome label must become available after decision features")
        period_start = _iso_date(raw.get("period_start"), "period_start")
        period_end = _iso_date(raw.get("period_end"), "period_end")
        horizon_start = datetime.combine(period_start, time.min, tzinfo=ET).astimezone(timezone.utc)
        horizon_close = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=ET).astimezone(timezone.utc)
        period = recommendation.get("period")
        if not isinstance(period, dict) or period.get("deadline_source") != "mlb_schedule_first_game_v1":
            raise ValueError("decision-science receipt lacks a trusted first-game deadline")
        decision_deadline_at = _utc_datetime(period.get("decision_deadline_at"), "decision_deadline_at")
        if period_end < period_start or not (horizon_start <= decision_deadline_at < horizon_close):
            raise ValueError("decision deadline is outside the target period")
        if generated_at >= decision_deadline_at:
            raise ValueError("decision features must be frozen before the first scoring deadline")
        if evaluated_at < horizon_close:
            raise ValueError("outcome label must become available after the target period closes")
        snapshot = recommendation.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("decision-science receipt snapshot lineage is missing")
        observed_at = _utc_datetime(snapshot.get("taken_at"), "snapshot.taken_at")
        if observed_at > generated_at or observed_at >= decision_deadline_at:
            raise ValueError("decision feature observation time is after its allowed cutoff")

        projected_gain = _finite(raw.get("projected_gain"), "projected_gain")
        baseline_value = _finite(raw.get("baseline_value"), "baseline_value")
        projected_value = _finite(raw.get("projected_value"), "projected_value")
        target_gain = _finite(metrics.get("counterfactual_gain"), "counterfactual_gain")
        input_hash = _hash(raw.get("input_hash"), "input_hash")
        source_hash = _hash(raw.get("source_evidence_hash"), "source_evidence_hash")
        evaluation_hash = _hash(raw.get("evaluation_evidence_hash"), "evaluation_evidence_hash")
        source_version = str(raw.get("source_evidence_version") or "").strip()
        if source_version != sandlot_receipts.COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION:
            raise ValueError("decision-science source evidence version is unsupported")
        baseline = recommendation.get("baseline_assignment")
        proposed = recommendation.get("proposed_assignment")
        unfilled = recommendation.get("unfilled_slots")
        if not isinstance(baseline, list) or not isinstance(proposed, list) or not isinstance(unfilled, list):
            raise ValueError("decision-science assignment features are malformed")
        baseline_ids = {str(item.get("player_id")) for item in baseline if isinstance(item, dict) and item.get("player_id")}
        proposed_ids = {str(item.get("player_id")) for item in proposed if isinstance(item, dict) and item.get("player_id")}
        sample_id = hashlib.sha256(
            f"{raw.get('receipt_id')}:{input_hash}:{source_hash}:{evaluation_hash}:{DATASET_VERSION}".encode("utf-8")
        ).hexdigest()
        dataset.append({
            "dataset_version": DATASET_VERSION,
            "sample_id": sample_id,
            "feature_cutoff_at": generated_at.isoformat(),
            "feature_observed_at": observed_at.isoformat(),
            "decision_deadline_at": decision_deadline_at.isoformat(),
            "label_available_at": evaluated_at.isoformat(),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "features": {
                "projected_gain": projected_gain,
                "baseline_projected_points": baseline_value,
                "proposed_projected_points": projected_value,
                "assignment_change_count": len(baseline_ids ^ proposed_ids),
                "unfilled_slot_count": len(unfilled),
                "days_to_period_start": (period_start - generated_at.date()).days,
            },
            "label": {"counterfactual_gain": target_gain},
            "lineage": {
                "receipt_input_hash": input_hash,
                "source_evidence_version": source_version,
                "source_evidence_hash": source_hash,
                "evaluation_evidence_hash": evaluation_hash,
            },
        })
    return sorted(dataset, key=lambda row: (row["feature_cutoff_at"], row["sample_id"]))


def coverage_report(
    rows: list[dict[str, Any]], *, as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Describe the full recommendation-period denominator before filtering labels."""
    as_of_time = _utc_datetime(as_of or datetime.now(timezone.utc), "coverage as_of")
    legacy = [row for row in rows if row.get("builder_version") == "monday_lineup_v1"]
    unsupported = [
        row for row in rows
        if row.get("builder_version") not in {"monday_lineup_v1", sandlot_receipts.MONDAY_LINEUP_BUILDER_VERSION}
    ]
    compatible = [
        row for row in rows
        if row.get("builder_version") == sandlot_receipts.MONDAY_LINEUP_BUILDER_VERSION
    ]
    eligible = []
    not_yet_due = []
    for row in compatible:
        period_end = _iso_date(row.get("period_end"), "coverage period_end")
        horizon_close = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=ET).astimezone(timezone.utc)
        (eligible if horizon_close <= as_of_time else not_yet_due).append(row)
    total = len(eligible)
    scored = sum(row.get("state") == "scored" for row in eligible)
    unavailable = sum(row.get("state") == "unavailable" for row in eligible)
    pending = total - scored - unavailable
    reasons: dict[str, int] = {}
    for row in eligible:
        if row.get("state") == "scored":
            continue
        evidence = row.get("evaluation_evidence")
        capability = row.get("counterfactual_capability")
        if (evidence or {}).get("detail"):
            reason = str(evidence["detail"])
        elif isinstance(capability, dict) and capability.get("eligible") is False:
            reason = "counterfactual_ineligible:" + str(capability.get("reason") or "unspecified")
        elif isinstance(capability, dict) and capability.get("eligible") is True:
            reason = "eligible_evaluation_missing"
        elif row.get("state") == "unavailable":
            reason = "unavailable_unspecified"
        else:
            reason = "completed_period_evidence_missing"
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "total_periods": total,
        "all_observed_periods": len(rows),
        "compatible_v2_periods": len(compatible),
        "legacy_deadline_unavailable_periods": len(legacy),
        "unsupported_builder_periods": len(unsupported),
        "not_yet_due_periods": len(not_yet_due),
        "scored_periods": scored,
        "unavailable_periods": unavailable,
        "pending_or_ineligible_periods": pending,
        "label_coverage_rate": round(scored / total, 6) if total else 0.0,
        "minimum_label_coverage": MIN_LABEL_COVERAGE,
        "coverage_ready": bool(total and scored / total >= MIN_LABEL_COVERAGE),
        "unscored_reasons": reasons,
        "as_of": as_of_time.isoformat(),
    }


def evaluation_report(
    dataset: list[dict[str, Any]], *, coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate identity and rolling affine predictions without temporal leakage."""
    _validate_dataset(dataset)
    baseline_pairs = [
        (row["features"]["projected_gain"], row["label"]["counterfactual_gain"])
        for row in dataset
    ]
    rolling = []
    for test in dataset:
        cutoff = _utc_datetime(test["feature_cutoff_at"], "feature_cutoff_at")
        train = [
            row for row in dataset
            if row["sample_id"] != test["sample_id"]
            and _utc_datetime(row["label_available_at"], "label_available_at") <= cutoff
        ]
        training_horizons = len({row["period_end"] for row in train})
        if training_horizons < MIN_TRAIN_SAMPLES:
            continue
        intercept, slope = _fit_affine([
            (row["features"]["projected_gain"], row["label"]["counterfactual_gain"])
            for row in train
        ])
        raw_prediction = test["features"]["projected_gain"]
        rolling.append({
            "sample_id": test["sample_id"],
            "feature_cutoff_at": test["feature_cutoff_at"],
            "period_end": test["period_end"],
            "training_samples": len(train),
            "training_horizons": training_horizons,
            "baseline_prediction": raw_prediction,
            "candidate_prediction": intercept + slope * raw_prediction,
            "actual": test["label"]["counterfactual_gain"],
            "intercept": intercept,
            "slope": slope,
        })
    evaluation_horizons = len({row["period_end"] for row in rolling})
    coverage = coverage or {
        "total_periods": len(dataset), "scored_periods": len(dataset),
        "unavailable_periods": 0, "pending_or_ineligible_periods": 0,
        "label_coverage_rate": 1.0 if dataset else 0.0,
        "minimum_label_coverage": MIN_LABEL_COVERAGE,
        "coverage_ready": bool(dataset), "unscored_reasons": {},
    }
    candidate_ready = (
        evaluation_horizons >= MIN_EVALUATION_SAMPLES
        and coverage.get("coverage_ready") is True
    )
    candidate_metrics = _metrics([(row["candidate_prediction"], row["actual"]) for row in rolling])
    comparable_baseline = _metrics([(row["baseline_prediction"], row["actual"]) for row in rolling])
    beats_baseline = bool(
        candidate_ready and candidate_metrics and comparable_baseline
        and candidate_metrics["mae"] < comparable_baseline["mae"]
    )
    return {
        "dataset_version": DATASET_VERSION,
        "sample_size": len(dataset),
        "sample_state": "ready_for_candidate_evaluation" if candidate_ready else "insufficient_evidence",
        "minimums": {
            "training": MIN_TRAIN_SAMPLES,
            "evaluation": MIN_EVALUATION_SAMPLES,
            "label_coverage": MIN_LABEL_COVERAGE,
        },
        "label_coverage": coverage,
        "coverage": {
            "distinct_periods": len({row["period_end"] for row in dataset}),
            "distinct_seasons": len({row["period_end"][:4] for row in dataset}),
            "rolling_evaluation_periods": evaluation_horizons,
        },
        "baseline": {"model_version": BASELINE_VERSION, "metrics": _metrics(baseline_pairs)},
        "candidate": {
            "model_version": CANDIDATE_VERSION,
            "evaluation_samples": len(rolling),
            "metrics": candidate_metrics,
            "comparable_baseline_metrics": comparable_baseline,
            "beats_baseline": beats_baseline,
            "eligible_for_product_use": False,
            "predictions": rolling,
        },
        "autopilot_eligible": False,
        "training_runtime": "offline_cpu_only",
        "target_semantics": "retrospective_static_lineup_counterfactual_not_causal_lift",
    }


def build_report(*, limit: int | None = None, as_of: datetime | str | None = None) -> dict[str, Any]:
    sandlot_db.init_schema()
    rows = sandlot_db.list_lineup_decision_science_rows(limit=limit)
    report = evaluation_report(
        build_lineup_dataset(rows), coverage=coverage_report(rows, as_of=as_of),
    )
    source_limit = limit if limit is not None else 10000
    report["source_query_limit"] = source_limit
    report["source_query_truncated"] = len(rows) >= source_limit
    return report


def _fit_affine(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    x_mean = sum(x for x, _ in pairs) / len(pairs)
    y_mean = sum(y for _, y in pairs) / len(pairs)
    denominator = sum((x - x_mean) ** 2 for x, _ in pairs)
    if denominator <= 1e-12:
        return y_mean, 0.0
    slope = sum((x - x_mean) * (y - y_mean) for x, y in pairs) / denominator
    return y_mean - slope * x_mean, slope


def _metrics(pairs: list[tuple[float, float]]) -> dict[str, Any] | None:
    if not pairs:
        return None
    errors = [prediction - actual for prediction, actual in pairs]
    return {
        "count": len(pairs),
        "mae": round(sum(abs(error) for error in errors) / len(errors), 6),
        "bias": round(sum(errors) / len(errors), 6),
        "direction_accuracy": round(sum(_direction(p) == _direction(a) for p, a in pairs) / len(pairs), 6),
    }


def _validate_dataset(dataset: list[dict[str, Any]]) -> None:
    seen = set()
    for row in dataset:
        if row.get("dataset_version") != DATASET_VERSION or row.get("sample_id") in seen:
            raise ValueError("decision-science dataset identity is invalid")
        seen.add(row["sample_id"])
        if set(row.get("features") or {}) != {
            "projected_gain", "baseline_projected_points", "proposed_projected_points",
            "assignment_change_count", "unfilled_slot_count", "days_to_period_start",
        }:
            raise ValueError("decision-science feature schema drifted")
        if set(row.get("label") or {}) != {"counterfactual_gain"}:
            raise ValueError("decision-science label schema drifted")
        if set(row.get("lineage") or {}) != {
            "receipt_input_hash", "source_evidence_version", "source_evidence_hash", "evaluation_evidence_hash",
        }:
            raise ValueError("decision-science lineage schema drifted")
        if _utc_datetime(row["label_available_at"], "label_available_at") <= _utc_datetime(row["feature_cutoff_at"], "feature_cutoff_at"):
            raise ValueError("decision-science label leaks across the feature cutoff")
        if _utc_datetime(row["feature_observed_at"], "feature_observed_at") > _utc_datetime(row["feature_cutoff_at"], "feature_cutoff_at"):
            raise ValueError("decision-science observation leaks across the feature cutoff")
        if _utc_datetime(row["feature_cutoff_at"], "feature_cutoff_at") >= _utc_datetime(row["decision_deadline_at"], "decision_deadline_at"):
            raise ValueError("decision-science features cross the first scoring deadline")


def _finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _hash(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ValueError(f"{label} must be a SHA-256 hash")
    return text


def _direction(value: float, *, tolerance: float = 1e-9) -> str:
    if value > tolerance:
        return "positive"
    if value < -tolerance:
        return "negative"
    return "neutral"


def _utc_datetime(value: Any, label: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        value = date.fromisoformat(value)
    if not isinstance(value, date):
        raise ValueError(f"{label} must be a date")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate leakage-safe Sandlot decision-science baselines.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--as-of", default=None, help="Reproducible ISO-8601 coverage cutoff.")
    args = parser.parse_args(argv)
    print(json.dumps(build_report(limit=args.limit, as_of=args.as_of), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
