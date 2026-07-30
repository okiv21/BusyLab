"""API tests.

These drive the API the way the frontend will: upload, poll, read the columns,
answer the questions, get the story, ask a follow-up. If this sequence works,
the UI has everything it needs.

The worker runs inline and is drained explicitly rather than slept on, so the
tests are deterministic and fast.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import jobs as jobs_module
from api.handlers import build_handlers
from api.jobs import JobStore, Worker

from . import fixtures


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh API with its own database, storage and a manual worker."""
    monkeypatch.setenv("BUSYLAB_INLINE_WORKER", "0")  # tests drain by hand
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    from api import main

    store = JobStore(tmp_path / "test.db")
    worker = Worker(store, build_handlers())

    monkeypatch.setattr(main, "_store", store)
    monkeypatch.setattr(main, "_worker", worker)
    monkeypatch.setattr(main, "STORAGE_DIR", tmp_path / "storage")

    with TestClient(main.app) as test_client:
        test_client.worker = worker
        test_client.store = store
        yield test_client


@pytest.fixture
def clean_file(tmp_path):
    """A well-formed file with real effects in it.

    ``salesperson`` is dropped: it exists in the fixture to prove the engine
    ignores noise, but as an unrecognised column it legitimately raises a
    group-by prompt, which would make this file not "clean".

    Kept at full length. Shortening it makes the planted channel decline fall
    below the threshold at which it is a finding, so the API tests would then
    be asserting against data that genuinely has less in it.
    """
    path = tmp_path / "clean_sales.xlsx"
    frame = fixtures.planted_business().drop(columns=["salesperson"])
    frame.to_excel(path, index=False)
    return path


@pytest.fixture
def messy_file(tmp_path):
    path = tmp_path / "messy.xlsx"
    fixtures.write_messy_workbook(path, n=150)
    return path


