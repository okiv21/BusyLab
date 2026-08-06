"""What the worker actually runs.

This is the entire seam between the web layer and the engine. Everything here
loads a file, calls into ``busylab``, and writes the result back to the store;
no analysis logic lives on this side of the line (spec 9: the API is a thin
wrapper around the engine, never the reverse).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from busylab import loading
from busylab.alerts import build_alerts
from busylab.digest import build_digest, send_digest
from busylab.analysis import analyse
from busylab.detection import detect
from busylab.detection.engine import ConfirmationPrompt, DetectionResult
from busylab.goals import Goal
from busylab.narration import from_env, narrate, suggest_chips
from busylab.roles import ROLE_SPECS, TIER_SPECS, Role

from .jobs import Job, JobStore
from .storage import FileStore, store_from_env


def _prompt_to_dict(prompt: ConfirmationPrompt) -> dict[str, Any]:
    """Shape a prompt for the column-confirmation screen."""
    return {
        "column": prompt.column,
        "question": prompt.question,
        "reason": prompt.reason,
        "suggested": prompt.suggested.value if prompt.suggested else None,
        "suggested_label": prompt.suggested_label,
        "options": [
            {"role": role.value, "label": ROLE_SPECS[role].label}
            for role in prompt.options
        ],
        "allow_ignore": prompt.allow_ignore,
        "allow_group_by": prompt.allow_group_by,
    }


def detection_to_dict(result: DetectionResult, rows: int) -> dict[str, Any]:
    """Everything the confirm screen needs, and nothing it does not."""
    confirmed = []
    for role, column in sorted(result.assignments.items(), key=lambda kv: kv[0].value):
        verdict = next((v for v in result.verdicts if v.column == column), None)
        if verdict is None or verdict.status != "confident":
            continue
        confirmed.append(
            {
                "role": role.value,
                "label": ROLE_SPECS[role].label,
                "column": column,
                "confidence": round(verdict.confidence, 3),
                "reason": verdict.reason,
            }
        )

    return {
        "rows": rows,
        "fingerprint": result.fingerprint,
        "ready": result.ready,
        "confirmed": confirmed,
        "prompts": [_prompt_to_dict(p) for p in result.prompts],
        "unknown_columns": result.unknown_columns,
        "missing": [
            {"role": r.value, "label": ROLE_SPECS[r].label} for r in sorted(
                result.missing, key=lambda r: r.value
            )
        ],
        "tiers": [
            {
                "tier": tier.value,
                "label": TIER_SPECS[tier].label,
                "unlocked": unlocked,
                "locked_prompt": TIER_SPECS[tier].locked_prompt,
            }
            for tier, unlocked in result.tiers.items()
        ],
        "notes": result.notes,
        "cost_basis": result.cost_basis,
    }


def _load(store: JobStore, dataset_id: str, files: FileStore):
    """Fetch a dataset's bytes and parse them.

    The file store is passed in rather than rebuilt here. Deriving it from the
    environment inside the handler would give the worker a different store from
    the one the API is writing to - which happens to work in production, where
    both read the same variables, and is simply wrong.

    Bytes come from that store rather than the filesystem, because in
    production they live in Supabase and there is no local path at all. The
    temporary file exists only because pandas reads paths.
    """
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise ValueError(f"Unknown dataset {dataset_id}")

    key = dataset.get("path") or ""
    if not key:
        raise ValueError("This dataset has no stored file.")

    suffix = Path(key).suffix or ".xlsx"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(files.get(key))
        temp_path = Path(handle.name)

    try:
        frame, report = loading.load(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    if frame.empty:
        raise ValueError("No usable table was found in this file.")
    return dataset, frame, report


def _overrides_from(raw: dict[str, str] | None) -> dict[str, Role]:
    """Turn stored role names back into roles, ignoring anything unknown."""
    out: dict[str, Role] = {}
    for column, role_name in (raw or {}).items():
        try:
            out[column] = Role(role_name)
        except ValueError:
            continue
    return out


def handle_detect(job: Job, store: JobStore, files: FileStore) -> dict[str, Any]:
    """Read the file and work out what its columns mean."""
    store.set_step(job.id, "reading the file")
    dataset, frame, report = _load(store, job.dataset_id, files)

    store.set_step(job.id, "checking the columns")
    overrides = _overrides_from(dataset.get("overrides"))

    # Mapping memory: a schema we have already confirmed runs silently
    # rather than asking the same questions again (spec 4.1).
    from busylab.detection import schema_fingerprint

    fingerprint = schema_fingerprint(frame)
    remembered = store.recall_mapping(fingerprint)
    reused = False
    if remembered and not overrides:
        overrides = _overrides_from(remembered)
        reused = True

    result = detect(frame, overrides=overrides)
    payload = detection_to_dict(result, rows=int(len(frame)))
    payload["loader"] = {
        "summary": report.summary(),
        "sheets_used": report.sheets_used,
        "dropped_total_rows": report.dropped_total_rows,
        "notes": report.notes,
    }
    payload["reused_mapping"] = reused

    store.save_detection(job.dataset_id, payload, result.fingerprint)
    return payload


def handle_analyse(job: Job, store: JobStore, files: FileStore) -> dict[str, Any]:
    """Run the analysis and narrate it."""
    store.set_step(job.id, "cleaning up the rows")
    dataset, frame, _ = _load(store, job.dataset_id, files)

    overrides = _overrides_from(dataset.get("overrides"))
    result = detect(frame, overrides=overrides)

    if result.missing:
        missing = ", ".join(ROLE_SPECS[r].label for r in result.missing)
        raise ValueError(f"Cannot analyse without: {missing}")

    # A confirmed mapping is worth remembering for the next refresh.
    store.remember_mapping(
        result.fingerprint,
        {column: role.value for role, column in result.assignments.items()},
    )

    store.set_step(job.id, "checking the data is sound")
    # The gate compares against the last run that passed for this schema, so a
    # halved row count or a shifted currency is visible rather than analysed.
    previous = store.recall_snapshot(result.fingerprint)

    store.set_step(job.id, "checking against normal variation")
    goals = []
    for raw in store.list_goals(job.dataset_id):
        try:
            goals.append(Goal.from_dict(raw))
        except (ValueError, KeyError):
            continue  # a malformed goal must not sink the whole analysis
    story = analyse(frame, result, previous_snapshot=previous, goals=goals)

    if story.held:
        payload = story.to_dict()
        payload["chips"] = []
        payload["columns"] = []
        # A refresh that failed the gate is exactly what nobody is watching
        # for once ingestion is unattended, so it still raises alerts.
        held_alerts = build_alerts(
            None, [], story.quality,
            already_sent=store.sent_alert_keys(job.dataset_id),
        )
        if held_alerts:
            store.record_alerts(job.dataset_id, [a.to_dict() for a in held_alerts])
        payload["new_alerts"] = [a.to_dict() for a in held_alerts]
        store.save_story(job.dataset_id, payload)
        return payload

    if story.quality and story.quality.snapshot:
        store.remember_snapshot(result.fingerprint, story.quality.snapshot.to_dict())

    store.set_step(job.id, "ranking what matters most")
    provider = from_env()
    cache: dict[str, str] = {}
    payload = story.to_dict()

    for finding, raw in zip(story.findings, payload["findings"]):
        narration = narrate(finding, provider, cache=cache)
        raw["summary"] = narration.text
        raw["narrated_by"] = narration.source

    # Proactive monitoring (spec Pillar 2). Alerts are derived from the
    # analysis that just ran, deduplicated against everything this dataset has
    # already been told, and recorded so the same event never fires twice.
    store.set_step(job.id, "checking what changed")
    try:
        alerts = build_alerts(
            story.frame,
            story.findings,
            story.quality,
            already_sent=store.sent_alert_keys(job.dataset_id),
        )
        if alerts:
            store.record_alerts(job.dataset_id, [a.to_dict() for a in alerts])
        payload["new_alerts"] = [a.to_dict() for a in alerts]
    except Exception as exc:  # monitoring must not sink the story
        payload["new_alerts"] = []
        payload.setdefault("errors", []).append(f"alerting failed: {exc}")

    columns = set(story.frame.data.columns) if story.frame else set()
    payload["chips"] = [
        {"name": chip.name, "label": chip.label}
        for chip in suggest_chips(story.findings, columns)
    ]
    payload["columns"] = sorted(columns)

    store.save_story(job.dataset_id, payload)
    return payload


def build_handlers(files: FileStore | None = None) -> dict:
    """Wire job kinds to their handlers, bound to a file store.

    The store is bound here so the worker and the API always share one, which
    is what makes the whole thing testable against a temporary directory.
    """
    from .jobs import JobKind

    resolved = files if files is not None else store_from_env()

    return {
        JobKind.DETECT: lambda job, store: handle_detect(job, store, resolved),
        JobKind.ANALYSE: lambda job, store: handle_analyse(job, store, resolved),
    }
