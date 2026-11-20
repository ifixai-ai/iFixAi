"""The register's names, in one place, because they are still being decided.

Identifiers and the repository they live in are the two things most likely to change once the
register has readers, and the two things hardest to change if they are written as literals across a
dozen files. So every one of them is a constant here, and the patterns are built from the constants
rather than spelled out again:

``ENTRY_ID_PREFIX``
    ``AGENT``, giving ids like ``AGENT-2026-0001``. It means agent, and it is not an acronym: there
    is deliberately no expansion of it anywhere in this repository. The prefix carries no vendor's
    name on purpose — CVE ids are not ``MITRE-YYYY-NNNN``, and a register meant to be cited by
    people who do not work here should not read as one company's asset.

``CANDIDATE_REF_PREFIX``
    ``CAND``. A candidate reference is not an identifier the register issues; it is the submitter's
    handle on their own draft, derived from the behaviour signature and the date.

``REGISTRY_REPO``
    Where submissions go. Changing it repoints the CLI, the docs and the MCP tool at once.

Changing ``ENTRY_ID_PREFIX`` is a one-line change here plus a regeneration of the schema. It does
not rewrite existing files: ids already issued keep the prefix they were issued under, which is why
``ID_PATTERN`` is anchored on the constant rather than on a character class.
"""

from __future__ import annotations

import re

#: The prefix of a permanent, accepted entry id. One line to change.
ENTRY_ID_PREFIX = "AGENT"

#: The prefix of a candidate's derived, self-assigned reference.
CANDIDATE_REF_PREFIX = "CAND"

#: The public register. Submissions are pull requests against this repository.
REGISTRY_REPO = "ifixai-ai/agent-misbehavior-registry"
REGISTRY_URL = f"https://github.com/{REGISTRY_REPO}"

#: Canonical spelling of a permanent id: four digits for 0001-9999, no padding beyond that, and no
#: trailing newline (``\Z``), so one id has one string and one file name.
ID_PATTERN = re.compile(rf"^{ENTRY_ID_PREFIX}-(?P<year>\d{{4}})-(?P<number>\d{{4}}|[1-9]\d{{4,}})\Z", re.ASCII)

#: ``CAND-<digest12>-<YYYYMMDD>``.
CANDIDATE_REF_PATTERN = re.compile(rf"^{CANDIDATE_REF_PREFIX}-[0-9a-f]{{12}}-\d{{8}}\Z", re.ASCII)

#: What an id looks like, for an error message or a document.
ID_EXAMPLE = f"{ENTRY_ID_PREFIX}-YYYY-NNNN"
CANDIDATE_REF_EXAMPLE = f"{CANDIDATE_REF_PREFIX}-<digest12>-<YYYYMMDD>"


def format_id(year: int, number: int) -> str:
    return f"{ENTRY_ID_PREFIX}-{year:04d}-{number:04d}"


def entry_year(entry_id: str) -> int:
    match = ID_PATTERN.match(entry_id)
    if match is None:
        raise ValueError(f"{entry_id!r} is not an {ID_EXAMPLE} id")
    return int(match["year"])


def entry_number(entry_id: str) -> int:
    match = ID_PATTERN.match(entry_id)
    if match is None:
        raise ValueError(f"{entry_id!r} is not an {ID_EXAMPLE} id")
    return int(match["number"])
