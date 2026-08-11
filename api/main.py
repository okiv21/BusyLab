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

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from busylab import __version__
from busylab.narration import from_env, narrate, route_question
from busylab.narration.answer import answer_question
from busylab.narration.routing import answer_from_findings

from .handlers import build_handlers
from .jobs import JobKind, JobStatus, Worker, open_store
from .storage import StorageError, store_from_env

log = logging.getLogger(__name__)

#: Where uploads land, and where jobs live. Both are abstractions because a
#: free Render instance has an ephemeral disk: it spins down and comes back
#: with the filesystem empty. Local implementations are for development only
#: (spec 8: never the Render disk).
FILES = store_from_env()

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


def allowed_origins(raw: str | None = None) -> list[str]:
    """Parse BUSYLAB_CORS into origins a browser will actually match.

    An origin is scheme, host and port - nothing else. A trailing slash or a
    path makes the string unequal to the browser's ``Origin`` header, and the
    request is refused with no explanation on either side: the browser reports
    a generic network failure and the server logs a perfectly normal request.
    Since that value is typed by hand into a dashboard, it is normalised here
    rather than trusted.
    """
    from urllib.parse import urlsplit

    origins: list[str] = []
    for entry in (raw if raw is not None else os.environ.get("BUSYLAB_CORS", DEFAULT_CORS)).split(","):
        entry = entry.strip().rstrip("/")
        if not entry:
            continue
        if entry == "*":
            origins.append(entry)
            continue
        parts = urlsplit(entry if "//" in entry else f"https://{entry}")
        if not parts.hostname:
            log.warning("ignoring unparseable BUSYLAB_CORS entry %r", entry)
            continue
        if parts.path:
            log.warning(
                "BUSYLAB_CORS entry %r has a path; using %s://%s instead",
                entry, parts.scheme, parts.netloc,
            )
        origins.append(f"{parts.scheme}://{parts.netloc}")

    # Duplicates are harmless but hide a typo behind a working entry.
    return list(dict.fromkeys(origins))


CORS_ORIGINS = allowed_origins()

# Logged because a CORS mismatch is invisible from both ends at request time.
# Seeing the list at startup is the only cheap way to compare it with the URL
# the browser is actually on.
log.info("CORS allows: %s", ", ".join(CORS_ORIGINS) or "(nothing)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_store = None
_worker: Worker | None = None


def get_store():
    global _store
    if _store is None:
        _store = open_store()
    return _store


def get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker(get_store(), build_handlers(FILES))
    return _worker


@app.on_event("startup")
def _startup() -> None:
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
def health(store = Depends(get_store)) -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "pending_jobs": store.pending_count(),
        "narration": from_env().name,
        "storage": FILES.name,
        # Which bucket, not just which kind of store. A wrong bucket name is
        # the one storage misconfiguration that looks identical to a working
        # setup until the first upload, and it cannot be checked from outside
        # without the service key. The name is configuration, not a secret.
        "bucket": getattr(FILES, "bucket", None),
        # The allowed origins are here because a CORS mismatch is otherwise
        # invisible: the browser reports a generic network error and the
        # server logs an ordinary request. Opening /health in a browser and
        # comparing this list with the address bar settles it in seconds.
        # These are configuration, not secrets - they are public URLs.
        "cors_allows": CORS_ORIGINS,
    }


