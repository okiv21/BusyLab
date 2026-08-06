"""One contract, two backends.

SQLite is right for development and wrong for production here: a free Render
instance has an ephemeral disk, so the database file is gone after every
spin-down and every dataset, goal and alert vanishes without an error. Postgres
(Supabase, per spec 8) is what actually persists.

Two implementations of one interface drift the moment nothing is checking, so
every assertion below runs against both. The Postgres pass is skipped unless a
``TEST_DATABASE_URL`` is set, because a database is not something a test suite
should assume; the SQLite pass always runs, which at least keeps the contract
itself honest.

Run the Postgres side with:

    TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_stores.py
"""

from __future__ import annotations

import os

import pytest

from api.jobs import JobKind, JobStatus, JobStore, open_store
from api.storage import LocalFileStore, StorageError

PG_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    """The same contract, whichever backend is available."""
    if request.param == "sqlite":
        yield JobStore(tmp_path / "contract.db")
        return

    if not PG_URL:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres contract")

    from api.pg import PostgresJobStore

    pg = PostgresJobStore(PG_URL)
    # Start from empty so the assertions below are about this test only.
    with pg._connect() as conn:  # noqa: SLF001 - test setup
        for table in ("alerts", "goals", "snapshots", "mappings", "jobs", "datasets"):
            conn.execute(f"TRUNCATE TABLE {table}")
    yield pg


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------


def test_a_dataset_round_trips(store) -> None:
    dataset_id = store.create_dataset("sales.xlsx", "")
    store.set_dataset_path(dataset_id, f"{dataset_id}.xlsx")

    dataset = store.get_dataset(dataset_id)
    assert dataset["filename"] == "sales.xlsx"
    assert dataset["path"] == f"{dataset_id}.xlsx"


def test_an_unknown_dataset_is_none(store) -> None:
    assert store.get_dataset("nope") is None


def test_detection_and_story_are_stored_as_structures(store) -> None:
    """Both backends must hand back dicts, not JSON strings."""
    dataset_id = store.create_dataset("s.xlsx", "k")
    store.save_detection(dataset_id, {"ready": True, "prompts": []}, "fp1")
    store.save_story(dataset_id, {"findings": [{"id": "x"}], "held": False})

    dataset = store.get_dataset(dataset_id)
    assert dataset["detection"]["ready"] is True
    assert dataset["story"]["findings"][0]["id"] == "x"
    assert dataset["fingerprint"] == "fp1"


