"""Reading and writing the register's files.

The register on disk is two directories of YAML, and that is the whole storage layer:

``register/entries/``
    Accepted entries. One file per permanent id, named ``IFX-YYYY-NNNN.yaml``.

``register/candidates/``
    Submissions. One file per candidate reference, named ``IFXC-<digest12>-<YYYYMMDD>.yaml``.

Files, in git, in the open repository. No database and no service, because the most reversible
thing a register can be at the start is a directory somebody can fork: if the project later moves
to an API, these files are the migration, and until then every reader can check the whole register
with a clone and a validator.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from ifixai.register.spec import MisbehaviourReport, ReportState

ENTRIES_DIR = Path("register/entries")
CANDIDATES_DIR = Path("register/candidates")


def dump_yaml(report: MisbehaviourReport) -> str:
    """The report as YAML, in the field order the spec declares.

    Key order follows the model rather than the alphabet: a reader meets the world before the
    grading, as the spec document presents it. ``sort_keys=False`` is what keeps that.
    """
    payload = report.model_dump(mode="json")
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)


def load_report(path: Path) -> MisbehaviourReport:
    return MisbehaviourReport.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def filename_for(report: MisbehaviourReport) -> str:
    if report.state is ReportState.candidate:
        assert report.candidate_ref is not None  # the model requires it
        return f"{report.candidate_ref}.yaml"
    assert report.id is not None
    return f"{report.id}.yaml"


def path_for(report: MisbehaviourReport, root: Path = Path()) -> Path:
    directory = CANDIDATES_DIR if report.state is ReportState.candidate else ENTRIES_DIR
    return root / directory / filename_for(report)


@dataclass(frozen=True)
class Register:
    """Every report the register holds, and the files that would not load.

    Load errors are kept rather than raised: one malformed candidate must not stop CI from
    validating the rest, and the bot's message is more useful when it can say which file.
    """

    reports: tuple[MisbehaviourReport, ...]
    load_errors: dict[str, str]

    @property
    def entries(self) -> tuple[MisbehaviourReport, ...]:
        return tuple(report for report in self.reports if report.state is ReportState.accepted)

    @property
    def candidates(self) -> tuple[MisbehaviourReport, ...]:
        return tuple(report for report in self.reports if report.state is ReportState.candidate)

    def by_reference(self, reference: str) -> Optional[MisbehaviourReport]:
        for report in self.reports:
            if reference in (report.id, report.candidate_ref):
                return report
        return None


def load_register(root: Path = Path()) -> Register:
    reports: list[MisbehaviourReport] = []
    errors: dict[str, str] = {}
    for directory in (root / ENTRIES_DIR, root / CANDIDATES_DIR):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                reports.append(load_report(path))
            except Exception as error:  # noqa: BLE001 - any unreadable file is reported, never fatal
                errors[str(path)] = f"{type(error).__name__}: {error}"
    return Register(tuple(reports), errors)


def write_report(report: MisbehaviourReport, root: Path = Path()) -> Path:
    """Write a report to its canonical path. Never overwrites: a submission that would replace an
    existing file is almost always a re-run that should be an edit under review, not a silent
    clobber of somebody else's filing."""
    path = path_for(report, root)
    if path.exists():
        raise FileExistsError(f"{path} already exists; edit it deliberately rather than overwriting")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(report), encoding="utf-8")
    return path