@app.post("/uploads", response_model=JobRef, status_code=202)
async def upload(
    file: UploadFile = File(...), store = Depends(get_store)
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

    dataset_id = store.create_dataset(file.filename or "upload", "")
    key = f"{dataset_id}{suffix}"

    # Read with a running size check so an oversized upload is refused before
    # it is stored anywhere, rather than after.
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            store.delete_dataset(dataset_id)
            raise HTTPException(status_code=413, detail="That file is too large.")
        chunks.append(chunk)

    try:
        FILES.put(key, b"".join(chunks))
    except StorageError as exc:
        store.delete_dataset(dataset_id)
        raise HTTPException(status_code=503, detail=f"Could not store the file: {exc}")

    store.set_dataset_path(dataset_id, key)
    job = store.enqueue(JobKind.DETECT, dataset_id)
    return JobRef(job_id=job.id, dataset_id=dataset_id, status=job.status.value)


@app.get("/jobs/{job_id}")
def job_status(job_id: str, store = Depends(get_store)) -> dict[str, Any]:
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
    dataset_id: str, store = Depends(get_store)
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
    store = Depends(get_store),
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
def get_story(dataset_id: str, store = Depends(get_store)) -> dict[str, Any]:
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
    dataset_id: str, body: AskRequest, store = Depends(get_store)
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
    routed = answer_from_findings(decision, findings) if decision.answerable else None

    # Answer from the findings themselves rather than only handing back the
    # routed one. Routing alone could only ever return a sentence the reader
    # had already seen in the story, which is why the feature read as a lookup
    # instead of an answer. Every number in the generated text is checked
    # against the findings it cites, and any failure falls back to exactly the
    # routed behaviour - so this cannot answer worse than routing did.
    result = answer_question(body.question, findings, provider, fallback=routed)

    if routed is None and not result.generated:
        # Nothing routed and nothing survived verification: say so rather than
        # dressing up a refusal as an answer.
        return {
            "answered": False,
            "message": decision.refusal,
            "suggestions": [
                {"name": r.name, "label": r.label} for r in decision.alternatives
            ],
        }

    finding = routed
    raw = (
        next((f for f in story["findings"] if f["id"] == finding.id), None)
        if finding is not None
        else None
    )
    return {
        "answered": True,
        "route": (
            {"name": decision.route.name, "label": decision.route.label}
            if decision.route
            else None
        ),
        "confidence": decision.confidence,
        "routed_by": decision.source,
        "finding": raw,
        "answer": result.text,
        # Which findings the answer rests on, so it can be traced back to the
        # computation instead of taken on trust.
        "sources": result.sources,
        "answered_by": result.origin,
        # Suggestions, and the caution that must be shown with them. Separate
        # fields so the frontend cannot render one without the other.
        "advice": result.advice,
        "advice_caution": result.caution,
        # Why a generated answer was discarded. Returned rather than only
        # logged: without it, diagnosing a bad answer meant guessing which of
        # six checks rejected it.
        "rejected": result.rejected,
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


@app.get("/datasets/{dataset_id}/export.{fmt}")
def export_story(
    dataset_id: str, fmt: str, store = Depends(get_store)
) -> Response:
    """Download the story as a PDF or a slide deck (spec Pillar 6).

    Deliberately not MP4: slow, expensive per refresh, and stale the moment the
    data moves. Present mode covers that need inside the app instead.
    """
    if fmt not in {"pdf", "pptx"}:
        raise HTTPException(
            status_code=404, detail="Exports are available as pdf or pptx."
        )

    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    story = dataset.get("story")
    if not story:
        raise HTTPException(status_code=409, detail="The analysis has not finished.")
    if story.get("held"):
        raise HTTPException(
            status_code=409,
            detail="This analysis was held by the data quality gate, so there "
            "is nothing to export.",
        )

    from busylab.export import to_pdf, to_pptx

    findings = _rebuild_findings(story)
    name = Path(dataset["filename"]).stem or "business"

    if fmt == "pdf":
        payload = to_pdf(findings, business_name=name)
        media = "application/pdf"
    else:
        payload = to_pptx(findings, business_name=name)
        media = (
            "application/vnd.openxmlformats-officedocument.presentationml."
            "presentation"
        )

    return Response(
        content=payload,
        media_type=media,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{name}-business-in-review.{fmt}"'
            )
        },
    )


class RecipientRequest(BaseModel):
    """Where this dataset's weekly digest should be sent."""

    email: str = Field(default="", max_length=254)


#: Deliberately permissive. Full RFC 5322 validation rejects addresses that
#: work, and the real check is whether the digest arrives; this only catches
#: obvious typos before they become silent non-delivery.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


@app.put("/datasets/{dataset_id}/recipient", status_code=204)
def set_recipient(
    dataset_id: str, body: RecipientRequest, store = Depends(get_store)
) -> None:
    """Set or clear where this dataset's digest goes.

    Per dataset rather than one global address, so each business receives its
    own numbers. An empty string clears it.

    Note for later: there is no authentication yet, so anyone who knows a
    dataset id can change where its digest is sent. That is acceptable while
    one person is testing against their own data and is not acceptable in
    public - this endpoint needs to sit behind an account before anyone else
    uses the product.
    """
    if store.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="No such dataset.")

    email = body.email.strip()
    if email and not _EMAIL.match(email):
        raise HTTPException(status_code=422, detail="That is not an email address.")

    store.set_recipient(dataset_id, email)


@app.get("/datasets/{dataset_id}/alerts")
def list_alerts(
    dataset_id: str,
    include_acknowledged: bool = False,
    store = Depends(get_store),
) -> dict[str, Any]:
    """What BusyLab noticed without being asked (spec Pillar 2)."""
    if store.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    return {"alerts": store.list_alerts(dataset_id, include_acknowledged=include_acknowledged)}


@app.post("/datasets/{dataset_id}/alerts/{key}/acknowledge", status_code=204)
def acknowledge_alert(
    dataset_id: str, key: str, store = Depends(get_store)
) -> None:
    if not store.acknowledge_alert(dataset_id, key):
        raise HTTPException(status_code=404, detail="No such alert.")


