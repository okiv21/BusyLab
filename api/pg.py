"""Postgres-backed job and dataset storage.

Spec 8 calls for a Postgres jobs table with polling, and spec 9 puts the
database in Supabase. SQLite is right for development - one fewer service - but
it cannot go to production here, because a free Render instance has an
ephemeral disk: it spins down after inactivity and comes back with the file
gone. Every dataset, goal and alert would disappear without a single error.

This class implements exactly the same public methods as :class:`JobStore`, so
the rest of the application never learns which one it has. A contract test runs
the same assertions against both, because two implementations of one interface
drift the moment nothing is checking.

Three differences from the SQLite version are worth naming:

* Placeholders are ``%s`` rather than ``?``.
* Upserts use ``ON CONFLICT`` rather than ``INSERT OR REPLACE``.
* ``claim`` uses ``SELECT ... FOR UPDATE SKIP LOCKED``, which is the real
  version of what the SQLite lock was imitating. Two workers can now poll the
  same table concurrently and never collide, which is what makes a separate
  Render worker process safe.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .jobs import Job, JobKind, JobStatus, _now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    step        TEXT NOT NULL DEFAULT '',
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    result      JSONB,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS jobs_dataset ON jobs (dataset_id, kind);

CREATE TABLE IF NOT EXISTS datasets (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    fingerprint  TEXT,
    detection    JSONB,
    story        JSONB,
    overrides    JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Who receives this dataset's digest. One address per dataset, because a
    -- single global recipient sends every business's numbers to one inbox.
    recipient    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mappings (
    fingerprint  TEXT PRIMARY KEY,
    roles        JSONB NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    fingerprint  TEXT PRIMARY KEY,
    snapshot     JSONB NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    metric      TEXT NOT NULL,
    target      DOUBLE PRECISION NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goals_dataset ON goals (dataset_id);

CREATE TABLE IF NOT EXISTS alerts (
    key          TEXT NOT NULL,
    dataset_id   TEXT NOT NULL,
    payload      JSONB NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (key, dataset_id)
);
CREATE INDEX IF NOT EXISTS alerts_dataset ON alerts (dataset_id, created_at);
"""


