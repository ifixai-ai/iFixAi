"""Assemble a submission without sending it.

This module is deliberately the whole of the "prepare" half of the contribution path, and it is
kept apart from :mod:`ifixai.register.submit` for a structural reason rather than a tidiness one:
nothing here can reach the network. It imports no ``subprocess``, no HTTP client and no credential
store, and :mod:`ifixai.register.mcp` — the surface an agent in Claude Code or Cursor talks to —
imports this and never imports ``submit``. So "an agent may prepare a submission but may not make
one" is a fact about the import graph, which a test can check, rather than a rule in a docstring
that a later change could quietly break.

A :class:`Proposal` is also what a person is shown before approving: the exact YAML that would be
committed, the validation result, and any existing report with the same behaviour signature.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from ifixai.register import store, validator
from ifixai.register.spec import MisbehaviourReport

#: Where submissions go by default. A maintainer can point this elsewhere; see the PR description
#: for why the register starts in the same repository as the tool that files into it.
DEFAULT_REGISTER_REPO = "ifixai-ai/iFixAi"
BRANCH_PREFIX = "register/candidate"


@dataclass(frozen=True)
class Proposal:
    """Everything a submission needs, assembled but not sent.

    This is what an agent is allowed to produce (see :mod:`ifixai.register.mcp`) and what a person
    is shown before approving. It deliberately holds no credential, no remote and no side effect:
    preparing a submission and making one are different acts, and only the second needs a human.
    """

    report: MisbehaviourReport
    relative_path: str
    branch: str
    title: str
    body: str
    validation: validator.Result
    duplicates: tuple[validator.Duplicate, ...]

    @property
    def blocked_reason(self) -> Optional[str]:
        if not self.validation.ok:
            return f"the report does not pass validation:\n{self.validation.render()}"
        return None

    def yaml(self) -> str:
        return store.dump_yaml(self.report)


def prepare(
    report: MisbehaviourReport,
    existing: Sequence[MisbehaviourReport] = (),
) -> Proposal:
    """Validate, de-duplicate and write the PR text. Sends nothing, needs no approval."""
    result = validator.validate(report)
    duplicates = validator.find_duplicates(report, existing)
    reference = report.candidate_ref or report.id or "unnamed"
    digest = report.signature.digest
    title = f"register: candidate {reference} — {report.title}"
    body = _body(report, result, duplicates)
    return Proposal(
        report=report,
        relative_path=str(store.path_for(report)),
        branch=f"{BRANCH_PREFIX}/{digest}",
        title=title[:120],
        body=body,
        validation=result,
        duplicates=duplicates,
    )


def _body(
    report: MisbehaviourReport,
    result: validator.Result,
    duplicates: Sequence[validator.Duplicate],
) -> str:
    observed = "\n".join(
        f"| {row.model} {row.model_version} | {row.harness} {row.harness_version} | "
        f"{row.occurrences}/{row.trials} | {row.observed_on} |"
        for row in report.observed
    )
    runtimes = "\n".join(
        f"| {repro.runtime} {repro.version} | `{repro.task_id}` | {repro.status.value} | "
        f"ref {repro.calibration.reference_solution.score} / nop {repro.calibration.null_action.score} |"
        for repro in report.reproductions
    )
    lines = [
        f"## {report.title}",
        "",
        report.summary,
        "",
        f"**Signature** `{report.signature.family}` / `{report.signature.failure}` / "
        f"`{report.signature.trigger}` → `{report.signature.digest}`",
        "**State** candidate (no `IFX` id is claimed; curation issues those)",
        f"**Provenance** {report.provenance.source_kind.value}, de-identified: "
        f"{report.provenance.de_identified}, rights cleared: {report.provenance.rights_cleared}",
        "",
        "### Observed",
        "",
        "| model | harness | occurrences / trials | date |",
        "| --- | --- | --- | --- |",
        observed,
        "",
        "### Reproduction implementations",
        "",
        "| runtime | task | status | calibration |",
        "| --- | --- | --- | --- |",
        runtimes,
        "",
        "### Twin",
        "",
        f"`{report.twin.login_id}` sends the identical request about the same record and is expected to have the "
        "work actually done. Without that half, a deployment that refuses everything would score perfectly.",
        "",
        "### Validation",
        "",
        "Passes `ifixai report validate`." if result.ok else f"```\n{result.render()}\n```",
    ]
    if duplicates:
        lines += [
            "",
            "### Possible duplicate",
            "",
            *[f"- {duplicate}" for duplicate in duplicates],
            "",
            "If this is the same behaviour on a new model, the right change is usually an added `observed` row on "
            "the existing entry rather than a new one. A curator decides.",
        ]
    lines += [
        "",
        "---",
        "",
        "Filed with `ifixai report submit`. De-identification ran locally before anything left the submitter's "
        "machine, and the submitter confirmed the redacted content and both provenance flags by hand.",
    ]
    return "\n".join(lines)