def test_overrides_round_trip(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    store.save_overrides(dataset_id, {"unit_cst": "cost"})
    assert store.get_dataset(dataset_id)["overrides"] == {"unit_cst": "cost"}


def test_only_analysed_datasets_are_swept_by_the_scheduler(store) -> None:
    pending = store.create_dataset("pending.xlsx", "k1")
    done = store.create_dataset("done.xlsx", "k2")
    store.save_story(done, {"findings": []})

    ids = store.all_dataset_ids()
    assert done in ids
    assert pending not in ids


def test_deleting_a_dataset_takes_its_jobs(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    store.enqueue(JobKind.DETECT, dataset_id)
    store.delete_dataset(dataset_id)

    assert store.get_dataset(dataset_id) is None
    assert store.pending_count() == 0


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def test_a_job_is_queued_then_claimed_then_finished(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    job = store.enqueue(JobKind.ANALYSE, dataset_id)

    assert store.get(job.id).status is JobStatus.PENDING
    assert store.pending_count() == 1

    claimed = store.claim()
    assert claimed is not None and claimed.id == job.id
    assert store.get(job.id).status is JobStatus.RUNNING

    store.finish(job.id, {"findings": []})
    finished = store.get(job.id)
    assert finished.status is JobStatus.DONE
    assert finished.result == {"findings": []}


def test_a_job_is_only_claimed_once(store) -> None:
    """Two workers must never run the same job."""
    dataset_id = store.create_dataset("s.xlsx", "k")
    store.enqueue(JobKind.DETECT, dataset_id)

    assert store.claim() is not None
    assert store.claim() is None


def test_claiming_an_empty_queue_returns_nothing(store) -> None:
    assert store.claim() is None


def test_a_failed_job_keeps_its_error(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    job = store.enqueue(JobKind.DETECT, dataset_id)
    store.claim()
    store.fail(job.id, "ValueError: nope")

    failed = store.get(job.id)
    assert failed.status is JobStatus.FAILED
    assert "nope" in failed.error


def test_a_job_reports_its_step(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    job = store.enqueue(JobKind.ANALYSE, dataset_id)
    store.set_step(job.id, "checking against normal variation")

    assert store.get(job.id).step == "checking against normal variation"


def test_jobs_are_claimed_oldest_first(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    first = store.enqueue(JobKind.DETECT, dataset_id)
    store.enqueue(JobKind.ANALYSE, dataset_id)

    assert store.claim().id == first.id


# --------------------------------------------------------------------------
# Mapping memory, snapshots, goals, alerts
# --------------------------------------------------------------------------


def test_a_mapping_is_remembered_and_replaced(store) -> None:
    store.remember_mapping("fp", {"a": "date"})
    assert store.recall_mapping("fp") == {"a": "date"}

    store.remember_mapping("fp", {"a": "product"})
    assert store.recall_mapping("fp") == {"a": "product"}


def test_an_unknown_mapping_is_none(store) -> None:
    assert store.recall_mapping("missing") is None


def test_a_snapshot_is_remembered_and_replaced(store) -> None:
    store.remember_snapshot("fp", {"rows": 100})
    assert store.recall_snapshot("fp")["rows"] == 100

    store.remember_snapshot("fp", {"rows": 250})
    assert store.recall_snapshot("fp")["rows"] == 250


def test_goals_are_listed_and_deleted(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    goal = store.add_goal(dataset_id, "revenue", 5000.0, "2025-01-01", "2025-12-31", "Q1")

    goals = store.list_goals(dataset_id)
    assert len(goals) == 1
    assert goals[0]["target"] == 5000.0
    assert goals[0]["label"] == "Q1"

    assert store.delete_goal(dataset_id, goal["id"]) is True
    assert store.list_goals(dataset_id) == []
    assert store.delete_goal(dataset_id, goal["id"]) is False


def test_an_alert_is_recorded_once(store) -> None:
    """The dedupe key is what stops a weekly scheduler re-sending."""
    dataset_id = store.create_dataset("s.xlsx", "k")
    alert = {"key": "abc", "title": "Revenue fell", "level": "high"}

    assert store.record_alerts(dataset_id, [alert]) == 1
    assert store.record_alerts(dataset_id, [alert]) == 0
    assert store.sent_alert_keys(dataset_id) == {"abc"}


def test_alerts_are_acknowledged_not_deleted(store) -> None:
    dataset_id = store.create_dataset("s.xlsx", "k")
    store.record_alerts(dataset_id, [{"key": "abc", "title": "t", "level": "high"}])

    assert len(store.list_alerts(dataset_id)) == 1
    assert store.acknowledge_alert(dataset_id, "abc") is True
    assert store.list_alerts(dataset_id) == []
    assert len(store.list_alerts(dataset_id, include_acknowledged=True)) == 1


def test_alerts_are_scoped_to_their_dataset(store) -> None:
    one = store.create_dataset("a.xlsx", "k1")
    two = store.create_dataset("b.xlsx", "k2")
    store.record_alerts(one, [{"key": "shared", "title": "t", "level": "high"}])

    assert store.sent_alert_keys(one) == {"shared"}
    assert store.sent_alert_keys(two) == set()


# --------------------------------------------------------------------------
# The factory
# --------------------------------------------------------------------------


def test_both_stores_expose_the_same_interface() -> None:
    """Catches drift without needing a database to be running.

    The behavioural contract above only runs against Postgres when a URL is
    configured, so this is the check that always runs: same public methods,
    same signatures. A method added to one store and forgotten on the other
    fails here rather than at 3am in production.
    """
    import inspect

    from api.pg import PostgresJobStore

    def surface(cls) -> dict[str, list[str]]:
        out = {}
        for name, member in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_"):
                continue
            params = list(inspect.signature(member).parameters)
            out[name] = params
        return out

    sqlite_surface = surface(JobStore)
    pg_surface = surface(PostgresJobStore)

    missing = set(sqlite_surface) - set(pg_surface)
    assert not missing, f"Postgres store is missing: {sorted(missing)}"

    extra = set(pg_surface) - set(sqlite_surface)
    assert not extra, f"SQLite store is missing: {sorted(extra)}"

    for name, params in sqlite_surface.items():
        assert pg_surface[name] == params, (
            f"{name} differs: sqlite{params} vs postgres{pg_surface[name]}"
        )


def test_the_factory_picks_sqlite_without_a_database_url(tmp_path) -> None:
    assert isinstance(open_store(dsn="", sqlite_path=tmp_path / "x.db"), JobStore)


@pytest.mark.skipif(not PG_URL, reason="needs TEST_DATABASE_URL")
def test_the_factory_picks_postgres_with_one() -> None:
    from api.pg import PostgresJobStore

    assert isinstance(open_store(dsn=PG_URL), PostgresJobStore)


# --------------------------------------------------------------------------
# File storage
# --------------------------------------------------------------------------


def test_a_file_round_trips(tmp_path) -> None:
    files = LocalFileStore(root=tmp_path)
    files.put("a/b.xlsx", b"hello")

    assert files.exists("a/b.xlsx")
    assert files.get("a/b.xlsx") == b"hello"

    files.delete("a/b.xlsx")
    assert not files.exists("a/b.xlsx")


def test_reading_a_missing_file_raises(tmp_path) -> None:
    with pytest.raises(StorageError):
        LocalFileStore(root=tmp_path).get("nope.xlsx")


def test_a_key_cannot_escape_the_store(tmp_path) -> None:
    """Keys are ours, but a traversal would read arbitrary files."""
    files = LocalFileStore(root=tmp_path / "inner")
    with pytest.raises(StorageError):
        files.put("../../escaped.txt", b"x")


def test_supabase_reports_itself_unavailable_without_credentials() -> None:
    from api.storage import SupabaseFileStore

    assert not SupabaseFileStore(url="", key="").available()


def test_uploads_carry_their_real_content_type() -> None:
    """A bucket that restricts MIME types rejects octet-stream.

    Every accepted extension must map to the type Supabase expects, or
    switching on the bucket's MIME restriction silently breaks all uploads.
    """
    from api.main import ALLOWED_SUFFIXES
    from api.storage import content_type_for

    for suffix in ALLOWED_SUFFIXES:
        resolved = content_type_for(f"dataset{suffix}")
        assert resolved != "application/octet-stream", suffix
        assert "/" in resolved


def test_an_unknown_extension_falls_back_to_a_generic_type() -> None:
    from api.storage import content_type_for

    assert content_type_for("x.bin") == "application/octet-stream"


def test_the_file_store_factory_defaults_to_local(monkeypatch) -> None:
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(key, raising=False)
    from api.storage import store_from_env

    assert store_from_env().name == "local"