class PostgresJobStore:
    """The production store. Same surface as :class:`JobStore`."""

    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool

        self.dsn = dsn
        # Check the credentials once, directly, before opening a pool.
        #
        # A pool treats every failure as transient and keeps retrying, which is
        # right for a dropped network and badly wrong for a rejected password:
        # it hammers the provider until Supabase's circuit breaker blocks the
        # address, and buries one real error under hundreds of identical
        # retries. One connection first turns that into a single readable line.
        _verify_credentials(dsn)
        # A small pool: a free Supabase project has a modest connection cap and
        # the API plus one worker do not need many.
        #
        # prepare_threshold=None disables psycopg's automatic prepared
        # statements, and it is not optional here. psycopg promotes a query to
        # a prepared statement after its fifth execution, and Supabase's
        # transaction pooler (port 6543) does not support prepared statements
        # at all. Since claim() polls continuously, the fifth execution arrives
        # within seconds of starting - so without this the worker would run
        # briefly and then fail permanently with a confusing protocol error.
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=_env_int("BUSYLAB_PG_POOL", 4),
            open=True,
            kwargs={"prepare_threshold": None, "autocommit": False},
        )
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            yield conn

    # -- datasets ---------------------------------------------------------

    def create_dataset(self, filename: str, path: str) -> str:
        dataset_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO datasets (id, filename, path, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (dataset_id, filename, path, _now()),
            )
        return dataset_id

    def set_dataset_path(self, dataset_id: str, path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET path = %s WHERE id = %s", (path, dataset_id)
            )

    def set_recipient(self, dataset_id: str, recipient: str) -> None:
        """Where this dataset's digest is sent."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET recipient = %s WHERE id = %s",
                (recipient, dataset_id),
            )

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, filename, path, fingerprint, detection, story, "
                "overrides, recipient, created_at FROM datasets WHERE id = %s",
                (dataset_id,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "id", "filename", "path", "fingerprint", "detection", "story",
            "overrides", "recipient", "created_at",
        )
        # psycopg already decodes jsonb into Python objects, so unlike the
        # SQLite store there is nothing to json.loads here.
        return dict(zip(keys, row))

    def save_detection(
        self, dataset_id: str, detection: dict[str, Any], fingerprint: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET detection = %s, fingerprint = %s WHERE id = %s",
                (json.dumps(detection), fingerprint, dataset_id),
            )

    def save_story(self, dataset_id: str, story: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET story = %s WHERE id = %s",
                (json.dumps(story), dataset_id),
            )

    def save_overrides(self, dataset_id: str, overrides: dict[str, str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET overrides = %s WHERE id = %s",
                (json.dumps(overrides), dataset_id),
            )

    def delete_dataset(self, dataset_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM datasets WHERE id = %s", (dataset_id,))
            conn.execute("DELETE FROM jobs WHERE dataset_id = %s", (dataset_id,))

    def all_dataset_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM datasets WHERE story IS NOT NULL ORDER BY created_at"
            ).fetchall()
        return [row[0] for row in rows]

    # -- mapping memory ---------------------------------------------------

    def remember_mapping(self, fingerprint: str, roles: dict[str, str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mappings (fingerprint, roles, created_at) "
                "VALUES (%s, %s, %s) ON CONFLICT (fingerprint) DO UPDATE "
                "SET roles = EXCLUDED.roles",
                (fingerprint, json.dumps(roles), _now()),
            )

    def recall_mapping(self, fingerprint: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT roles FROM mappings WHERE fingerprint = %s", (fingerprint,)
            ).fetchone()
        return row[0] if row else None

    # -- quality snapshots ------------------------------------------------

    def remember_snapshot(self, fingerprint: str, snapshot: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO snapshots (fingerprint, snapshot, created_at) "
                "VALUES (%s, %s, %s) ON CONFLICT (fingerprint) DO UPDATE "
                "SET snapshot = EXCLUDED.snapshot",
                (fingerprint, json.dumps(snapshot), _now()),
            )

    def recall_snapshot(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot FROM snapshots WHERE fingerprint = %s",
                (fingerprint,),
            ).fetchone()
        return row[0] if row else None

    # -- goals ------------------------------------------------------------

    def add_goal(
        self,
        dataset_id: str,
        metric: str,
        target: float,
        start: str,
        end: str,
        label: str = "",
    ) -> dict[str, Any]:
        goal_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO goals (id, dataset_id, metric, target, start_date, "
                "end_date, label, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (goal_id, dataset_id, metric, target, start, end, label, _now()),
            )
        return {
            "id": goal_id,
            "metric": metric,
            "target": target,
            "start": start,
            "end": end,
            "label": label,
        }

    def list_goals(self, dataset_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, metric, target, start_date, end_date, label "
                "FROM goals WHERE dataset_id = %s ORDER BY created_at",
                (dataset_id,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "metric": r[1],
                "target": r[2],
                "start": r[3],
                "end": r[4],
                "label": r[5],
            }
            for r in rows
        ]

    def delete_goal(self, dataset_id: str, goal_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM goals WHERE id = %s AND dataset_id = %s",
                (goal_id, dataset_id),
            )
            return bool(cursor.rowcount)

    # -- alerts -----------------------------------------------------------

    def record_alerts(self, dataset_id: str, alerts: list[dict[str, Any]]) -> int:
        stored = 0
        with self._connect() as conn:
            for alert in alerts:
                cursor = conn.execute(
                    "INSERT INTO alerts (key, dataset_id, payload, created_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (key, dataset_id) DO NOTHING",
                    (alert["key"], dataset_id, json.dumps(alert), _now()),
                )
                stored += int(cursor.rowcount)
        return stored

    def sent_alert_keys(self, dataset_id: str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key FROM alerts WHERE dataset_id = %s", (dataset_id,)
            ).fetchall()
        return {row[0] for row in rows}

    def list_alerts(
        self, dataset_id: str, *, include_acknowledged: bool = False
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT payload, acknowledged FROM alerts WHERE dataset_id = %s"
        )
        if not include_acknowledged:
            query += " AND acknowledged = FALSE"
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, (dataset_id,)).fetchall()
        out = []
        for payload, acknowledged in rows:
            payload = dict(payload)
            payload["acknowledged"] = bool(acknowledged)
            out.append(payload)
        return out

    def acknowledge_alert(self, dataset_id: str, key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE alerts SET acknowledged = TRUE "
                "WHERE key = %s AND dataset_id = %s",
                (key, dataset_id),
            )
            return bool(cursor.rowcount)

    # -- jobs -------------------------------------------------------------

    def enqueue(
        self, kind: JobKind, dataset_id: str, payload: dict[str, Any] | None = None
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:16],
            kind=kind,
            dataset_id=dataset_id,
            payload=payload or {},
            created_at=_now(),
            updated_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, kind, dataset_id, status, step, payload, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    job.id,
                    job.kind.value,
                    job.dataset_id,
                    job.status.value,
                    "",
                    json.dumps(job.payload),
                    job.created_at,
                    job.updated_at,
                ),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, kind, dataset_id, status, step, payload, result, "
                "error, created_at, updated_at FROM jobs WHERE id = %s",
                (job_id,),
            ).fetchone()
        return _row_to_job(row) if row else None

    def claim(self) -> Job | None:
        """Take the oldest pending job.

        ``FOR UPDATE SKIP LOCKED`` is the real version of what the SQLite lock
        was imitating: concurrent workers each grab a different row instead of
        contending for the same one. This is what makes running the API and a
        separate worker process safe.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, kind, dataset_id, status, step, payload, result, "
                "error, created_at, updated_at FROM jobs WHERE status = %s "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
                (JobStatus.PENDING.value,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status = %s, updated_at = %s WHERE id = %s",
                (JobStatus.RUNNING.value, _now(), row[0]),
            )
        job = _row_to_job(row)
        job.status = JobStatus.RUNNING
        return job

    def set_step(self, job_id: str, step: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET step = %s, updated_at = %s WHERE id = %s",
                (step, _now(), job_id),
            )

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = %s, result = %s, updated_at = %s, "
                "step = %s WHERE id = %s",
                (JobStatus.DONE.value, json.dumps(result), _now(), "done", job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = %s, error = %s, updated_at = %s "
                "WHERE id = %s",
                (JobStatus.FAILED.value, error, _now(), job_id),
            )

    def pending_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = %s",
                (JobStatus.PENDING.value,),
            ).fetchone()
        return int(row[0])