@app.get("/datasets/{dataset_id}/digest")
def preview_digest(
    dataset_id: str, store = Depends(get_store)
) -> dict[str, Any]:
    """The business-in-review digest, rendered but not sent."""
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    story = dataset.get("story")
    if not story:
        raise HTTPException(status_code=409, detail="The analysis has not finished.")

    from busylab.alerts import Alert, AlertKind, AlertLevel
    from busylab.digest import build_digest

    findings = _rebuild_findings(story)
    alerts = [
        Alert(
            kind=AlertKind(raw["kind"]),
            level=AlertLevel(raw["level"]),
            title=raw["title"],
            detail=raw["detail"],
            subject=raw.get("subject", ""),
            period=raw.get("period", ""),
            finding_id=raw.get("finding_id"),
        )
        for raw in store.list_alerts(dataset_id)
    ]
    digest = build_digest(findings, alerts)

    # Who it goes to, and when. Without this the preview was a rendered email
    # with no indication that anything would ever send it, which is why it read
    # as decoration - the machinery behind it was real the whole time.
    from busylab.digest import mailer_from_env

    recipient = (dataset.get("recipient") or "").strip()
    fallback = os.environ.get("BUSYLAB_DIGEST_TO", "").strip()
    mailer = mailer_from_env()

    return {
        **digest.to_dict(),
        "html": digest.to_html(),
        "text": digest.to_text(),
        "delivery": {
            "recipient": recipient or fallback,
            "is_fallback": not recipient and bool(fallback),
            # False means the digest is written to the server log instead, which
            # is a supported setup rather than a failure. LogMailer reports
            # itself as available, so availability is the wrong question - what
            # matters is whether anything reaches an inbox.
            "can_send": mailer.name != "log",
            "mailer": mailer.name,
            # The cron in .github/workflows/scheduled-refresh.yml.
            "schedule": "Mondays at 07:00 UTC, and the 1st of each month",
        },
    }


@app.post("/datasets/{dataset_id}/digest/send", status_code=200)
def send_digest_now(
    dataset_id: str, store = Depends(get_store)
) -> dict[str, Any]:
    """Send this dataset's digest immediately.

    Exists so the preview can be proved rather than trusted. Reading a rendered
    email tells you nothing about whether delivery works, and the alternative
    was waiting until Monday to find out.
    """
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    story = dataset.get("story")
    if not story:
        raise HTTPException(status_code=409, detail="The analysis has not finished.")

    recipient = (dataset.get("recipient") or "").strip()
    if not recipient and not os.environ.get("BUSYLAB_DIGEST_TO", "").strip():
        raise HTTPException(
            status_code=400,
            detail="Set an address for this dataset first.",
        )

    from busylab.alerts import Alert, AlertKind, AlertLevel
    from busylab.digest import build_digest, mailer_from_env, send_digest

    findings = _rebuild_findings(story)
    alerts = [
        Alert(
            kind=AlertKind(raw["kind"]),
            level=AlertLevel(raw["level"]),
            title=raw["title"],
            detail=raw["detail"],
            subject=raw.get("subject", ""),
            period=raw.get("period", ""),
            finding_id=raw.get("finding_id"),
        )
        for raw in store.list_alerts(dataset_id)
    ]
    digest = build_digest(findings, alerts)
    if digest.is_empty:
        return {
            "sent": False,
            "detail": "There is nothing worth emailing yet.",
            "recipient": recipient,
        }

    mailer = mailer_from_env()
    to = recipient or os.environ.get("BUSYLAB_DIGEST_TO", "").strip()
    delivered = send_digest(digest, to, mailer)

    if mailer.name == "log":
        # Reporting this as sent would be a lie: it went to the server log.
        return {
            "sent": False,
            "detail": (
                "No mail server is configured, so the digest was written to the "
                "server log instead of being emailed."
            ),
            "recipient": to,
        }
    return {
        "sent": delivered,
        "detail": (
            f"Sent to {to}." if delivered else "The mail server refused it."
        ),
        "recipient": to,
    }


#: Shared secret for the external scheduler. Render's free tier spins down, so
#: scheduled work is triggered from outside rather than by a timer in-process
#: (spec 8 and 9). Without a secret set, the endpoint refuses to run at all
#: rather than sitting open.
SCHEDULER_TOKEN = os.environ.get("BUSYLAB_SCHEDULER_TOKEN", "")


