"""Open a pull request with an approved submission. Nothing here runs without an approval.

Every function that could put bytes on the network takes an :class:`~ifixai.register.approval.Approval`
as its first argument and passes it through :func:`ifixai.register.approval.require` before doing
anything else. There is no code path in this module that sends without one, and no parameter that
makes the check optional.

Submissions go out as a pull request against ``ifixai-ai/agent-misbehavior-registry``, not through
an API. That is the most reversible choice available: a PR needs no service to exist, no account on
anything the project runs, and leaves the review in public where a register's credibility actually
comes from. If the project later wants an API, this module is the piece that changes and the report
format is untouched — which is the other reason the two are separate.

The ``gh`` CLI does the pushing. It is already how a maintainer of this project interacts with
GitHub, it uses the submitter's own credentials, and it means this module holds no token.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ifixai.register import approval as approval_module
from ifixai.register.approval import Approval
from ifixai.register.proposal import DEFAULT_REGISTER_REPO, Proposal


class SubmissionError(RuntimeError):
    """The submission could not be prepared or sent. Nothing partial is left behind."""


def _run(command: Sequence[str], cwd: Path) -> str:
    try:
        done = subprocess.run(
            list(command), cwd=str(cwd), capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise SubmissionError(f"could not run {command[0]}: {error}") from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        raise SubmissionError(f"{' '.join(command[:2])} failed: {detail[-1] if detail else 'no output'}")
    return done.stdout


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SubmissionError(f"{name} is not installed; install it or open the pull request by hand")
    return path


@dataclass(frozen=True)
class Submission:
    """What was sent, and where it can be read."""

    pull_request_url: str
    branch: str
    approval: dict[str, object]


def submit(
    prepared: Proposal,
    granted: Optional[Approval],
    checkout: Path,
    repo: str = DEFAULT_REGISTER_REPO,
    dry_run: bool = False,
) -> Submission:
    """Commit the approved report and open a pull request.

    ``granted`` is checked first, against the exact report in ``prepared``: an approval for an
    earlier version of the same report is refused, because the person approved bytes, not an
    intention. ``dry_run`` stops after the commit, so the whole path can be exercised in a test
    and by a cautious submitter without anything being published.
    """
    approved = approval_module.require(granted, prepared.report)
    blocked = prepared.blocked_reason
    if blocked is not None:
        raise SubmissionError(f"refusing to submit: {blocked}")
    git = _tool("git")
    target = checkout / prepared.relative_path
    if target.exists():
        raise SubmissionError(f"{prepared.relative_path} already exists in the checkout; this is already filed")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(prepared.yaml(), encoding="utf-8")
    _run([git, "checkout", "-b", prepared.branch], checkout)
    _run([git, "add", prepared.relative_path], checkout)
    _run([git, "commit", "-m", prepared.title], checkout)
    record = approval_module.record(approved)
    if dry_run:
        return Submission(pull_request_url="", branch=prepared.branch, approval=record)
    gh = _tool("gh")
    _run([git, "push", "-u", "origin", prepared.branch], checkout)
    out = _run(
        [gh, "pr", "create", "--repo", repo, "--base", "main", "--title", prepared.title, "--body", prepared.body],
        checkout,
    )
    url = next((line.strip() for line in out.splitlines() if line.startswith("http")), "")
    return Submission(pull_request_url=url, branch=prepared.branch, approval=record)
