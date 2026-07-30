"""Run the engine against a file from the command line.

``python -m busylab path/to/sales.xlsx``

The spec's one non-negotiable is that the engine is a plain library that runs
standalone against test files with no deployment involved (spec 9), because
that is how iteration actually happens. This is that entry point.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import loading
from .detection import detect
from .roles import ROLE_SPECS, TIER_SPECS


def _bar(label: str) -> str:
    return f"\n{label}\n{'-' * len(label)}"


def run(
    path: Path,
    *,
    verbose: bool = False,
    analyse_too: bool = True,
    question: str | None = None,
) -> int:
    try:
        frame, report = loading.load(path)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return 2

    if frame.empty:
        print(f"No usable table found in {path.name}.", file=sys.stderr)
        return 1

    print(_bar(f"File: {report.source}"))
    print(report.summary())
    for note in report.notes:
        print(f"  note: {note}")

    result = detect(frame)

    print(_bar("Columns understood"))
    for role, column in sorted(result.assignments.items(), key=lambda kv: kv[0].value):
        verdict = next(v for v in result.verdicts if v.column == column)
        mark = "ok" if verdict.status == "confident" else "??"
        print(
            f"  [{mark}] {ROLE_SPECS[role].label:<14} <- {column!r}"
            f"  ({verdict.confidence:.2f}, {verdict.reason})"
        )

    if result.unknown_columns:
        print(f"\n  not recognised: {', '.join(result.unknown_columns)}")

    print(_bar("Questions"))
    if result.prompts:
        for prompt in result.prompts:
            print(f"  {prompt.column}: {prompt.question}")
            if prompt.options:
                names = " / ".join(ROLE_SPECS[o].label for o in prompt.options)
                print(f"      options: {names}")
    else:
        print("  none, this file is ready to analyse")

    print(_bar("Analysis unlocked"))
    for tier, unlocked in result.tiers.items():
        spec = TIER_SPECS[tier]
        if unlocked:
            print(f"  [x] {spec.label}")
        else:
            print(f"  [ ] {spec.label} — {spec.locked_prompt}")

    if result.notes:
        print(_bar("Notes"))
        for note in result.notes:
            print(f"  {note}")

    if result.missing:
        missing = ", ".join(ROLE_SPECS[r].label for r in result.missing)
        print(f"\nCannot analyse yet. Missing: {missing}")
        return 1

    print(f"\nfingerprint {result.fingerprint} · ready: {result.ready}")

    if not analyse_too:
        return 0

    from .analysis import analyse
    from .narration import from_env, narrate, route_question, suggest_chips
    from .narration.routing import answer_from_findings

    story = analyse(frame, result)
    provider = from_env()

    print(_bar("Your story"))
    if provider.available():
        print(f"  (narrated by {provider.name})\n")
    if not story.findings:
        print("  nothing stood out in this data")

    cache: dict[str, str] = {}
    for i, finding in enumerate(story.findings, start=1):
        flag = {"urgent": "!!", "watch": "! ", "good": "+ ", "neutral": "  "}[
            finding.severity.value
        ]
        sentence = narrate(finding, provider, cache=cache)
        print(f"\n  {i}. {flag} {sentence.text}")
        print(
            f"        {finding.type.value} · {finding.chart.value}"
            f" · {finding.evidence.strength} · via {sentence.source}"
        )

    columns = set(story.frame.data.columns) if story.frame else set()
    chips = suggest_chips(story.findings, columns)
    if chips:
        print(_bar("Keep pulling the thread"))
        for chip in chips:
            print(f"  · {chip.label}")

    if question:
        decision = route_question(question, story.findings, provider, columns=columns)
        print(_bar(f"You asked: {question}"))
        if not decision.answerable:
            print(f"  {decision.refusal}")
        else:
            answer = answer_from_findings(decision, story.findings)
            print(f"  routed to '{decision.route.label}' via {decision.source}")
            if answer is not None:
                print(f"  {narrate(answer, provider, cache=cache).text}")
            else:
                print("  That analysis has not run on this data yet.")

    for note in story.errors:
        print(f"\n  error: {note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="busylab", description="Inspect what BusyLab understands about a file."
    )
    parser.add_argument("path", type=Path, help="an .xlsx or .csv sales file")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show per-column detail"
    )
    parser.add_argument(
        "--columns-only",
        action="store_true",
        help="stop after detection, do not run the analysis",
    )
    parser.add_argument(
        "-q",
        "--ask",
        metavar="QUESTION",
        help='ask a question about the findings, e.g. "why did revenue drop?"',
    )
    args = parser.parse_args(argv)
    return run(
        args.path,
        verbose=args.verbose,
        analyse_too=not args.columns_only,
        question=args.ask,
    )


if __name__ == "__main__":
    raise SystemExit(main())