@app.post("/internal/tick", status_code=202)
def scheduled_tick(
    token: str = "",
    store = Depends(get_store),
) -> dict[str, Any]:
    """Re-analyse every known dataset, so monitoring is proactive.

    Hit by GitHub Actions on a cron. A free Render web service spins down after
    inactivity and so cannot host a reliable timer, which is why the trigger
    lives outside the app entirely.
    """
    if not SCHEDULER_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="No scheduler token is configured, so scheduled runs are off.",
        )
    # compare_digest to avoid leaking the token a character at a time.
    import hmac

    if not hmac.compare_digest(token, SCHEDULER_TOKEN):
        raise HTTPException(status_code=401, detail="Bad scheduler token.")

    queued = []
    for dataset_id in store.all_dataset_ids():
        # The flag is what separates a scheduled run from a manual one. Only
        # scheduled runs email a digest; re-analysing because someone set a
        # goal must not.
        job = store.enqueue(
            JobKind.ANALYSE, dataset_id, payload={"send_digest": True}
        )
        queued.append({"dataset_id": dataset_id, "job_id": job.id})

    return {"queued": len(queued), "jobs": queued}


@app.post("/internal/test-email", status_code=200)
def test_email(token: str = "", to: str = "") -> dict[str, Any]:
    """Send one throwaway digest, to prove the SMTP settings work.

    Getting mail credentials right is trial and error - the wrong key, an
    unverified sender, a trailing space - and the normal feedback loop is to
    wait for a cron and then read worker logs. This closes that loop: it tests
    the settings on the *deployed* service, which is the only place they
    matter, and returns the provider's own error rather than a generic failure.

    Behind the scheduler token so it cannot be used to send mail at will.
    """
    if not SCHEDULER_TOKEN:
        raise HTTPException(status_code=503, detail="No scheduler token is set.")

    import hmac

    if not hmac.compare_digest(token, SCHEDULER_TOKEN):
        raise HTTPException(status_code=401, detail="Bad scheduler token.")

    recipient = (to or os.environ.get("BUSYLAB_DIGEST_TO", "")).strip()
    if not recipient:
        raise HTTPException(
            status_code=422,
            detail="Pass ?to=you@example.com, or set BUSYLAB_DIGEST_TO.",
        )

    from busylab.digest import Digest, mailer_from_env

    mailer = mailer_from_env()
    sample = Digest(
        period_label="test message",
        headline=(
            "If you are reading this, BusyLab can send your weekly digest."
        ),
        lines=["The real one arrives on Mondays, and only when there is "
               "something worth saying."],
    )

    delivered = mailer.send(recipient, sample)
    return {
        "sent": delivered,
        "mailer": mailer.name,
        "to": recipient,
        "hint": (
            "Delivered. Check the inbox, and the spam folder."
            if delivered and mailer.name != "log"
            else "No mailer is configured, so this was written to the worker "
            "log instead of sent."
            if mailer.name == "log"
            else "The provider refused it. The worker log carries the reason - "
            "usually an unverified sender, or the API key used in place of "
            "the SMTP key."
        ),
    }


class GoalRequest(BaseModel):
    """A target the business is setting (spec Pillar 4)."""

    metric: str = Field(default="revenue", pattern="^(revenue|profit)$")
    target: float = Field(gt=0)
    start: str
    end: str
    label: str = Field(default="", max_length=80)


@app.get("/datasets/{dataset_id}/goals")
def list_goals(
    dataset_id: str, store = Depends(get_store)
) -> dict[str, Any]:
    """Every target, with where it actually stands.

    The stored target on its own is what the panel used to show, and a target
    with no measurement beside it looks exactly like a target nothing is
    tracking. The progress is computed here so the panel can always say
    something - including when the answer is "this window has not begun".
    """
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")

    goals = store.list_goals(dataset_id)
    story = dataset.get("story")
    if not goals or not story:
        return {"goals": goals, "progress": []}

    progress: list[dict[str, Any]] = []
    for raw in goals:
        finding = next(
            (
                f
                for f in story.get("findings", [])
                if f.get("id") == f"goal_{raw['id']}"
            ),
            None,
        )
        if finding is None:
            continue
        progress.append(
            {
                "goal_id": raw["id"],
                "says": finding.get("summary", ""),
                "meaning": finding.get("meaning", ""),
                "severity": finding.get("severity", "neutral"),
                "facts": finding.get("facts", {}),
            }
        )

    return {"goals": goals, "progress": progress}


@app.post("/datasets/{dataset_id}/goals", status_code=201)
def create_goal(
    dataset_id: str, body: GoalRequest, store = Depends(get_store)
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
    dataset_id: str, goal_id: str, store = Depends(get_store)
) -> dict[str, Any]:
    if not store.delete_goal(dataset_id, goal_id):
        raise HTTPException(status_code=404, detail="No such goal.")
    job = store.enqueue(JobKind.ANALYSE, dataset_id)
    return {"deleted": goal_id, "job_id": job.id}


@app.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, store = Depends(get_store)) -> None:
    """Remove an upload and its raw file.

    Raw files accumulate and storage is where cost bites second (spec 9), so
    deleting one has to be possible from day one.
    """
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset.")
    key = dataset.get("path") or ""
    if key:
        FILES.delete(key)
    store.delete_dataset(dataset_id)
