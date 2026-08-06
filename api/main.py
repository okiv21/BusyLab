"""The HTTP surface.

Thin on purpose (spec 8). Every endpoint here does one of three things: accept
a file, hand work to the queue, or serve a cached result. No analysis, no
statistics and no narration logic lives in this module — that all sits in
``busylab``, which is what keeps the engine shippable without the web stack
(spec 9's one non-negotiable).

Endpoint shape follows the screens in the product: upload, check columns,
analysing, story, drill-down.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from busylab import __version__
from busylab.narration import from_env, narrate, route_question
from busylab.narration.routing import answer_from_findings

from .handlers import build_handlers
from .jobs import JobKind, JobStore, JobStatus, Worker

#: Where uploads land. Supabase Storage in production; never the Render disk,
#: which is ephemeral (spec 8).
STORAGE_DIR = Path(os.environ.get("BUSYLAB_STORAGE", "storage"))
DB_PATH = os.environ.get("BUSYLAB_DB", "busylab.db")

#: Accepted input is structured tabular data only (spec 3.1).
ALLOWED_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(
    title="BusyLab API",
    version=__version__,
    description="A thin wrapper around the BusyLab analysis engine.",
)

#: localhost and 127.0.0.1 are different origins to a browser, and Next.js
#: will happily serve on either, so both are allowed by default. Getting this
#: wrong presents as "cannot reach the API" while curl works perfectly.
DEFAULT_CORS = "http://localhost:3000,http://127.0.0.1:3000"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get("BUSYLAB_CORS", DEFAULT_CORS).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: JobStore | None = None
_worker: Worker | None = None


def get_store() -> JobStore:
    global _store
    if _store is None:
        _store = JobStore(DB_PATH)
    return _store


def get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker(get_store(), build_handlers())
    return _worker


@app.on_event("startup")
def _startup() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    get_store()
    # In production this is a separate Render background worker so heavy jobs
    # never block the API. In development one process is simpler.
    if os.environ.get("BUSYLAB_INLINE_WORKER", "1") == "1":
        get_worker().start()


@app.on_event("shutdown")
def _shutdown() -> None:
    if _worker is not None:
        _worker.stop()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class JobRef(BaseModel):
    job_id: str
    dataset_id: str
    status: str


class ConfirmRequest(BaseModel):
    """Answers from the column confirmation screen."""

    roles: dict[str, str] = Field(
        default_factory=dict,
        description="Column name to role, e.g. {'unit_cst': 'cost'}",
    )


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@app.get("/health")
def health(store: JobStore = Depends(get_store)) -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "pending_jobs": store.pending_count(),
        "narration": from_env().name,
    }


@app.post("/uploads", response_model=JobRef, status_code=202)
async def upload(
    file: UploadFile = File(...), store: JobStore = Depends(get_store)
) -> JobRef:
    """Accept a spreadsheet and hand detection to the queue.

    Returns immediately with a job id. Reading and understanding a file can
    take tens of seconds, which does not belong inside an HTTP request.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                "BusyLab reads structured spreadsheets only: "
                f"{', '.join(sorted(ALLOWED_SUFFIXES))}."
            ),
        )

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    dataset_id = store.create_dataset(file.filename or "upload", "")
    target = STORAGE_DIR / f"{dataset_id}{suffix}"

    size = 0
    with target.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="That file is too large.")
            out.write(chunk)

    with store._connect() as conn:  # noqa: SLF001 - same module family
        conn.execute(
            "UPDATE datasets SET path = ? WHERE id = ?", (str(target), dataset_id)
        )

    job = store.enqueue(JobKind.DETECT, dataset_id)
    return JobRef(job_id=job.id, dataset_id=dataset_id, status=job.status.value)


@app.get("/jobs/{job_id}")
def job_status(job_id: str, store: JobStore = Depends(get_store)) -> dict[str, Any]:
    """Poll a job. The frontend drives its progress screen from this."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job.")
    payload = job.to_dict()
    if job.status is JobStatus.DONE:
        payload["result"] = job.result
    return payload


@app.get("/datasets/{dataset_id}/columns")
def get_columns(
    dataset_id: str, store: JobStore = Depends(get_store)
) -> dict[str, Any]:
    """What the detector understood, and what it still needs to ask."""
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    if not dataset.get("detection"):
        raise HTTPException(status_code=409, detail="Still reading this file.")
    return dataset["detection"]


@app.post("/datasets/{dataset_id}/columns", response_model=JobRef, status_code=202)
def confirm_columns(
    dataset_id: str,
    body: ConfirmRequest,
    store: JobStore = Depends(get_store),
) -> JobRef:
    """Accept the user's answers and queue the analysis."""
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")

    from busylab.roles import Role

    for column, role in body.roles.items():
        try:
            Role(role)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown role {role!r}.")

    merged = {**(dataset.get("overrides") or {}), **body.roles}
    store.save_overrides(dataset_id, merged)

    job = store.enqueue(JobKind.ANALYSE, dataset_id)
    return JobRef(job_id=job.id, dataset_id=dataset_id, status=job.status.value)


