"""The job queue.

Analysis is too slow for a request-response cycle. Detection plus significance
testing plus forecasting can take tens of seconds, and running that inside the
HTTP request means timeouts and spinners (spec 9). So an upload returns
immediately with a job id, a worker processes it off the request cycle, and the
frontend polls. This is architectural rather than cosmetic, and it has to exist
before any scheduled or proactive work can (spec 10, step 5).

Backed by SQLite here. Spec 8 calls for a Postgres jobs table with polling and
Redis later; the point of that choice is one fewer service to run, and the same
reasoning applies harder in local development. The interface below is
deliberately the small intersection of what SQLite and Postgres both do well —
insert, claim one pending row, update status — so moving to Supabase is a
driver swap rather than a redesign.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED)


class JobKind(str, Enum):
    DETECT = "detect"
    ANALYSE = "analyse"


@dataclass
class Job:
    """One unit of work handed off from the request cycle."""

    id: str
    kind: JobKind
    dataset_id: str
    status: JobStatus = JobStatus.PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    #: Coarse progress for the analysing screen. Never a fake percentage.
    step: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "dataset_id": self.dataset_id,
            "status": self.status.value,
            "step": self.step,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    step        TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    result      TEXT,
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
    detection    TEXT,
    story        TEXT,
    overrides    TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);

-- Mapping memory (spec 4.1): a source whose fingerprint is already known
-- refreshes silently instead of asking the same questions every time.
CREATE TABLE IF NOT EXISTS mappings (
    fingerprint  TEXT PRIMARY KEY,
    roles        TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- The data quality gate (spec 4.3) needs to know what the last good run
-- looked like, otherwise "the row count halved" is unanswerable.
CREATE TABLE IF NOT EXISTS snapshots (
    fingerprint  TEXT PRIMARY KEY,
    snapshot     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""


class JobStore:
    """SQLite-backed job and dataset storage, safe across threads."""

    def __init__(self, path: str | Path = "busylab.db") -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- datasets ---------------------------------------------------------

    def create_dataset(self, filename: str, path: str) -> str:
        dataset_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO datasets (id, filename, path, created_at) "
                "VALUES (?, ?, ?, ?)",
                (dataset_id, filename, path, _now()),
            )
        return dataset_id

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        for key in ("detection", "story", "overrides"):
            if data.get(key):
                data[key] = json.loads(data[key])
        return data

    def save_detection(
        self, dataset_id: str, detection: dict[str, Any], fingerprint: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET detection = ?, fingerprint = ? WHERE id = ?",
                (json.dumps(detection), fingerprint, dataset_id),
            )

    def save_story(self, dataset_id: str, story: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET story = ? WHERE id = ?",
                (json.dumps(story), dataset_id),
            )

    def save_overrides(self, dataset_id: str, overrides: dict[str, str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE datasets SET overrides = ? WHERE id = ?",
                (json.dumps(overrides), dataset_id),
            )

    # -- mapping memory (spec 4.1) ---------------------------------------

    def remember_mapping(self, fingerprint: str, roles: dict[str, str]) -> None:
        """Store confirmed role assignments against a schema fingerprint.

        Without this, detection re-runs on every refresh and the user
        re-confirms the same ambiguous columns forever, which is a chore on a
        schedule rather than automation.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mappings (fingerprint, roles, created_at) "
                "VALUES (?, ?, ?)",
                (fingerprint, json.dumps(roles), _now()),
            )

    def recall_mapping(self, fingerprint: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT roles FROM mappings WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return json.loads(row["roles"]) if row else None

    # -- quality snapshots (spec 4.3) -------------------------------------

    def remember_snapshot(self, fingerprint: str, snapshot: dict[str, Any]) -> None:
        """Record what a passing run looked like, for the next one to check."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (fingerprint, snapshot, created_at) "
                "VALUES (?, ?, ?)",
                (fingerprint, json.dumps(snapshot), _now()),
            )

    def recall_snapshot(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot FROM snapshots WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return json.loads(row["snapshot"]) if row else None

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
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def claim(self) -> Job | None:
        """Take the oldest pending job, atomically.

        The lock plus the status check in the UPDATE is what stops two workers
        running the same job. In Postgres this becomes SELECT ... FOR UPDATE
        SKIP LOCKED, which is the same idea with better concurrency.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (JobStatus.PENDING.value,),
            ).fetchone()
            if row is None:
                return None
            changed = conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? "
                "WHERE id = ? AND status = ?",
                (JobStatus.RUNNING.value, _now(), row["id"], JobStatus.PENDING.value),
            ).rowcount
            if not changed:
                return None
        job = self._row_to_job(row)
        job.status = JobStatus.RUNNING
        return job

    def set_step(self, job_id: str, step: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET step = ?, updated_at = ? WHERE id = ?",
                (step, _now(), job_id),
            )

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, result = ?, updated_at = ?, step = ? "
                "WHERE id = ?",
                (JobStatus.DONE.value, json.dumps(result), _now(), "done", job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (JobStatus.FAILED.value, error, _now(), job_id),
            )

    def pending_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status = ?",
                (JobStatus.PENDING.value,),
            ).fetchone()
        return int(row["n"])

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            kind=JobKind(row["kind"]),
            dataset_id=row["dataset_id"],
            status=JobStatus(row["status"]),
            step=row["step"] or "",
            payload=json.loads(row["payload"] or "{}"),
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class Worker:
    """Runs jobs off the request cycle.

    A thread here, a separate Render background worker in production (spec 9).
    The handler signature is the same either way, so the deployment topology
    changes without the job code changing.
    """

    def __init__(
        self,
        store: JobStore,
        handlers: dict[JobKind, Callable[[Job, JobStore], dict[str, Any]]],
        *,
        poll_seconds: float = 0.2,
    ) -> None:
        self.store = store
        self.handlers = handlers
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_one(self) -> bool:
        """Process a single job. Returns False when the queue is empty."""
        job = self.store.claim()
        if job is None:
            return False
        handler = self.handlers.get(job.kind)
        if handler is None:
            self.store.fail(job.id, f"No handler for {job.kind.value}")
            return True
        try:
            result = handler(job, self.store)
            self.store.finish(job.id, result)
        except Exception as exc:  # a failed job must not kill the worker
            self.store.fail(job.id, f"{type(exc).__name__}: {exc}")
        return True

    def drain(self, limit: int = 100) -> int:
        """Run everything currently queued. Used by tests and by the CLI."""
        done = 0
        while done < limit and self.run_one():
            done += 1
        return done

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_one():
                self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
