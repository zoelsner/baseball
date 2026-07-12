"""Postgres persistence for Sandlot v1.

V1 stores full Fantrax snapshots as JSONB and derives API responses from the
latest successful row. This keeps the data model easy to change while the app
shape is still moving.
"""

from __future__ import annotations

import os
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
