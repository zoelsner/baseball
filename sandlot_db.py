"""Postgres persistence for Sandlot v1.

V1 stores full Fantrax snapshots as JSONB and derives API responses from the
latest successful row. This keeps the data model easy to change while the app
shape is still moving.
"""

from __future__ import annotations

import math
import os
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(database_url(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
              id BIGSERIAL PRIMARY KEY,
              taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              source TEXT NOT NULL DEFAULT 'manual',
              status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
              league_id TEXT,
              team_id TEXT,
              team_name TEXT,
              duration_ms INTEGER,
              errors JSONB NOT NULL DEFAULT '[]'::jsonb,
              data JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS snapshots_status_taken_at_idx
            ON snapshots (status, taken_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fantrax_sessions (
              id TEXT PRIMARY KEY DEFAULT 'default',
              cookies_json JSONB NOT NULL,
              source TEXT,
              expires_at TIMESTAMPTZ,
              refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_runs (
              id BIGSERIAL PRIMARY KEY,
              started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              finished_at TIMESTAMPTZ,
              source TEXT NOT NULL DEFAULT 'manual',
              status TEXT,
              snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,
              duration_ms INTEGER,
              error TEXT
            )
            """
        )
        # Migrate existing deployments: the FK was originally created without an
        # ON DELETE action, which blocks prune_successful_snapshots once the
        # keep window fills. Rewrite the constraint to ON DELETE SET NULL so the
        # cron audit row survives even when its underlying snapshot is pruned.
        conn.execute("ALTER TABLE refresh_runs DROP CONSTRAINT IF EXISTS refresh_runs_snapshot_id_fkey")
        conn.execute(
            """
            ALTER TABLE refresh_runs
              ADD CONSTRAINT refresh_runs_snapshot_id_fkey
              FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE SET NULL
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
              id BIGSERIAL PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              title TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
              id BIGSERIAL PRIMARY KEY,
              session_id BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
              content TEXT NOT NULL,
              tier INTEGER,
              model TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS chat_messages_session_created_idx
            ON chat_messages (session_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_id_map (
              fantrax_id TEXT PRIMARY KEY,
              mlb_id BIGINT,
              resolved_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_game_logs (
              mlb_id BIGINT NOT NULL,
              group_type TEXT NOT NULL CHECK (group_type IN ('hitting', 'pitching')),
              season INTEGER NOT NULL,
              games JSONB NOT NULL,
              fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (mlb_id, group_type, season)
            )
            """
        )
        conn.execute("ALTER TABLE player_game_logs DROP CONSTRAINT IF EXISTS player_game_logs_pkey")
        conn.execute(
            """
            ALTER TABLE player_game_logs
              ADD CONSTRAINT player_game_logs_pkey PRIMARY KEY (mlb_id, group_type, season)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_media (
              mlb_id BIGINT PRIMARY KEY,
              items JSONB NOT NULL,
              fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_takes (
              player_id TEXT NOT NULL,
              snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
              text TEXT NOT NULL,
              model TEXT NOT NULL,
              generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (player_id, snapshot_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_briefs (
              snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
              brief_type TEXT NOT NULL,
              subject_key TEXT NOT NULL,
              text TEXT NOT NULL,
              model TEXT NOT NULL,
              input_hash TEXT,
              generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (snapshot_id, brief_type, subject_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_logs (
              id BIGSERIAL PRIMARY KEY,
              snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,
              model_version TEXT NOT NULL,
              surface TEXT NOT NULL DEFAULT 'api',
              shown_date DATE NOT NULL DEFAULT CURRENT_DATE,
              matchup_key TEXT NOT NULL,
              period_id TEXT,
              my_team_id TEXT,
              opponent_team_id TEXT,
              predicted_my DOUBLE PRECISION NOT NULL,
              predicted_opp DOUBLE PRECISION NOT NULL,
              predicted_margin DOUBLE PRECISION NOT NULL,
              win_probability DOUBLE PRECISION NOT NULL,
              data_quality JSONB NOT NULL DEFAULT '{}'::jsonb,
              drivers JSONB NOT NULL DEFAULT '{}'::jsonb,
              actual_my DOUBLE PRECISION,
              actual_opp DOUBLE PRECISION,
              actual_winner TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (snapshot_id, model_version, matchup_key)
            )
            """
        )
        conn.execute("ALTER TABLE projection_logs ADD COLUMN IF NOT EXISTS surface TEXT")
        conn.execute("UPDATE projection_logs SET surface = 'api' WHERE surface IS NULL")
        conn.execute("ALTER TABLE projection_logs ALTER COLUMN surface SET DEFAULT 'api'")
        conn.execute("ALTER TABLE projection_logs ALTER COLUMN surface SET NOT NULL")
        conn.execute("ALTER TABLE projection_logs ADD COLUMN IF NOT EXISTS shown_date DATE")
        conn.execute("UPDATE projection_logs SET shown_date = COALESCE(shown_date, created_at::date, CURRENT_DATE) WHERE shown_date IS NULL")
        conn.execute("ALTER TABLE projection_logs ALTER COLUMN shown_date SET DEFAULT CURRENT_DATE")
        conn.execute("ALTER TABLE projection_logs ALTER COLUMN shown_date SET NOT NULL")
        conn.execute("ALTER TABLE projection_logs ADD COLUMN IF NOT EXISTS drivers JSONB NOT NULL DEFAULT '{}'::jsonb")
        # Projection logs are the durable calibration history. Snapshots are a
        # short-lived cache (30 rows by default), so cascading snapshot pruning
        # would otherwise erase the predictions before bias/Brier metrics can
        # accumulate across matchup periods.
        conn.execute("ALTER TABLE projection_logs DROP CONSTRAINT IF EXISTS projection_logs_snapshot_id_fkey")
        conn.execute("ALTER TABLE projection_logs ALTER COLUMN snapshot_id DROP NOT NULL")
        conn.execute(
            """
            ALTER TABLE projection_logs
              ADD CONSTRAINT projection_logs_snapshot_id_fkey
              FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE SET NULL
            """
        )
        conn.execute("ALTER TABLE projection_logs DROP CONSTRAINT IF EXISTS projection_logs_snapshot_id_model_version_matchup_key_key")
        conn.execute(
            """
            DELETE FROM projection_logs stale
            USING projection_logs keep
            WHERE stale.id < keep.id
              AND stale.model_version = keep.model_version
              AND stale.matchup_key = keep.matchup_key
              AND stale.surface = keep.surface
              AND stale.shown_date = keep.shown_date
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS projection_logs_surface_day_key
            ON projection_logs (model_version, matchup_key, surface, shown_date)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_requests (
              request_id TEXT PRIMARY KEY,
              mode TEXT NOT NULL CHECK (mode = 'dry_run'),
              snapshot_id BIGINT NOT NULL,
              proposal_id TEXT NOT NULL,
              input_hash TEXT NOT NULL,
              contract JSONB NOT NULL,
              expected_roster_ids JSONB NOT NULL,
              safety JSONB NOT NULL,
              state TEXT NOT NULL CHECK (state IN (
                'pending', 'claimed', 'preflight_passed', 'preflight_failed',
                'expired', 'cancelled'
              )),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              expires_at TIMESTAMPTZ NOT NULL,
              claimed_at TIMESTAMPTZ,
              lease_expires_at TIMESTAMPTZ,
              completed_at TIMESTAMPTZ,
              claimed_by TEXT,
              lease_token_hash TEXT,
              evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
              failure_reason TEXT
            )
            """
        )
        _init_recommendation_receipts_schema(conn)
        _init_recommendation_outcome_evaluations_schema(conn)
        _init_lineup_period_evidence_schema(conn)
        _init_trade_player_period_evidence_schema(conn)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS execution_requests_proposal_instance_key
            ON execution_requests (mode, snapshot_id, proposal_id, input_hash)
            """
        )
        conn.execute(
            "ALTER TABLE execution_requests ADD COLUMN IF NOT EXISTS safety JSONB NOT NULL DEFAULT '{}'::jsonb"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS execution_requests_claim_queue_idx
            ON execution_requests (state, expires_at, created_at)
            """
        )


def create_refresh_run(source: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO refresh_runs (source) VALUES (%s) RETURNING id",
            (source,),
        ).fetchone()
    return int(row["id"])


def finish_refresh_run(
    run_id: int,
    *,
    status: str,
    snapshot_id: int | None,
    duration_ms: int,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE refresh_runs
            SET finished_at = now(),
                status = %s,
                snapshot_id = %s,
                duration_ms = %s,
                error = %s
            WHERE id = %s
            """,
            (status, snapshot_id, duration_ms, error, run_id),
        )


def insert_snapshot(
    *,
    source: str,
    status: str,
    data: dict[str, Any],
    duration_ms: int,
    errors: list[str] | None = None,
) -> int:
    taken_at = _parse_timestamp(data.get("timestamp")) or datetime.now(timezone.utc)
    errors = errors if errors is not None else list(data.get("errors") or [])
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO snapshots
              (taken_at, source, status, league_id, team_id, team_name, duration_ms, errors, data)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                taken_at,
                source,
                status,
                data.get("league_id"),
                data.get("team_id"),
                data.get("team_name"),
                duration_ms,
                Jsonb(errors),
                Jsonb(data),
            ),
        ).fetchone()
    return int(row["id"])


def ensure_recommendation_receipts_schema() -> None:
    """Create only the ledger schema for standalone recommendation writers."""
    with connect() as conn:
        _init_recommendation_receipts_schema(conn)
        _init_recommendation_outcome_evaluations_schema(conn)


def _init_lineup_period_evidence_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lineup_period_evidence (
          league_id TEXT NOT NULL,
          team_id TEXT NOT NULL,
          period_start DATE NOT NULL,
          period_end DATE NOT NULL,
          evidence_version TEXT NOT NULL,
          period_number TEXT NOT NULL,
          observed_team_total DOUBLE PRECISION NOT NULL,
          evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
          evidence JSONB NOT NULL,
          source_snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,
          captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (league_id, team_id, period_start, period_end, evidence_version),
          CHECK (period_end >= period_start)
        )
        """
    )


def archive_lineup_period_evidence(*, evidence: dict[str, Any], snapshot_id: int) -> tuple[dict[str, Any], bool]:
    """Insert immutable period evidence; identical refresh replay is a no-op."""
    from fantrax_data import LINEUP_PERIOD_EVIDENCE_VERSION, lineup_period_evidence_hash

    if evidence.get("evidence_version") != LINEUP_PERIOD_EVIDENCE_VERSION:
        raise ValueError("unsupported lineup period evidence version")
    evidence_hash = str(evidence.get("evidence_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
        raise ValueError("lineup period evidence hash must be lowercase SHA-256")
    if evidence_hash != lineup_period_evidence_hash(evidence):
        raise ValueError("lineup period evidence hash is invalid")
    period = evidence.get("period") if isinstance(evidence.get("period"), dict) else {}
    required = {
        "league_id": evidence.get("league_id"), "team_id": evidence.get("team_id"),
        "period_start": period.get("start"), "period_end": period.get("end"),
        "period_number": period.get("number"), "observed_team_total": evidence.get("observed_team_total"),
    }
    if any(value in (None, "") for value in required.values()):
        raise ValueError("lineup period evidence identity is incomplete")
    values = (
        evidence.get("league_id"), evidence.get("team_id"), period.get("start"), period.get("end"),
        evidence.get("evidence_version"), period.get("number"), evidence.get("observed_team_total"),
        evidence_hash, Jsonb(evidence), snapshot_id,
    )
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO lineup_period_evidence
               (league_id, team_id, period_start, period_end, evidence_version, period_number,
                observed_team_total, evidence_hash, evidence, source_snapshot_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (league_id, team_id, period_start, period_end, evidence_version) DO NOTHING
               RETURNING *""",
            values,
        ).fetchone()
        if row:
            return dict(row), True
        existing = conn.execute(
            """SELECT * FROM lineup_period_evidence
               WHERE league_id=%s AND team_id=%s AND period_start=%s AND period_end=%s AND evidence_version=%s""",
            values[:5],
        ).fetchone()
        if existing and existing.get("evidence_hash") == evidence_hash and existing.get("evidence") == evidence:
            return dict(existing), False
        raise ValueError("completed lineup period already has different immutable evidence")


def _init_trade_player_period_evidence_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_player_period_evidence (
          league_id TEXT NOT NULL,
          season INTEGER NOT NULL,
          period_number TEXT NOT NULL,
          period_start DATE NOT NULL,
          period_end DATE NOT NULL,
          fantrax_scorer_id TEXT NOT NULL,
          scoring_role TEXT NOT NULL CHECK (scoring_role IN ('hitting', 'pitching')),
          evidence_version TEXT NOT NULL,
          league_fantasy_points NUMERIC NOT NULL,
          source_status TEXT NOT NULL CHECK (source_status IN ('observed', 'explicit_zero')),
          evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
          evidence JSONB NOT NULL,
          source_snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,
          observed_at TIMESTAMPTZ NOT NULL,
          captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (
            league_id, season, period_number, period_start, period_end,
            fantrax_scorer_id, scoring_role, evidence_version
          ),
          CHECK (period_end >= period_start)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS trade_player_period_evidence_period_idx
        ON trade_player_period_evidence
          (league_id, season, period_number, evidence_version, fantrax_scorer_id, scoring_role)
        """
    )


def archive_trade_player_period_evidence(
    *, evidence: dict[str, Any], snapshot_id: int,
) -> tuple[dict[str, Any], bool]:
    """Append one exact immutable arbitrary-player period scoring entity."""
    import sandlot_trade_outcomes

    sandlot_trade_outcomes.validate_player_period_evidence(evidence)
    period = evidence.get("period") if isinstance(evidence.get("period"), dict) else {}
    entity = evidence.get("entity") if isinstance(evidence.get("entity"), dict) else {}
    values = (
        evidence.get("league_id"), evidence.get("season"), period.get("number"),
        period.get("start"), period.get("end"), entity.get("fantrax_scorer_id"),
        entity.get("scoring_role"), evidence.get("evidence_version"),
        evidence.get("league_fantasy_points"), evidence.get("source_status"),
        evidence.get("evidence_hash"), Jsonb(evidence), snapshot_id, evidence.get("observed_at"),
    )
    if any(value in (None, "") for value in values[:10]) or not snapshot_id:
        raise ValueError("trade player-period evidence identity is incomplete")
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO trade_player_period_evidence
               (league_id, season, period_number, period_start, period_end,
                fantrax_scorer_id, scoring_role, evidence_version,
                league_fantasy_points, source_status, evidence_hash, evidence,
                source_snapshot_id, observed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (
                 league_id, season, period_number, period_start, period_end,
                 fantrax_scorer_id, scoring_role, evidence_version
               ) DO NOTHING
               RETURNING *""",
            values,
        ).fetchone()
        if row:
            return dict(row), True
        existing = conn.execute(
            """SELECT * FROM trade_player_period_evidence
               WHERE league_id=%s AND season=%s AND period_number=%s
                 AND period_start=%s AND period_end=%s AND fantrax_scorer_id=%s
                 AND scoring_role=%s AND evidence_version=%s""",
            values[:8],
        ).fetchone()
        if existing and existing.get("evidence_hash") == evidence.get("evidence_hash") and existing.get("evidence") == evidence:
            return dict(existing), False
        raise ValueError("trade player-period entity already has different immutable evidence")


def _init_recommendation_receipts_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendation_receipts (
          receipt_id TEXT PRIMARY KEY,
          builder_version TEXT NOT NULL,
          scope_key TEXT NOT NULL,
          source TEXT NOT NULL,
          action_type TEXT NOT NULL,
          league_id TEXT NOT NULL,
          team_id TEXT NOT NULL,
          season INTEGER NOT NULL,
          period_start DATE NOT NULL,
          period_end DATE NOT NULL,
          proposal_id TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          snapshot_id BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,
          recommendation JSONB NOT NULL,
          evaluation_horizon TEXT NOT NULL,
          metric_name TEXT NOT NULL,
          metric_unit TEXT NOT NULL,
          baseline_value DOUBLE PRECISION,
          projected_value DOUBLE PRECISION,
          projected_gain DOUBLE PRECISION,
          lifecycle_state TEXT NOT NULL DEFAULT 'active'
            CHECK (lifecycle_state IN ('active', 'superseded', 'expired')),
          superseded_by TEXT REFERENCES recommendation_receipts(receipt_id),
          decision_state TEXT NOT NULL DEFAULT 'pending'
            CHECK (decision_state IN ('pending', 'accepted', 'rejected')),
          decision_source TEXT,
          decision_reason TEXT,
          decided_at TIMESTAMPTZ,
          outcome_state TEXT NOT NULL DEFAULT 'pending'
            CHECK (outcome_state IN ('pending', 'scored', 'unavailable')),
          actual_baseline DOUBLE PRECISION,
          actual_value DOUBLE PRECISION,
          actual_gain DOUBLE PRECISION,
          scoring_version TEXT,
          outcome_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
          evaluated_at TIMESTAMPTZ,
          generated_at TIMESTAMPTZ NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (period_end >= period_start),
          CHECK (expires_at > generated_at)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS recommendation_receipts_active_scope_key
        ON recommendation_receipts (scope_key)
        WHERE lifecycle_state = 'active'
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS recommendation_receipts_history_idx
        ON recommendation_receipts (league_id, team_id, action_type, period_start DESC, generated_at DESC)
        """
    )


def _init_recommendation_outcome_evaluations_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recommendation_outcome_evaluations (
          receipt_id TEXT NOT NULL REFERENCES recommendation_receipts(receipt_id) ON DELETE CASCADE,
          scoring_version TEXT NOT NULL,
          state TEXT NOT NULL CHECK (state IN ('scored', 'unavailable')),
          source_evidence_version TEXT NOT NULL,
          source_evidence_hash TEXT NOT NULL CHECK (source_evidence_hash ~ '^[0-9a-f]{64}$'),
          evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
          metrics JSONB NOT NULL,
          evidence JSONB NOT NULL,
          evaluated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (receipt_id, scoring_version)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS recommendation_outcome_evaluations_version_time_idx
        ON recommendation_outcome_evaluations (scoring_version, evaluated_at DESC)
        """
    )


def record_recommendation_receipt(receipt: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Persist immutable evidence and supersede a changed active scope atomically."""
    from sandlot_receipts import immutable_receipt_fields

    immutable = immutable_receipt_fields(receipt)
    required_keys = (
        *immutable.keys(),
        "snapshot_id",
        "generated_at",
        "expires_at",
    )
    missing = [key for key in required_keys if receipt.get(key) is None]
    if missing:
        raise ValueError(f"Recommendation receipt missing immutable fields: {', '.join(missing)}")

    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM recommendation_receipts WHERE receipt_id = %s FOR UPDATE",
            (receipt["receipt_id"],),
        ).fetchone()
        if existing:
            existing = dict(existing)
            if immutable_receipt_fields(existing) != immutable:
                raise RuntimeError("Recommendation receipt identity collision with different immutable evidence")
            if existing.get("lifecycle_state") != "active":
                raise RuntimeError("A superseded or expired recommendation receipt cannot be reactivated")
            return existing, False

        active = conn.execute(
            """
            SELECT * FROM recommendation_receipts
            WHERE scope_key = %s AND lifecycle_state = 'active'
            FOR UPDATE
            """,
            (receipt["scope_key"],),
        ).fetchone()
        if active and active.get("decision_state") != "pending":
            raise RuntimeError("A decided recommendation receipt cannot be superseded")
        if active:
            conn.execute(
                """
                UPDATE recommendation_receipts
                SET lifecycle_state = 'superseded', updated_at = now()
                WHERE receipt_id = %s AND lifecycle_state = 'active'
                """,
                (active["receipt_id"],),
            )

        row = conn.execute(
            """
            INSERT INTO recommendation_receipts (
              receipt_id, builder_version, scope_key, source, action_type,
              league_id, team_id, season, period_start, period_end,
              proposal_id, input_hash, snapshot_id, recommendation,
              evaluation_horizon, metric_name, metric_unit,
              baseline_value, projected_value, projected_gain,
              generated_at, expires_at
            )
            VALUES (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s
            )
            RETURNING *
            """,
            (
                receipt["receipt_id"],
                receipt["builder_version"],
                receipt["scope_key"],
                receipt["source"],
                receipt["action_type"],
                receipt["league_id"],
                receipt["team_id"],
                receipt["season"],
                receipt["period_start"],
                receipt["period_end"],
                receipt["proposal_id"],
                receipt["input_hash"],
                receipt["snapshot_id"],
                Jsonb(receipt["recommendation"]),
                receipt["evaluation_horizon"],
                receipt["metric_name"],
                receipt["metric_unit"],
                receipt["baseline_value"],
                receipt["projected_value"],
                receipt["projected_gain"],
                receipt["generated_at"],
                receipt["expires_at"],
            ),
        ).fetchone()
        if active:
            conn.execute(
                """
                UPDATE recommendation_receipts
                SET superseded_by = %s, updated_at = now()
                WHERE receipt_id = %s AND lifecycle_state = 'superseded'
                """,
                (receipt["receipt_id"], active["receipt_id"]),
            )
    if not row:
        raise RuntimeError("Recommendation receipt insert returned no row")
    return dict(row), True


def list_recommendation_receipts(*, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM recommendation_receipts
            ORDER BY generated_at DESC, receipt_id DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def pending_recommendation_receipts(*, source: str) -> list[dict[str, Any]]:
    """Return completed-horizon receipts that still need outcome telemetry."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM recommendation_receipts
            WHERE source = %s
              AND outcome_state = 'pending'
              AND period_end < CURRENT_DATE
            ORDER BY period_end, generated_at, receipt_id
            """,
            (source,),
        ).fetchall()
    return [dict(row) for row in rows]


def recent_scored_recommendation_receipts(*, source: str, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM recommendation_receipts
            WHERE source = %s AND outcome_state = 'scored'
            ORDER BY evaluated_at DESC, receipt_id DESC
            LIMIT %s
            """,
            (source, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def recommendation_outcome_evaluation_report(
    *, source: str, scoring_version: str, detail_limit: int = 8
) -> dict[str, Any]:
    """Aggregate independent active periods and return scalar-only recent details."""
    detail_limit = max(1, min(int(detail_limit), 50))
    samples_cte = """
        WITH ranked AS (
          SELECT
            e.state, e.metrics, e.evidence, e.evaluated_at,
            r.period_start, r.period_end, r.decision_state,
            row_number() OVER (
              PARTITION BY r.league_id, r.team_id, r.period_start, r.period_end
              ORDER BY r.generated_at DESC, e.evaluated_at DESC, e.receipt_id DESC
            ) AS sample_rank
          FROM recommendation_outcome_evaluations e
          JOIN recommendation_receipts r ON r.receipt_id = e.receipt_id
          WHERE r.source = %s
            AND r.lifecycle_state = 'active'
            AND e.scoring_version = %s
        ), samples AS (
          SELECT * FROM ranked WHERE sample_rank = 1
        )
    """
    with connect() as conn:
        summary = conn.execute(
            samples_cte + """
            SELECT
              count(*) AS evaluated,
              count(*) FILTER (WHERE state = 'scored') AS scored,
              count(*) FILTER (WHERE state = 'unavailable') AS unavailable,
              count(*) FILTER (
                WHERE state = 'scored'
                  AND evidence->>'decision_alignment' = 'accepted_proposal_observed'
              ) AS accepted_and_observed,
              count(*) FILTER (
                WHERE state = 'scored' AND evidence->>'actual_assignment_match' = 'proposed'
              ) AS proposed_matches,
              count(*) FILTER (
                WHERE state = 'scored' AND evidence->>'actual_assignment_match' = 'baseline'
              ) AS baseline_matches,
              count(*) FILTER (
                WHERE state = 'scored' AND evidence->>'actual_assignment_match' = 'other'
              ) AS other_matches,
              avg(
                CASE WHEN state = 'scored'
                  AND jsonb_typeof(metrics->'counterfactual_gain') = 'number'
                THEN (metrics->>'counterfactual_gain')::double precision END
              ) AS average_counterfactual_gain,
              avg(
                CASE WHEN state = 'scored'
                  AND jsonb_typeof(metrics->'counterfactual_gain') = 'number'
                THEN CASE WHEN (metrics->>'counterfactual_gain')::double precision > 0
                  THEN 1.0 ELSE 0.0 END END
              ) AS positive_counterfactual_gain_rate
            FROM samples
            """,
            (source, scoring_version),
        ).fetchone()
        details = conn.execute(
            samples_cte + """
            SELECT
              state, period_start, period_end, decision_state, evaluated_at,
              evidence->>'actual_assignment_match' AS actual_assignment_match,
              evidence->>'decision_alignment' AS decision_alignment,
              CASE WHEN jsonb_typeof(metrics->'counterfactual_baseline_total') = 'number'
                THEN (metrics->>'counterfactual_baseline_total')::double precision END
                AS counterfactual_baseline_total,
              CASE WHEN jsonb_typeof(metrics->'counterfactual_proposed_total') = 'number'
                THEN (metrics->>'counterfactual_proposed_total')::double precision END
                AS counterfactual_proposed_total,
              CASE WHEN jsonb_typeof(metrics->'counterfactual_gain') = 'number'
                THEN (metrics->>'counterfactual_gain')::double precision END
                AS counterfactual_gain,
              CASE WHEN jsonb_typeof(metrics->'observed_team_total') = 'number'
                THEN (metrics->>'observed_team_total')::double precision END
                AS observed_team_total
            FROM samples
            ORDER BY evaluated_at DESC, period_end DESC, period_start DESC
            LIMIT %s
            """,
            (source, scoring_version, detail_limit),
        ).fetchall()
    return {
        "summary": dict(summary or {}),
        "items": [dict(row) for row in details],
    }


def list_lineup_decision_science_rows(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Return up to 10,000 internal receipt/evaluation rows for offline modeling."""
    bounded_limit = max(1, min(int(limit), 10000)) if limit is not None else 10000
    with connect() as conn:
        rows = conn.execute(
            """
            WITH ranked AS (
              SELECT
                r.receipt_id, r.builder_version, r.input_hash, r.generated_at, r.period_start, r.period_end,
                r.baseline_value, r.projected_value, r.projected_gain,
                r.recommendation, e.state, e.scoring_version, e.metrics, e.evaluated_at,
                e.source_evidence_version, e.source_evidence_hash,
                e.evidence_hash AS evaluation_evidence_hash,
                e.evidence AS evaluation_evidence,
                l.evidence->'counterfactual_capability' AS counterfactual_capability,
                row_number() OVER (
                  PARTITION BY r.league_id, r.team_id, r.period_start, r.period_end
                  ORDER BY r.generated_at DESC, e.evaluated_at DESC, r.receipt_id DESC
                ) AS sample_rank
              FROM recommendation_receipts r
              LEFT JOIN recommendation_outcome_evaluations e
                ON e.receipt_id = r.receipt_id
               AND e.scoring_version = 'counterfactual_lineup_v1'
              LEFT JOIN lineup_period_evidence l
                ON l.league_id = r.league_id
               AND l.team_id = r.team_id
               AND l.period_start = r.period_start
               AND l.period_end = r.period_end
               AND l.evidence_version = 'fantrax_period_lineup_v2'
              WHERE r.source = 'monday_lineup'
                AND r.lifecycle_state = 'active'
            )
            SELECT receipt_id, builder_version, input_hash, generated_at, period_start, period_end,
                   baseline_value, projected_value, projected_gain,
                   recommendation, state, scoring_version, metrics, evaluated_at,
                   source_evidence_version, source_evidence_hash, evaluation_evidence_hash,
                   evaluation_evidence, counterfactual_capability
            FROM ranked
            WHERE sample_rank = 1
            ORDER BY generated_at ASC, receipt_id ASC
            LIMIT %s
            """,
            (bounded_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def receipts_missing_outcome_evaluation(
    *, source: str, scoring_version: str, evidence_version: str
) -> list[dict[str, Any]]:
    """Return completed receipts with eligible archived evidence but no evaluation."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, l.evidence AS period_evidence
            FROM recommendation_receipts r
            JOIN lineup_period_evidence l
              ON l.league_id = r.league_id
             AND l.team_id = r.team_id
             AND l.period_start = r.period_start
             AND l.period_end = r.period_end
             AND l.evidence_version = %s
            LEFT JOIN recommendation_outcome_evaluations e
              ON e.receipt_id = r.receipt_id AND e.scoring_version = %s
            WHERE r.source = %s
              AND r.period_end < CURRENT_DATE
              AND r.expires_at <= clock_timestamp()
              AND e.receipt_id IS NULL
              AND l.evidence->'counterfactual_capability'->>'eligible' = 'true'
            ORDER BY r.period_end, r.generated_at, r.receipt_id
            """,
            (evidence_version, scoring_version, source),
        ).fetchall()
    return [dict(row) for row in rows]


def trade_receipts_missing_static_package_evaluation(
    *, league_id: str, team_id: str, limit: int = 500,
) -> list[dict[str, Any]]:
    """Return eligible V4 trade receipts regardless of owner intent or lifecycle state."""
    import sandlot_trade_outcomes

    bounded_limit = max(1, min(int(limit), 5000))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*
            FROM recommendation_receipts r
            LEFT JOIN recommendation_outcome_evaluations e
              ON e.receipt_id = r.receipt_id AND e.scoring_version = %s
            WHERE r.action_type = 'trade_assessment'
              AND r.builder_version = %s
              AND r.league_id = %s
              AND r.team_id = %s
              AND r.recommendation->'outcome_contract'->>'eligible' = 'true'
              AND e.receipt_id IS NULL
            ORDER BY r.generated_at, r.receipt_id
            LIMIT %s
            """,
            (
                sandlot_trade_outcomes.TRADE_STATIC_PACKAGE_SCORING_VERSION,
                sandlot_trade_outcomes.TRADE_ASSESSMENT_BUILDER_VERSION,
                league_id,
                team_id,
                bounded_limit,
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def get_trade_player_period_evidence(
    *, requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load exact archived entities for a bounded set of frozen requirements."""
    if not requirements:
        return []
    found: list[dict[str, Any]] = []
    with connect() as conn:
        for requirement in requirements:
            row = conn.execute(
                """SELECT evidence FROM trade_player_period_evidence
                   WHERE league_id=%s AND season=%s AND period_number=%s
                     AND period_start=%s AND period_end=%s AND fantrax_scorer_id=%s
                     AND scoring_role=%s AND evidence_version=%s""",
                (
                    requirement.get("league_id"), requirement.get("season"),
                    requirement.get("period_number"), requirement.get("period_start"),
                    requirement.get("period_end"), requirement.get("fantrax_scorer_id"),
                    requirement.get("scoring_role"), requirement.get("evidence_version"),
                ),
            ).fetchone()
            if row and isinstance(row.get("evidence"), dict):
                found.append(row["evidence"])
    return found


def record_recommendation_outcome_evaluation(
    *, receipt_id: str, evaluation: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Append one immutable scorer version without touching legacy outcome columns."""
    from sandlot_receipts import (
        COUNTERFACTUAL_LINEUP_SCORING_VERSION,
        COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION,
        counterfactual_evidence_hash,
    )

    if evaluation.get("scoring_version") != COUNTERFACTUAL_LINEUP_SCORING_VERSION:
        raise ValueError("unsupported recommendation evaluation scoring version")
    state = evaluation.get("state")
    if state not in {"scored", "unavailable"}:
        raise ValueError("counterfactual recommendation evaluation state is invalid")
    source_version = str(evaluation.get("source_evidence_version") or "")
    source_hash = str(evaluation.get("source_evidence_hash") or "")
    evidence = evaluation.get("evidence")
    metrics = evaluation.get("metrics")
    if not source_version or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("counterfactual source evidence identity is invalid")
    if source_version != COUNTERFACTUAL_LINEUP_SOURCE_EVIDENCE_VERSION:
        raise ValueError("counterfactual source evidence version is unsupported")
    if not isinstance(evidence, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("evidence_hash") or "")):
        raise ValueError("counterfactual evaluation evidence hash is required")
    if evidence["evidence_hash"] != counterfactual_evidence_hash(evidence):
        raise ValueError("counterfactual evaluation evidence hash is invalid")
    required_metrics = {
        "counterfactual_baseline_total",
        "counterfactual_proposed_total",
        "counterfactual_gain",
        "observed_team_total",
    }
    expected_metrics = required_metrics if state == "scored" else set()
    if not isinstance(metrics, dict) or set(metrics) != expected_metrics:
        raise ValueError("counterfactual evaluation metrics are incomplete")
    for value in metrics.values():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("counterfactual evaluation metric must be finite") from exc
        if not math.isfinite(number):
            raise ValueError("counterfactual evaluation metric must be finite")
    if evidence.get("metrics") != metrics:
        raise ValueError("counterfactual evaluation metrics do not match evidence")
    if evidence.get("autopilot_eligible") is not False:
        raise ValueError("counterfactual evaluation cannot enable autopilot")
    embedded_source = evidence.get("source_evidence")
    if not isinstance(embedded_source, dict) or embedded_source != {
        "version": source_version,
        "hash": source_hash,
    }:
        raise ValueError("counterfactual evaluation source lineage is contradictory")
    if evidence.get("scoring_version") != evaluation["scoring_version"]:
        raise ValueError("counterfactual evaluation scoring lineage is contradictory")

    with connect() as conn:
        receipt = conn.execute(
            "SELECT * FROM recommendation_receipts WHERE receipt_id = %s FOR UPDATE",
            (receipt_id,),
        ).fetchone()
        if not receipt:
            raise LookupError("Recommendation receipt not found")
        expected = {
            "receipt_id": receipt.get("receipt_id"),
            "input_hash": receipt.get("input_hash"),
            "league_id": receipt.get("league_id"),
            "team_id": receipt.get("team_id"),
        }
        if any(evidence.get(key) != value for key, value in expected.items()):
            raise ValueError("counterfactual evaluation does not match target receipt")
        if evidence.get("decision_state") not in (None, receipt.get("decision_state")):
            raise ValueError("counterfactual evaluation decision state is stale")
        period = evidence.get("period") if isinstance(evidence.get("period"), dict) else {}
        if str(period.get("start")) != str(receipt.get("period_start")) or str(period.get("end")) != str(receipt.get("period_end")):
            raise ValueError("counterfactual evaluation period does not match target receipt")
        archive = conn.execute(
            """
            SELECT evidence_hash FROM lineup_period_evidence
            WHERE league_id=%s AND team_id=%s AND period_start=%s AND period_end=%s
              AND evidence_version=%s
            """,
            (
                receipt.get("league_id"), receipt.get("team_id"),
                receipt.get("period_start"), receipt.get("period_end"), source_version,
            ),
        ).fetchone()
        if not archive or archive.get("evidence_hash") != source_hash:
            raise ValueError("counterfactual source archive does not match stored evidence")
        row = conn.execute(
            """
            INSERT INTO recommendation_outcome_evaluations
              (receipt_id, scoring_version, state, source_evidence_version,
               source_evidence_hash, evidence_hash, metrics, evidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (receipt_id, scoring_version) DO NOTHING
            RETURNING *
            """,
            (
                receipt_id, evaluation["scoring_version"], state,
                source_version, source_hash, evidence["evidence_hash"],
                Jsonb(metrics), Jsonb(evidence),
            ),
        ).fetchone()
        if row:
            return dict(row), True
        existing = conn.execute(
            """SELECT * FROM recommendation_outcome_evaluations
               WHERE receipt_id=%s AND scoring_version=%s""",
            (receipt_id, evaluation["scoring_version"]),
        ).fetchone()
        same = existing and all((
            existing.get("state") == evaluation["state"],
            existing.get("source_evidence_version") == source_version,
            existing.get("source_evidence_hash") == source_hash,
            existing.get("evidence_hash") == evidence["evidence_hash"],
            existing.get("metrics") == metrics,
            existing.get("evidence") == evidence,
        ))
        if same:
            return dict(existing), False
        raise ValueError("recommendation evaluation version already has different immutable evidence")


def record_trade_static_package_evaluation(
    *, receipt_id: str, evaluation: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Append one immutable trade package label with exact source-row verification."""
    import sandlot_trade_outcomes

    if evaluation.get("scoring_version") != sandlot_trade_outcomes.TRADE_STATIC_PACKAGE_SCORING_VERSION:
        raise ValueError("trade static package scoring version is unsupported")
    state = evaluation.get("state")
    if state not in {"scored", "unavailable"}:
        raise ValueError("trade static package evaluation state is unsupported")
    source_version = str(evaluation.get("source_evidence_version") or "")
    source_hash = str(evaluation.get("source_evidence_hash") or "")
    evidence = evaluation.get("evidence")
    metrics = evaluation.get("metrics")
    if source_version != sandlot_trade_outcomes.TRADE_PLAYER_PERIOD_EVIDENCE_VERSION:
        raise ValueError("trade static package source version is unsupported")
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("trade static package source hash is invalid")
    if not isinstance(evidence, dict) or evidence.get("evidence_hash") != sandlot_trade_outcomes.static_package_evaluation_hash(evidence):
        raise ValueError("trade static package evaluation hash is invalid")
    scored_metrics = {
        "give_package_points", "get_package_points", "static_package_asset_points_delta",
        "give_asset_count", "get_asset_count", "give_entity_count", "get_entity_count",
    }
    required_metrics = scored_metrics if state == "scored" else set()
    if not isinstance(metrics, dict) or set(metrics) != required_metrics or evidence.get("metrics") != metrics:
        raise ValueError("trade static package metrics are incomplete")
    if not all(math.isfinite(float(value)) for value in metrics.values()):
        raise ValueError("trade static package metrics must be finite")
    if state == "scored" and round(
        float(metrics["get_package_points"]) - float(metrics["give_package_points"]), 4
    ) != float(metrics["static_package_asset_points_delta"]):
        raise ValueError("trade static package delta is contradictory")
    for key in (
        "causal_lift_claimed", "execution_claimed", "lineup_lift_claimed",
        "ros_claimed", "dynasty_claimed", "autopilot_eligible",
    ):
        if evidence.get(key) is not False:
            raise ValueError("trade static package evaluation contains an unsupported claim")
    if evidence.get("execution_state") != "unknown":
        raise ValueError("trade static package evaluation cannot infer execution")
    embedded_source = evidence.get("source_evidence")
    if not isinstance(embedded_source, dict) or embedded_source.get("version") != source_version:
        raise ValueError("trade static package source lineage is invalid")
    source_rows = embedded_source.get("rows") if state == "scored" else None
    if state == "scored" and (not isinstance(source_rows, list) or not source_rows):
        raise ValueError("trade static package source rows are missing")
    if embedded_source.get("hash") != source_hash or (
        state == "scored" and source_hash != sandlot_trade_outcomes.source_set_hash(source_rows)
    ):
        raise ValueError("trade static package source-set hash is invalid")

    with connect() as conn:
        receipt = conn.execute(
            "SELECT * FROM recommendation_receipts WHERE receipt_id = %s FOR UPDATE",
            (receipt_id,),
        ).fetchone()
        if not receipt:
            raise LookupError("Recommendation receipt not found")
        if receipt.get("builder_version") != sandlot_trade_outcomes.TRADE_ASSESSMENT_BUILDER_VERSION:
            raise ValueError("trade static package receipt version is unsupported")
        expected = {
            "receipt_id": receipt.get("receipt_id"), "input_hash": receipt.get("input_hash"),
            "league_id": receipt.get("league_id"), "team_id": receipt.get("team_id"),
        }
        if any(evidence.get(key) != value for key, value in expected.items()):
            raise ValueError("trade static package evaluation does not match the receipt")
        recommendation = receipt.get("recommendation") if isinstance(receipt.get("recommendation"), dict) else {}
        contract = recommendation.get("outcome_contract") if isinstance(recommendation.get("outcome_contract"), dict) else {}
        target = contract.get("target_period") if isinstance(contract.get("target_period"), dict) else {}
        expected_target = {
            "season": target.get("season"), "period_number": str(target.get("period_number")),
            "start": target.get("start"), "end": target.get("end"),
            "maturity_at": target.get("maturity_at"),
        }
        if evidence.get("target_period") != expected_target:
            raise ValueError("trade static package target period contradicts the receipt")
        if evidence.get("offer_cluster_key") != contract.get("offer_cluster_key"):
            raise ValueError("trade static package offer cluster contradicts the receipt")
        if state == "unavailable":
            sandlot_trade_outcomes.validate_static_package_unavailable(
                receipt=dict(receipt), evaluation=evaluation,
            )
            terminal_proof = evidence.get("terminal_proof")
            if not isinstance(terminal_proof, dict):
                raise ValueError("trade unavailable terminal proof is missing")
            snapshot_id = int(terminal_proof.get("snapshot_id") or 0)
            snapshot_row = conn.execute(
                "SELECT id, taken_at, league_id, team_id, data FROM snapshots WHERE id = %s",
                (snapshot_id,),
            ).fetchone()
            if not snapshot_row or not isinstance(snapshot_row.get("data"), dict):
                raise ValueError("trade unavailable terminal snapshot was not found")
            snapshot = snapshot_row["data"]
            if (
                str(snapshot_row.get("league_id") or "") != str(receipt.get("league_id") or "")
                or str(snapshot_row.get("team_id") or "") != str(receipt.get("team_id") or "")
                or str(snapshot.get("league_id") or "") != str(receipt.get("league_id") or "")
                or str(snapshot.get("team_id") or "") != str(receipt.get("team_id") or "")
            ):
                raise ValueError("trade unavailable terminal snapshot identity is contradictory")
            stored_taken_at = sandlot_trade_outcomes._utc_datetime(
                snapshot_row.get("taken_at"), "stored snapshot taken_at",
            )
            payload_taken_at = sandlot_trade_outcomes._utc_datetime(
                snapshot.get("timestamp"), "stored snapshot timestamp",
            )
            if stored_taken_at != payload_taken_at:
                raise ValueError("trade unavailable terminal snapshot timestamp is contradictory")
            observations = evidence.get("missing_observations")
            if not isinstance(observations, list) or not observations:
                raise ValueError("trade unavailable missing observations are absent")
            replay_as_of = max(
                sandlot_trade_outcomes._utc_datetime(
                    item.get("observed_at") if isinstance(item, dict) else None,
                    "trade unavailable observation time",
                )
                for item in observations
            )
            expected_evaluation = sandlot_trade_outcomes.build_static_package_unavailable(
                receipt=dict(receipt), missing_observations=observations,
                snapshot=snapshot, snapshot_id=snapshot_id, as_of=replay_as_of,
            )
            if expected_evaluation != evaluation:
                raise ValueError("trade unavailable evaluation does not replay from the stored snapshot")
        else:
            archived_evidence: list[dict[str, Any]] = []
            for source in source_rows:
                if not isinstance(source, dict):
                    raise ValueError("trade static package source row is invalid")
                archive = conn.execute(
                    """SELECT evidence_hash, evidence FROM trade_player_period_evidence
                       WHERE league_id=%s AND season=%s AND period_number=%s
                         AND period_start=%s AND period_end=%s AND fantrax_scorer_id=%s
                         AND scoring_role=%s AND evidence_version=%s""",
                    (
                        source.get("league_id"), source.get("season"), source.get("period_number"),
                        source.get("period_start"), source.get("period_end"),
                        source.get("fantrax_scorer_id"), source.get("scoring_role"),
                        source.get("evidence_version"),
                    ),
                ).fetchone()
                if not archive or archive.get("evidence_hash") != source.get("hash"):
                    raise ValueError("trade static package source row does not match the archive")
                if not isinstance(archive.get("evidence"), dict):
                    raise ValueError("trade static package source archive is incomplete")
                archived_evidence.append(archive["evidence"])
            replay_as_of = max(str(item.get("observed_at") or "") for item in archived_evidence)
            expected_evaluation = sandlot_trade_outcomes.build_static_package_evaluation(
                receipt=dict(receipt), player_period_evidence=archived_evidence, as_of=replay_as_of,
            )
            if expected_evaluation != evaluation:
                raise ValueError("trade static package evaluation does not replay from stored evidence")
        row = conn.execute(
            """INSERT INTO recommendation_outcome_evaluations
                 (receipt_id, scoring_version, state, source_evidence_version,
                  source_evidence_hash, evidence_hash, metrics, evidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (receipt_id, scoring_version) DO NOTHING
               RETURNING *""",
            (
                receipt_id, evaluation["scoring_version"], evaluation["state"],
                source_version, source_hash, evidence["evidence_hash"],
                Jsonb(metrics), Jsonb(evidence),
            ),
        ).fetchone()
        if row:
            return dict(row), True
        existing = conn.execute(
            """SELECT * FROM recommendation_outcome_evaluations
               WHERE receipt_id=%s AND scoring_version=%s""",
            (receipt_id, evaluation["scoring_version"]),
        ).fetchone()
        same = existing and all((
            existing.get("state") == evaluation["state"],
            existing.get("source_evidence_version") == source_version,
            existing.get("source_evidence_hash") == source_hash,
            existing.get("evidence_hash") == evidence["evidence_hash"],
            existing.get("metrics") == metrics,
            existing.get("evidence") == evidence,
        ))
        if same:
            return dict(existing), False
        raise ValueError("trade static package evaluation already has different immutable evidence")


def score_recommendation_receipt_team_result(
    *, receipt_id: str, outcome: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Persist one immutable, versioned observed-team outcome with CAS semantics."""
    from sandlot_receipts import TEAM_RESULT_SCORING_VERSION, team_result_evidence_hash

    if outcome.get("scoring_version") != TEAM_RESULT_SCORING_VERSION:
        raise ValueError("Unsupported recommendation outcome scoring version")
    if outcome.get("actual_baseline") is not None or outcome.get("actual_gain") is not None:
        raise ValueError("team_result_v1 cannot claim counterfactual baseline or gain")
    evidence = outcome.get("outcome_evidence")
    if not isinstance(evidence, dict) or not evidence.get("evidence_hash"):
        raise ValueError("Recommendation outcome evidence hash is required")
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence["evidence_hash"])):
        raise ValueError("Recommendation outcome evidence hash must be lowercase SHA-256")
    if evidence["evidence_hash"] != team_result_evidence_hash(evidence):
        raise ValueError("Recommendation outcome evidence hash is invalid")
    try:
        actual_value = float(outcome.get("actual_value"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Recommendation actual team total must be finite") from exc
    if not math.isfinite(actual_value):
        raise ValueError("Recommendation actual team total must be finite")
    required_evidence = {
        "measurement_scope": "observed_team_total",
        "adherence_state": "unverified",
        "counterfactual_state": "unavailable",
        "counterfactual_reason": "per_player_period_scoring_and_lineup_participation_not_ingested",
    }
    if any(evidence.get(key) != value for key, value in required_evidence.items()):
        raise ValueError("team_result_v1 requires fixed non-counterfactual evidence labels")

    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM recommendation_receipts WHERE receipt_id = %s FOR UPDATE",
            (receipt_id,),
        ).fetchone()
        if not row:
            raise LookupError("Recommendation receipt not found")
        row = dict(row)
        if row.get("outcome_state") == "scored":
            same = (
                row.get("scoring_version") == outcome["scoring_version"]
                and row.get("actual_value") == outcome.get("actual_value")
                and row.get("actual_baseline") is None
                and row.get("actual_gain") is None
                and row.get("outcome_evidence") == evidence
            )
            if same:
                return row, False
            raise ValueError("Recommendation receipt already has different outcome evidence")
        if row.get("outcome_state") != "pending":
            raise ValueError(f"Recommendation receipt outcome is already {row.get('outcome_state')}")
        expected_binding = {
            "receipt_id": row.get("receipt_id"),
            "input_hash": row.get("input_hash"),
            "league_id": row.get("league_id"),
            "team_id": row.get("team_id"),
        }
        if any(evidence.get(key) != value for key, value in expected_binding.items()):
            raise ValueError("Recommendation outcome evidence does not match the target receipt")
        period = evidence.get("period") if isinstance(evidence.get("period"), dict) else {}
        if str(period.get("start")) != str(row.get("period_start")) or str(period.get("end")) != str(row.get("period_end")):
            raise ValueError("Recommendation outcome period does not match the target receipt")
        if evidence.get("projected_team_total") != row.get("projected_value"):
            raise ValueError("Recommendation outcome projection does not match the target receipt")
        updated = conn.execute(
            """
            UPDATE recommendation_receipts
            SET outcome_state = 'scored',
                actual_baseline = NULL,
                actual_value = %s,
                actual_gain = NULL,
                scoring_version = %s,
                outcome_evidence = %s,
                evaluated_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE receipt_id = %s AND outcome_state = 'pending'
            RETURNING *
            """,
            (
                actual_value,
                outcome["scoring_version"],
                Jsonb(evidence),
                receipt_id,
            ),
        ).fetchone()
        if not updated:
            raise RuntimeError("Recommendation receipt changed during outcome scoring")
    return dict(updated), True


def mark_recommendation_receipt_outcome_unavailable(
    *, receipt_id: str, evidence: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Record one terminal inability to observe a completed-period result."""
    from sandlot_receipts import TEAM_RESULT_SCORING_VERSION, team_result_evidence_hash

    if evidence.get("reason") != "completed_period_evidence_missed_after_grace_window":
        raise ValueError("Unsupported unavailable outcome reason")
    if evidence.get("retryable") is not False:
        raise ValueError("Unavailable recommendation outcome must be terminal")
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("evidence_hash") or "")):
        raise ValueError("Unavailable outcome evidence hash must be lowercase SHA-256")
    if evidence["evidence_hash"] != team_result_evidence_hash(evidence):
        raise ValueError("Unavailable outcome evidence hash is invalid")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM recommendation_receipts WHERE receipt_id = %s FOR UPDATE",
            (receipt_id,),
        ).fetchone()
        if not row:
            raise LookupError("Recommendation receipt not found")
        row = dict(row)
        if row.get("outcome_state") == "unavailable":
            if row.get("scoring_version") == TEAM_RESULT_SCORING_VERSION and row.get("outcome_evidence") == evidence:
                return row, False
            raise ValueError("Recommendation receipt already has different unavailable evidence")
        if row.get("outcome_state") != "pending":
            raise ValueError(f"Recommendation receipt outcome is already {row.get('outcome_state')}")
        expected_binding = {
            "receipt_id": row.get("receipt_id"),
            "input_hash": row.get("input_hash"),
            "league_id": row.get("league_id"),
            "team_id": row.get("team_id"),
        }
        if any(evidence.get(key) != value for key, value in expected_binding.items()):
            raise ValueError("Unavailable outcome evidence does not match the target receipt")
        period = evidence.get("period") if isinstance(evidence.get("period"), dict) else {}
        if str(period.get("start")) != str(row.get("period_start")) or str(period.get("end")) != str(row.get("period_end")):
            raise ValueError("Unavailable outcome period does not match the target receipt")
        updated = conn.execute(
            """
            UPDATE recommendation_receipts
            SET outcome_state = 'unavailable', scoring_version = %s,
                actual_baseline = NULL, actual_value = NULL, actual_gain = NULL,
                outcome_evidence = %s, evaluated_at = clock_timestamp(), updated_at = clock_timestamp()
            WHERE receipt_id = %s AND outcome_state = 'pending'
            RETURNING *
            """,
            (TEAM_RESULT_SCORING_VERSION, Jsonb(evidence), receipt_id),
        ).fetchone()
        if not updated:
            raise RuntimeError("Recommendation receipt changed during unavailable outcome recording")
    return dict(updated), True


def latest_active_recommendation_receipt(*, source: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM recommendation_receipts
            WHERE source = %s
              AND lifecycle_state = 'active'
              AND expires_at > clock_timestamp()
            ORDER BY generated_at DESC, receipt_id DESC
            LIMIT 1
            """,
            (source,),
        ).fetchone()
    return dict(row) if row else None


def decide_recommendation_receipt(
    *,
    receipt_id: str,
    input_hash: str,
    decision: str,
    source: str,
    reason: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record one terminal owner decision with DB-clock expiry and CAS semantics."""
    if decision not in {"accepted", "rejected"}:
        raise ValueError("Decision must be accepted or rejected")
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM recommendation_receipts
            WHERE receipt_id = %s
            FOR UPDATE
            """,
            (receipt_id,),
        ).fetchone()
        if not row:
            raise LookupError("Recommendation receipt not found")
        row = dict(row)
        if str(row.get("input_hash") or "") != input_hash:
            raise ValueError("Recommendation receipt hash is stale or mismatched")
        clock_row = conn.execute("SELECT clock_timestamp() AS current_time").fetchone()
        if row.get("expires_at") <= clock_row["current_time"]:
            raise ValueError("Recommendation receipt has expired")
        if row.get("lifecycle_state") != "active":
            raise ValueError("Recommendation receipt is no longer active")
        current = row.get("decision_state")
        if current == decision:
            return row, False
        if current != "pending":
            raise ValueError(f"Recommendation receipt was already {current}")
        updated = conn.execute(
            """
            UPDATE recommendation_receipts
            SET decision_state = %s,
                decision_source = %s,
                decision_reason = %s,
                decided_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE receipt_id = %s
              AND input_hash = %s
              AND lifecycle_state = 'active'
              AND decision_state = 'pending'
              AND expires_at > clock_timestamp()
            RETURNING *
            """,
            (decision, source, reason, receipt_id, input_hash),
        ).fetchone()
        if not updated:
            raise RuntimeError("Recommendation receipt changed during decision recording")
    return dict(updated), True


def latest_successful_snapshot() -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM snapshots
            WHERE status = 'success'
            ORDER BY taken_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()


def previous_successful_snapshot(*, before_id: int, before_taken_at: datetime | None = None) -> dict[str, Any] | None:
    cutoff = before_taken_at or datetime.now(timezone.utc)
    with connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM snapshots
            WHERE status = 'success'
              AND (
                taken_at < %s
                OR (taken_at = %s AND id < %s)
              )
            ORDER BY taken_at DESC, id DESC
            LIMIT 1
            """,
            (cutoff, cutoff, before_id),
        ).fetchone()


def snapshot_from_days_ago(days: int) -> dict[str, Any] | None:
    """Most recent successful snapshot taken at least `days` days ago.

    Used to compute week-over-week deltas (rank, wins/losses) for the Today
    page. Returns None when the deploy hasn't been alive long enough to have
    a comparison row.
    """
    with connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM snapshots
            WHERE status = 'success'
              AND taken_at <= now() - make_interval(days => %s)
            ORDER BY taken_at DESC, id DESC
            LIMIT 1
            """,
            (days,),
        ).fetchone()


def snapshot_by_id(snapshot_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM snapshots
            WHERE id = %s
            """,
            (snapshot_id,),
        ).fetchone()


def latest_refresh_run() -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM refresh_runs
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()


def upsert_fantrax_cookies(
    cookies: list[dict[str, Any]],
    *,
    source: str = "manual",
    expires_at: datetime | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO fantrax_sessions (id, cookies_json, source, expires_at, refreshed_at)
            VALUES ('default', %s, %s, %s, now())
            ON CONFLICT (id) DO UPDATE
            SET cookies_json = EXCLUDED.cookies_json,
                source = EXCLUDED.source,
                expires_at = EXCLUDED.expires_at,
                refreshed_at = now()
            """,
            (Jsonb(cookies), source, expires_at),
        )


def get_fantrax_cookies() -> list[dict[str, Any]] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT cookies_json
            FROM fantrax_sessions
            WHERE id = 'default'
            """
        ).fetchone()
    if not row:
        return None
    cookies = row["cookies_json"]
    return cookies if isinstance(cookies, list) else None


def create_execution_request(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Create one immutable request or return its idempotent existing row."""
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO execution_requests (
              request_id, mode, snapshot_id, proposal_id, input_hash,
              contract, expected_roster_ids, safety, state, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            ON CONFLICT (mode, snapshot_id, proposal_id, input_hash) DO NOTHING
            RETURNING *, TRUE AS created
            """,
            (
                payload["request_id"],
                payload["mode"],
                payload["snapshot_id"],
                payload["proposal_id"],
                payload["input_hash"],
                Jsonb(payload["contract"]),
                Jsonb(payload["expected_roster_ids"]),
                Jsonb(payload["safety"]),
                payload["expires_at"],
            ),
        ).fetchone()
        if row:
            result = dict(row)
            result.pop("created", None)
            return result, True
        _expire_execution_requests(conn)
        row = conn.execute(
            """
            SELECT *
            FROM execution_requests
            WHERE mode = %s AND snapshot_id = %s AND proposal_id = %s AND input_hash = %s
            """,
            (
                payload["mode"],
                payload["snapshot_id"],
                payload["proposal_id"],
                payload["input_hash"],
            ),
        ).fetchone()
    if not row:
        raise RuntimeError("Execution request insert lost its idempotency row")
    return dict(row), False


def execution_request_by_id(request_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        _expire_execution_requests(conn, request_id=request_id)
        row = conn.execute(
            "SELECT * FROM execution_requests WHERE request_id = %s",
            (request_id,),
        ).fetchone()
    return dict(row) if row else None


def claim_next_execution_request(
    *,
    runner_id: str,
    lease_token_hash: str,
    lease_seconds: int,
) -> dict[str, Any] | None:
    """Atomically claim one request once; expired claims are never requeued."""
    with connect() as conn:
        _expire_execution_requests(conn)
        row = conn.execute(
            """
            WITH candidate AS (
              SELECT request_id
              FROM execution_requests
              WHERE state = 'pending' AND expires_at > now()
              ORDER BY created_at ASC, request_id ASC
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE execution_requests request
            SET state = 'claimed',
                claimed_at = now(),
                claimed_by = %s,
                lease_token_hash = %s,
                lease_expires_at = LEAST(
                  request.expires_at,
                  now() + (%s * interval '1 second')
                )
            FROM candidate
            WHERE request.request_id = candidate.request_id
              AND request.state = 'pending'
            RETURNING request.*
            """,
            (runner_id, lease_token_hash, lease_seconds),
        ).fetchone()
    return dict(row) if row else None


def finish_execution_preflight(
    *,
    request_id: str,
    lease_token_hash: str,
    outcome: str,
    evidence: dict[str, Any],
    failure_reason: str | None,
) -> dict[str, Any] | None:
    target_state = "preflight_passed" if outcome == "passed" else "preflight_failed"
    with connect() as conn:
        _expire_execution_requests(conn, request_id=request_id)
        row = conn.execute(
            """
            UPDATE execution_requests
            SET state = %s,
                evidence = %s,
                failure_reason = %s,
                lease_token_hash = NULL,
                completed_at = now()
            WHERE request_id = %s
              AND state = 'claimed'
              AND lease_token_hash = %s
              AND lease_expires_at > now()
              AND expires_at > now()
            RETURNING *
            """,
            (
                target_state,
                Jsonb(evidence),
                failure_reason,
                request_id,
                lease_token_hash,
            ),
        ).fetchone()
    return dict(row) if row else None


def _expire_execution_requests(conn: Any, *, request_id: str | None = None) -> None:
    request_filter = " AND request_id = %s" if request_id is not None else ""
    params: tuple[Any, ...] = (request_id,) if request_id is not None else ()
    conn.execute(
        f"""
        UPDATE execution_requests
        SET state = 'expired',
            completed_at = now(),
            lease_token_hash = NULL,
            failure_reason = CASE
              WHEN state = 'claimed' THEN 'Runner lease expired before preflight completion'
              ELSE 'Execution request expired before claim'
            END
        WHERE state IN ('pending', 'claimed')
          AND (
            expires_at <= now()
            OR (state = 'claimed' AND lease_expires_at <= now())
          )
          {request_filter}
        """,
        params,
    )


DEFAULT_CHAT_SESSION_TITLE = "Skipper"


def get_or_create_default_session() -> int:
    """V1 is single-user / single-thread; return (or create) the lone session."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM chat_sessions ORDER BY id ASC LIMIT 1
            """
        ).fetchone()
        if row:
            return int(row["id"])
        row = conn.execute(
            "INSERT INTO chat_sessions (title) VALUES (%s) RETURNING id",
            (DEFAULT_CHAT_SESSION_TITLE,),
        ).fetchone()
    return int(row["id"])


def list_chat_messages(session_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, created_at, role, content, tier, model
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
    return list(rows)


def append_chat_message(
    session_id: int,
    role: str,
    content: str,
    *,
    tier: int | None = None,
    model: str | None = None,
) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, tier, model)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (session_id, role, content, tier, model),
        ).fetchone()
    return int(row["id"])


def clear_chat_messages(session_id: int) -> int:
    with connect() as conn:
        rows = conn.execute(
            "DELETE FROM chat_messages WHERE session_id = %s RETURNING id",
            (session_id,),
        ).fetchall()
    return len(rows)


# ---------------------------------------------------------------------------
# Player profile cache (fantrax_id <-> mlb_id, plus per-game logs)
# ---------------------------------------------------------------------------


def get_mlb_id(fantrax_id: str) -> dict[str, Any] | None:
    """Return {mlb_id, resolved_at} or None if never resolved.

    `mlb_id` may be NULL in the row, meaning "we tried to resolve and failed";
    callers should treat that as a negative cache instead of re-looking-up.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT mlb_id, resolved_at FROM player_id_map WHERE fantrax_id = %s",
            (fantrax_id,),
        ).fetchone()
    return dict(row) if row else None


def set_mlb_id(fantrax_id: str, mlb_id: int | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO player_id_map (fantrax_id, mlb_id, resolved_at)
            VALUES (%s, %s, now())
            ON CONFLICT (fantrax_id) DO UPDATE
            SET mlb_id = EXCLUDED.mlb_id,
                resolved_at = now()
            """,
            (fantrax_id, mlb_id),
        )


def get_player_game_log(
    mlb_id: int,
    *,
    group_type: str,
    season: int,
) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT mlb_id, group_type, season, games, fetched_at
            FROM player_game_logs
            WHERE mlb_id = %s
              AND group_type = %s
              AND season = %s
            """,
            (mlb_id, group_type, season),
        ).fetchone()
    return dict(row) if row else None


def set_player_game_log(
    mlb_id: int,
    *,
    group_type: str,
    season: int,
    games: list[dict[str, Any]],
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO player_game_logs (mlb_id, group_type, season, games, fetched_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (mlb_id, group_type, season) DO UPDATE
            SET games = EXCLUDED.games,
                fetched_at = now()
            """,
            (mlb_id, group_type, season, Jsonb(games)),
        )


def get_player_media(mlb_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT mlb_id, items, fetched_at
            FROM player_media
            WHERE mlb_id = %s
            """,
            (mlb_id,),
        ).fetchone()
    return dict(row) if row else None


def set_player_media(mlb_id: int, items: list[dict[str, Any]]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO player_media (mlb_id, items, fetched_at)
            VALUES (%s, %s, now())
            ON CONFLICT (mlb_id) DO UPDATE
            SET items = EXCLUDED.items,
                fetched_at = now()
            """,
            (mlb_id, Jsonb(items)),
        )


def get_player_take(player_id: str, snapshot_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT player_id, snapshot_id, text, model, generated_at
            FROM player_takes
            WHERE player_id = %s AND snapshot_id = %s
            """,
            (player_id, snapshot_id),
        ).fetchone()
    return dict(row) if row else None


def set_player_take(player_id: str, snapshot_id: int, text: str, model: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO player_takes (player_id, snapshot_id, text, model, generated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (player_id, snapshot_id) DO UPDATE
            SET text = EXCLUDED.text,
                model = EXCLUDED.model,
                generated_at = now()
            """,
            (player_id, snapshot_id, text, model),
        )


def get_ai_brief(snapshot_id: int, brief_type: str, subject_key: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT snapshot_id, brief_type, subject_key, text, model, input_hash, generated_at
            FROM ai_briefs
            WHERE snapshot_id = %s AND brief_type = %s AND subject_key = %s
            """,
            (snapshot_id, brief_type, subject_key),
        ).fetchone()
    return dict(row) if row else None


def set_ai_brief(
    snapshot_id: int,
    brief_type: str,
    subject_key: str,
    text: str,
    model: str,
    input_hash: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_briefs
              (snapshot_id, brief_type, subject_key, text, model, input_hash, generated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (snapshot_id, brief_type, subject_key) DO UPDATE
            SET text = EXCLUDED.text,
                model = EXCLUDED.model,
                input_hash = EXCLUDED.input_hash,
                generated_at = now()
            """,
            (snapshot_id, brief_type, subject_key, text, model, input_hash),
        )


def upsert_projection_log(
    *,
    snapshot_id: int,
    model_version: str,
    matchup_key: str,
    period_id: str | None,
    my_team_id: str | None,
    opponent_team_id: str | None,
    predicted_my: float,
    predicted_opp: float,
    predicted_margin: float,
    win_probability: float,
    data_quality: dict[str, Any] | None = None,
    drivers: dict[str, Any] | None = None,
    surface: str = "api",
    shown_date: date | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO projection_logs
              (
                snapshot_id, model_version, surface, shown_date, matchup_key, period_id,
                my_team_id, opponent_team_id, predicted_my, predicted_opp,
                predicted_margin, win_probability, data_quality, drivers
              )
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_version, matchup_key, surface, shown_date) DO UPDATE
            SET snapshot_id = EXCLUDED.snapshot_id,
                period_id = EXCLUDED.period_id,
                my_team_id = EXCLUDED.my_team_id,
                opponent_team_id = EXCLUDED.opponent_team_id,
                predicted_my = EXCLUDED.predicted_my,
                predicted_opp = EXCLUDED.predicted_opp,
                predicted_margin = EXCLUDED.predicted_margin,
                win_probability = EXCLUDED.win_probability,
                data_quality = EXCLUDED.data_quality,
                drivers = EXCLUDED.drivers,
                updated_at = now()
            """,
            (
                snapshot_id,
                model_version,
                surface,
                shown_date or date.today(),
                matchup_key,
                period_id,
                my_team_id,
                opponent_team_id,
                predicted_my,
                predicted_opp,
                predicted_margin,
                win_probability,
                Jsonb(data_quality or {}),
                Jsonb(drivers or {}),
            ),
        )


def update_projection_actuals(
    *,
    matchup_key: str,
    period_id: str | None,
    actual_my: float,
    actual_opp: float,
    actual_winner: str,
) -> int:
    with connect() as conn:
        rows = conn.execute(
            """
            UPDATE projection_logs
            SET actual_my = %s,
                actual_opp = %s,
                actual_winner = %s,
                updated_at = now()
            WHERE matchup_key = %s
              AND (%s IS NULL OR period_id = %s)
            RETURNING id
            """,
            (actual_my, actual_opp, actual_winner, matchup_key, period_id, period_id),
        ).fetchall()
    return len(rows)


def list_projection_logs_for_evaluation(limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT
          id, snapshot_id, model_version, surface, shown_date, matchup_key, period_id,
          my_team_id, opponent_team_id, predicted_my, predicted_opp,
          predicted_margin, win_probability, data_quality, drivers,
          actual_my, actual_opp, actual_winner, created_at, updated_at
        FROM projection_logs
        WHERE actual_my IS NOT NULL
          AND actual_opp IS NOT NULL
          AND actual_winner IS NOT NULL
        ORDER BY updated_at DESC, id DESC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def prune_successful_snapshots(keep: int = 30) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            WITH doomed AS (
              SELECT id
              FROM snapshots
              WHERE status = 'success'
              ORDER BY taken_at DESC, id DESC
              OFFSET %s
            )
            DELETE FROM snapshots
            WHERE id IN (SELECT id FROM doomed)
            RETURNING id
            """,
            (keep,),
        ).fetchall()
    return len(row)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
