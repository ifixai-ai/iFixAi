#!/usr/bin/env python3
"""Check submitted register files and write a readable verdict.

Run by ``.github/workflows/register-submission.yml`` over the files a pull request changed, and
runnable by hand over the same files. The point of the separate script rather than a run of the CLI
is the output: a submitter who gets this wrong should be told which field and what to change, in a
comment they can act on, not handed a stack trace.

Checks, in order:

==========================  =====================================================================
schema                      the file parses as a misbehaviour report, spec version 1
rules                       every cross-field rule (twin, grading, signature digest, counts)
unfinished                  no ``TODO:`` placeholder is left in a submission
calibration                 at least one reproduction currently calibrates, 1 for the reference
                            solution and 0 for doing nothing
rights                      an audit-derived report has de_identified and rights_cleared both set
id_claim                    a candidate claims no permanent IFX id
placement                   the file is in the directory its state belongs in, under its own name
duplicate                   the behaviour signature is flagged against existing reports (never a
                            failure: a curator decides whether it joins an existing entry)
==========================  =====================================================================

Exit status is 1 when anything failed, so the check is required and a failing submission is not
merged. A duplicate flag alone exits 0.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ifixai.register import naming, store, validator  # noqa: E402
from ifixai.register.draft import unfinished  # noqa: E402
from ifixai.register.spec import (  # noqa: E402
    SPEC_VERSION,
    ReportState,
    ReproductionStatus,
    SourceKind,
)


@dataclass
class FileVerdict:
    path: str
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def check_file(path: Path, existing: Sequence) -> FileVerdict:
    verdict = FileVerdict(path=str(path))
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        verdict.failures.append(f"**unreadable** — {type(error).__name__}: {error}")
        return verdict
    result = validator.validate_payload(payload)
    report = result.report
    if report is None:
        verdict.failures.append(
            f"**does not match the report spec (version {SPEC_VERSION})**. "
            "See `docs/register-spec.md`; run `ifixai report validate` locally to see this before pushing."
        )
        verdict.failures.extend(f"`{problem.where}` — {problem.message}" for problem in result.problems)
        return verdict
    verdict.failures.extend(f"`{problem.where}` — {problem.message}" for problem in result.problems)

    left = unfinished(report)
    if left:
        verdict.failures.append(
            "**unfinished draft** — these fields still carry the drafter's `TODO:` placeholder: "
            + ", ".join(f"`{item}`" for item in left[:10])
        )

    calibrating = [repro for repro in report.reproductions if repro.status is ReproductionStatus.calibrates]
    if not calibrating:
        verdict.failures.append(
            "**no calibration** — no reproduction currently calibrates. At least one implementation must have a "
            "reference solution scoring 1 and a null action scoring 0, verified on the runtime version named. "
            "This is what separates a finding from an assertion."
        )
    for repro in calibrating:
        reference = repro.calibration.reference_solution.score
        null = repro.calibration.null_action.score
        if (reference, null) != (1, 0):
            verdict.failures.append(
                f"**bad calibration** — `{repro.runtime} {repro.task_id}` reports reference {reference} and null "
                f"{null}; a calibrating implementation is 1 and 0"
            )

    if report.provenance.source_kind is SourceKind.audit_derived and not (
        report.provenance.de_identified and report.provenance.rights_cleared
    ):
        verdict.failures.append(
            "**audit-derived without clearance** — `provenance.de_identified` and `provenance.rights_cleared` must "
            "both be true. Customer data and customer contracts are both cleared before anything is published."
        )

    if report.state is ReportState.candidate and report.id is not None:
        verdict.failures.append(
            f"**claims an id** — `{report.id}`. Permanent `{naming.ID_EXAMPLE}` ids are issued by curation; a submission "
            "is a candidate and carries only its derived `candidate_ref`."
        )

    expected_path = store.path_for(report)
    if path.name != expected_path.name or path.parent.name != expected_path.parent.name:
        verdict.failures.append(
            f"**wrong place** — a {report.state.value} report belongs at `{expected_path}`, not `{path}`"
        )

    duplicates = validator.find_duplicates(report, existing)
    for duplicate in duplicates:
        verdict.notes.append(
            f"possible duplicate — {duplicate}. If this is the same behaviour on a new model, the right change is "
            "usually an added `observed` row on the existing entry. A curator decides; this is not a failure."
        )
    return verdict


def render(verdicts: Sequence[FileVerdict]) -> str:
    lines = ["## Register submission check", ""]
    if not verdicts:
        lines.append("No register files changed in this pull request.")
        return "\n".join(lines)
    failed = [verdict for verdict in verdicts if not verdict.ok]
    if failed:
        lines.append(
            f"**Not mergeable as submitted.** {len(failed)} of {len(verdicts)} file(s) did not pass. Each item "
            "below names the field and what to change; push a fix and this runs again."
        )
    else:
        lines.append(f"All {len(verdicts)} submitted file(s) pass.")
    for verdict in verdicts:
        lines += ["", f"### `{verdict.path}`"]
        if verdict.ok:
            lines.append("Passes schema, rules, calibration, rights and placement.")
        for failure in verdict.failures:
            lines.append(f"- ❌ {failure}")
        for note in verdict.notes:
            lines.append(f"- ⚠️ {note}")
    lines += [
        "",
        "---",
        "",
        "Run the same checks locally with `ifixai report validate <file>`. The format and the reason for every "
        "required field are in [`docs/register-spec.md`](docs/register-spec.md).",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    """``check_register_submission.py [--register-root DIR] FILE...``

    ``--register-root`` is where ``entries/`` and ``candidates/`` live: the root of a clone of the
    public register, or ``ifixai/register/examples`` in this repository, where the worked examples double
    as the fixture. Defaulting it to the working directory means the script needs no arguments at
    all inside the register itself.
    """
    arguments = list(argv)
    register_root = Path(".")
    if "--register-root" in arguments:
        index = arguments.index("--register-root")
        register_root = Path(arguments[index + 1])
        del arguments[index : index + 2]
    paths = [Path(arg) for arg in arguments if arg.endswith(".yaml") and Path(arg).exists()]
    register = store.load_register(register_root)
    # Exclude the files under review from the comparison set, so a submission is never reported as
    # a duplicate of itself (its own file is already on the branch by the time this runs).
    under_review = {str(path.resolve()) for path in paths}
    existing = [
        report
        for report in register.reports
        if str((register_root / store.path_for(report)).resolve()) not in under_review
    ]
    verdicts = [check_file(path, existing) for path in paths]
    report_text = render(verdicts)
    print(report_text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report_text + "\n")
    for name, message in sorted(register.load_errors.items()):
        print(f"::warning file={name}::{message}")
    return 1 if any(not verdict.ok for verdict in verdicts) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