@app.get("/datasets/{dataset_id}/story")
def get_story(dataset_id: str, store: JobStore = Depends(get_store)) -> dict[str, Any]:
    """The ranked narrative: findings in order, each with its chart."""
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    story = dataset.get("story")
    if not story:
        raise HTTPException(status_code=409, detail="The analysis has not finished.")
    return story


@app.post("/datasets/{dataset_id}/ask")
def ask(
    dataset_id: str, body: AskRequest, store: JobStore = Depends(get_store)
) -> dict[str, Any]:
    """Drill down by asking a question.

    The question is routed to an analysis that has already run. Nothing is
    computed here and no number is produced by a model (spec 6).
    """
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    story = dataset.get("story")
    if not story:
        raise HTTPException(status_code=409, detail="The analysis has not finished.")

    findings = _rebuild_findings(story)
    columns = set(story.get("columns") or [])
    provider = from_env()

    decision = route_question(body.question, findings, provider, columns=columns)
    if not decision.answerable:
        return {
            "answered": False,
            "message": decision.refusal,
            "suggestions": [
                {"name": r.name, "label": r.label} for r in decision.alternatives
            ],
        }

    finding = answer_from_findings(decision, findings)
    if finding is None:
        return {
            "answered": False,
            "message": "That analysis has not run on this data yet.",
            "suggestions": [
                {"name": r.name, "label": r.label} for r in decision.alternatives
            ],
        }

    raw = next((f for f in story["findings"] if f["id"] == finding.id), None)
    return {
        "answered": True,
        "route": {"name": decision.route.name, "label": decision.route.label},
        "confidence": decision.confidence,
        "routed_by": decision.source,
        "finding": raw,
        "answer": narrate(finding, provider).text,
    }


def _rebuild_findings(story: dict[str, Any]) -> list:
    """Reconstruct Finding objects from a cached story.

    Routing needs finding ids and facts, not the full statistical machinery, so
    this rebuilds only what the router reads rather than re-running analysis.
    """
    from busylab.findings import Evidence, Finding, FindingType, Severity

    out = []
    for raw in story.get("findings", []):
        evidence_raw = raw.get("evidence") or {}
        out.append(
            Finding(
                id=raw["id"],
                type=FindingType(raw["type"]),
                summary=raw.get("summary", ""),
                facts=raw.get("facts") or {},
                evidence=Evidence(
                    method=evidence_raw.get("method", ""),
                    p_value=evidence_raw.get("p_value"),
                    adjusted_p=evidence_raw.get("adjusted_p"),
                    sample_size=evidence_raw.get("sample_size"),
                    correction=evidence_raw.get("correction"),
                ),
                severity=Severity(raw.get("severity", "neutral")),
                importance=raw.get("importance", 0.5),
                chart_data=raw.get("chart_data") or {},
            )
        )
    return out


class GoalRequest(BaseModel):
    """A target the business is setting (spec Pillar 4)."""

    metric: str = Field(default="revenue", pattern="^(revenue|profit)$")
    target: float = Field(gt=0)
    start: str
    end: str
    label: str = Field(default="", max_length=80)


@app.get("/datasets/{dataset_id}/goals")
def list_goals(
    dataset_id: str, store: JobStore = Depends(get_store)
) -> dict[str, Any]:
    if store.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    return {"goals": store.list_goals(dataset_id)}


@app.post("/datasets/{dataset_id}/goals", status_code=201)
def create_goal(
    dataset_id: str, body: GoalRequest, store: JobStore = Depends(get_store)
) -> dict[str, Any]:
    """Set a target, and re-run the analysis so the story includes it."""
    if store.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="No such dataset.")

    from busylab.goals import Goal

    try:
        # Validate through the engine's own model rather than duplicating the
        # rules here, so the API cannot accept a goal the engine rejects.
        Goal.from_dict(
            {
                "id": "validate",
                "metric": body.metric,
                "target": body.target,
                "start": body.start,
                "end": body.end,
            }
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    goal = store.add_goal(
        dataset_id, body.metric, body.target, body.start, body.end, body.label
    )
    job = store.enqueue(JobKind.ANALYSE, dataset_id)
    return {"goal": goal, "job_id": job.id}


@app.delete("/datasets/{dataset_id}/goals/{goal_id}", status_code=202)
def delete_goal(
    dataset_id: str, goal_id: str, store: JobStore = Depends(get_store)
) -> dict[str, Any]:
    if not store.delete_goal(dataset_id, goal_id):
        raise HTTPException(status_code=404, detail="No such goal.")
    job = store.enqueue(JobKind.ANALYSE, dataset_id)
    return {"deleted": goal_id, "job_id": job.id}


@app.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, store: JobStore = Depends(get_store)) -> None:
    """Remove an upload and its raw file.

    Raw files accumulate and storage is where cost bites second (spec 9), so
    deleting one has to be possible from day one.
    """
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    path = Path(dataset["path"])
    if path.exists():
        path.unlink()
    with store._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        conn.execute("DELETE FROM jobs WHERE dataset_id = ?", (dataset_id,))