class DatabaseConfigError(RuntimeError):
    """The connection string is wrong in a way retrying will not fix."""


def _verify_credentials(dsn: str) -> None:
    """Open one connection, and translate the failure into something readable.

    Every message below names the specific mistake, because the raw errors do
    not. "password authentication failed for user postgres" is what Supabase
    says when the *username* is missing its project ref, which sends people off
    resetting a password that was never wrong.
    """
    import psycopg

    try:
        with psycopg.connect(dsn, connect_timeout=15) as conn:
            conn.execute("SELECT 1")
        return
    except psycopg.OperationalError as exc:
        message = str(exc)
        raise DatabaseConfigError(_explain(dsn, message)) from exc


def _explain(dsn: str, message: str) -> str:
    """Turn a driver error into the thing that is actually wrong."""
    lowered = message.lower()
    pooled = "pooler.supabase.com" in dsn
    user = _username(dsn)

    # The pooler's own words for "your username has no project ref". It is
    # the most precise signal available and worth matching before anything
    # else, because it is unambiguous where the auth-failure message is not.
    if "enoidentifier" in lowered or "no tenant identifier" in lowered:
        return (
            "The connection pooler could not tell which project this is. The "
            "username must be 'postgres.<your-project-ref>' - the pooler reads "
            f"the project from it, and {user!r} carries no ref. Copy the "
            "Transaction pooler string from the Connect button rather than "
            "editing the direct one."
        )

    if "circuitbreaker" in lowered or "too many authentication failures" in lowered:
        return (
            "Supabase has temporarily blocked this address after repeated "
            "failed logins. Wait about five minutes, then retry with the "
            "corrected credentials. "
            + _username_hint(pooled, user)
        )

    if "password authentication failed" in lowered:
        hint = _username_hint(pooled, user)
        return (
            f"The database rejected the credentials for user {user!r}. {hint}"
            " Otherwise the password is wrong, or it contains a character "
            "such as @ : / or ? that must be percent-encoded inside a URL."
        )

    if "does not exist" in lowered and "database" in lowered:
        return (
            "That database name does not exist. On Supabase it is 'postgres', "
            "which is the last path segment of the connection string."
        )

    if "network is unreachable" in lowered or "could not translate host" in lowered:
        return (
            "Could not reach the database host. If this is the direct "
            "connection (db.<ref>.supabase.co), it is IPv6-only and most hosts "
            "including Render cannot use it - take the transaction pooler "
            "string instead, on port 6543."
        )

    if "timeout" in lowered or "timed out" in lowered:
        return (
            "The database did not answer in time. Check the host and port, "
            "and that the project is not paused in the Supabase dashboard."
        )

    return f"Could not connect to the database: {_first_reason(message)}"


def _first_reason(message: str) -> str:
    """One line out of a driver error that repeats itself per IP address.

    psycopg tries every address the host resolves to and concatenates all the
    failures, so a single mistake arrives three or four times over. Only the
    first distinct reason is worth showing.
    """
    for line in message.splitlines():
        line = line.strip()
        if "FATAL:" in line:
            return line.split("FATAL:", 1)[1].strip()
    first = message.strip().splitlines()[0] if message.strip() else message
    return first.strip()


def _username(dsn: str) -> str:
    """The username in a connection string, without parsing the password."""
    from urllib.parse import urlparse

    try:
        return urlparse(dsn).username or "?"
    except ValueError:
        return "?"


def _username_hint(pooled: bool, user: str) -> str:
    if pooled and "." not in user:
        return (
            f"The host is the connection pooler, so the username must be "
            f"'postgres.<your-project-ref>' rather than plain '{user}' - the "
            "pooler identifies the project from it. Copy the transaction "
            "pooler string from Connect rather than editing the direct one."
        )
    if not pooled and "." in user:
        return (
            "The username carries a project ref, which belongs to the pooler, "
            "but the host is the direct connection. Use one or the other."
        )
    return ""


def _row_to_job(row: tuple) -> Job:
    return Job(
        id=row[0],
        kind=JobKind(row[1]),
        dataset_id=row[2],
        status=JobStatus(row[3]),
        step=row[4] or "",
        payload=row[5] or {},
        result=row[6],
        error=row[7],
        created_at=row[8],
        updated_at=row[9],
    )


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default
