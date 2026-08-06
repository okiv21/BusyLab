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
        import psycopg
        from psycopg_pool import ConnectionPool

        self.dsn = dsn
        # A small pool: a free Supabase project has a modest connection cap and
        # the API plus one worker do not need many.
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=int(_env_int("BUSYLAB_PG_POOL", 4)), open=True
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

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, filename, path, fingerprint, detection, story, "
                "overrides, created_at FROM datasets WHERE id = %s",
                (dataset_id,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "id", "filename", "path", "fingerprint", "detection", "story",
            "overrides", "created_at",
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
