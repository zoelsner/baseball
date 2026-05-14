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
              mlb_id BIGINT PRIMARY KEY,
              group_type TEXT NOT NULL CHECK (group_type IN ('hitting', 'pitching')),
              season INTEGER NOT NULL,
              games JSONB NOT NULL,
              fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
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
              snapshot_id BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
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


def get_player_game_log(mlb_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT mlb_id, group_type, season, games, fetched_at
            FROM player_game_logs
            WHERE mlb_id = %s
            """,
            (mlb_id,),
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
            ON CONFLICT (mlb_id) DO UPDATE
            SET group_type = EXCLUDED.group_type,
                season = EXCLUDED.season,
                games = EXCLUDED.games,
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