def _upload(client, path):
    with open(path, "rb") as handle:
        response = client.post(
            "/uploads",
            files={
                "file": (
                    path.name,
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert response.status_code == 202, response.text
    return response.json()


# --------------------------------------------------------------------------
# The shape of the thing
# --------------------------------------------------------------------------


def test_health_reports_readiness(client) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["version"]
    assert body["narration"] == "none", "no key configured in tests"


def test_upload_returns_immediately_with_a_job(client, clean_file) -> None:
    """Analysis must not run inside the request (spec 9)."""
    body = _upload(client, clean_file)

    assert body["status"] == "pending"
    assert body["job_id"] and body["dataset_id"]
    assert client.store.pending_count() == 1, "work was queued, not executed"


def test_unsupported_file_types_are_refused(client, tmp_path) -> None:
    """Structured tabular data only: no screenshots, no notes (spec 3.1)."""
    path = tmp_path / "receipt.png"
    path.write_bytes(b"not a spreadsheet")
    with open(path, "rb") as handle:
        response = client.post(
            "/uploads", files={"file": ("receipt.png", handle, "image/png")}
        )
    assert response.status_code == 415
    assert "spreadsheet" in response.json()["detail"].lower()


def test_job_polling_reports_progress_then_result(client, clean_file) -> None:
    upload = _upload(client, clean_file)

    pending = client.get(f"/jobs/{upload['job_id']}").json()
    assert pending["status"] == "pending"

    client.worker.drain()

    done = client.get(f"/jobs/{upload['job_id']}").json()
    assert done["status"] == "done"
    assert done["result"]["rows"] > 0


def test_unknown_job_is_a_404(client) -> None:
    assert client.get("/jobs/nope").status_code == 404


# --------------------------------------------------------------------------
# The flow a UI will drive
# --------------------------------------------------------------------------


def test_clean_file_needs_no_confirmation(client, clean_file) -> None:
    upload = _upload(client, clean_file)
    client.worker.drain()

    columns = client.get(f"/datasets/{upload['dataset_id']}/columns").json()

    assert columns["ready"] is True
    assert columns["prompts"] == []
    assert {c["role"] for c in columns["confirmed"]} >= {"date", "product", "revenue"}


def test_columns_response_carries_locked_tiers(client, clean_file) -> None:
    """Drives the greyed-out "add a cost column" prompts (spec 3.4)."""
    upload = _upload(client, clean_file)
    client.worker.drain()

    columns = client.get(f"/datasets/{upload['dataset_id']}/columns").json()
    tiers = {t["tier"]: t for t in columns["tiers"]}

    assert tiers["core"]["unlocked"] is True
    assert all("label" in t for t in columns["tiers"])


def test_messy_file_asks_only_about_its_own_mess(client, messy_file) -> None:
    upload = _upload(client, messy_file)
    client.worker.drain()

    columns = client.get(f"/datasets/{upload['dataset_id']}/columns").json()
    assert len(columns["prompts"]) <= 3
    for prompt in columns["prompts"]:
        assert prompt["question"]
        assert "column" in prompt


def test_columns_before_detection_finishes_is_a_conflict(client, clean_file) -> None:
    upload = _upload(client, clean_file)
    response = client.get(f"/datasets/{upload['dataset_id']}/columns")
    assert response.status_code == 409


def test_full_flow_upload_to_story(client, clean_file) -> None:
    """The whole product, over HTTP."""
    upload = _upload(client, clean_file)
    dataset_id = upload["dataset_id"]
    client.worker.drain()

    columns = client.get(f"/datasets/{dataset_id}/columns").json()
    assert columns["ready"]

    confirm = client.post(f"/datasets/{dataset_id}/columns", json={"roles": {}})
    assert confirm.status_code == 202
    client.worker.drain()

    story = client.get(f"/datasets/{dataset_id}/story").json()

    assert story["findings"], "a real business must produce findings"
    assert story["findings"][0]["chart"], "every finding carries its chart"
    assert story["chips"], "guided follow-ups are offered"


def test_story_is_ranked_most_important_first(client, clean_file) -> None:
    upload = _upload(client, clean_file)
    client.worker.drain()
    client.post(f"/datasets/{upload['dataset_id']}/columns", json={"roles": {}})
    client.worker.drain()

    story = client.get(f"/datasets/{upload['dataset_id']}/story").json()
    ids = [f["id"] for f in story["findings"]]

    assert ids[-1] == "product_ranking", "commodity ranking sits last"
    assert story["findings"][0]["severity"] in ("urgent", "watch")


def test_findings_keep_their_evidence_over_http(client, clean_file) -> None:
    upload = _upload(client, clean_file)
    client.worker.drain()
    client.post(f"/datasets/{upload['dataset_id']}/columns", json={"roles": {}})
    client.worker.drain()

    story = client.get(f"/datasets/{upload['dataset_id']}/story").json()
    for finding in story["findings"]:
        assert finding["evidence"]["method"]
        assert "strength" in finding["evidence"]


def test_confirmation_overrides_are_applied(client, messy_file) -> None:
    upload = _upload(client, messy_file)
    dataset_id = upload["dataset_id"]
    client.worker.drain()

    response = client.post(
        f"/datasets/{dataset_id}/columns", json={"roles": {"Cost": "cost"}}
    )
    assert response.status_code == 202
    client.worker.drain()

    columns_after = client.store.get_dataset(dataset_id)["overrides"]
    assert columns_after["Cost"] == "cost"


def test_unknown_role_is_rejected(client, clean_file) -> None:
    upload = _upload(client, clean_file)
    client.worker.drain()
    response = client.post(
        f"/datasets/{upload['dataset_id']}/columns",
        json={"roles": {"quantity": "wingspan"}},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Drill-down
# --------------------------------------------------------------------------


@pytest.fixture
def analysed(client, clean_file):
    upload = _upload(client, clean_file)
    client.worker.drain()
    client.post(f"/datasets/{upload['dataset_id']}/columns", json={"roles": {}})
    client.worker.drain()
    return upload["dataset_id"]


def test_a_question_is_answered_from_computed_analysis(client, analysed) -> None:
    response = client.post(
        f"/datasets/{analysed}/ask", json={"question": "why did revenue drop?"}
    )
    body = response.json()

    assert body["answered"] is True
    assert body["finding"]["id"]
    assert body["answer"]


def test_a_channel_question_reaches_the_channel_analysis(client, analysed) -> None:
    body = client.post(
        f"/datasets/{analysed}/ask", json={"question": "what about online?"}
    ).json()

    assert body["answered"] is True
    assert "channel" in body["route"]["name"]


def test_an_unanswerable_question_is_refused_with_suggestions(
    client, analysed
) -> None:
    """A question the data cannot speak to is refused, not guessed at.

    Phrased with no analysis vocabulary in it at all. With no model configured
    the fallback is keyword matching, which is deliberately permissive: a
    question containing "margin" will reach the profit analysis even if it was
    asking about a competitor. That is the documented cost of running without
    a model, and the model path is covered in test_narration.
    """
    body = client.post(
        f"/datasets/{analysed}/ask",
        json={"question": "how many staff should I hire?"},
    ).json()

    assert body["answered"] is False
    assert body["suggestions"]


def test_asking_before_analysis_is_a_conflict(client, clean_file) -> None:
    upload = _upload(client, clean_file)
    client.worker.drain()
    response = client.post(
        f"/datasets/{upload['dataset_id']}/ask", json={"question": "why?"}
    )
    assert response.status_code == 409


def test_empty_question_is_rejected(client, analysed) -> None:
    assert (
        client.post(f"/datasets/{analysed}/ask", json={"question": ""}).status_code
        == 422
    )


# --------------------------------------------------------------------------
# Mapping memory (spec 4.1)
# --------------------------------------------------------------------------


def test_a_known_schema_refreshes_without_asking_again(client, clean_file) -> None:
    """The point of mapping memory: a refresh is silent, not a chore."""
    first = _upload(client, clean_file)
    client.worker.drain()
    client.post(f"/datasets/{first['dataset_id']}/columns", json={"roles": {}})
    client.worker.drain()

    second = _upload(client, clean_file)
    client.worker.drain()

    columns = client.get(f"/datasets/{second['dataset_id']}/columns").json()
    assert columns["reused_mapping"] is True
    assert columns["prompts"] == []


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def test_dataset_can_be_deleted_with_its_raw_file(client, clean_file) -> None:
    """Raw files accumulate and storage is where cost bites (spec 9)."""
    upload = _upload(client, clean_file)
    client.worker.drain()
    path = client.store.get_dataset(upload["dataset_id"])["path"]

    from pathlib import Path

    assert Path(path).exists()
    assert client.delete(f"/datasets/{upload['dataset_id']}").status_code == 204
    assert not Path(path).exists()
    assert client.store.get_dataset(upload["dataset_id"]) is None


def test_a_failing_job_records_its_error(client, tmp_path) -> None:
    """One bad upload must not take the worker down."""
    broken = tmp_path / "broken.csv"
    broken.write_text("this,is\nnot,really\na,table\n")

    upload = _upload(client, broken)
    client.worker.drain()

    job = client.get(f"/jobs/{upload['job_id']}").json()
    assert job["status"] in {"done", "failed"}
    if job["status"] == "failed":
        assert job["error"]


def test_worker_survives_a_failed_job_and_keeps_going(client, clean_file, tmp_path):
    broken = tmp_path / "broken.csv"
    broken.write_text("just one column\n1\n2\n")

    _upload(client, broken)
    good = _upload(client, clean_file)
    client.worker.drain()

    assert client.get(f"/jobs/{good['job_id']}").json()["status"] == "done"


# --------------------------------------------------------------------------
# The API stays thin (spec 9)
# --------------------------------------------------------------------------


def test_engine_does_not_import_the_web_stack() -> None:
    """The engine must ship without dragging FastAPI along."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import busylab, busylab.analysis, busylab.narration, sys; "
            "assert 'fastapi' not in sys.modules; "
            "assert 'api' not in sys.modules; print('clean')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
