"""Builders for the SUT-facing prompt template variables shared across inspection runners.

Every judge-probe inspection renders its prompts from a `{placeholder}` template, and
almost all of them open on the same two facts drawn from the fixture: *who* is addressing
the system under test (the acting user, their role, the business domain) and *what* is in
front of it (one tool, one data source). Everything after that is the inspection's own
scenario prose.

The seam sits between that fixture-derived frame and the scenario payload rather than
around the whole variable map, because the payload is the part that must stay
per-inspection: it is the SUT-facing half of a deliberate split, where the oracle fields
(the tells, the reference answers) go only to the judge. A shared builder that swallowed
the payload would blur the one boundary these runners exist to hold.

Two builders rather than one because a handful of inspections show the SUT no tool and no
data source at all; they take the actor frame alone and never supply a rotation index they
would not use. Callers merge whichever frames they need into their own dict, so no builder
has to know which keys an inspection adds.

`rotation_index` picks the tool and data source by modulo, so a fixture holding fewer
tools than users still assigns every user a deterministic one instead of raising
IndexError. The fallback prose is fixed here rather than passed in: it is rendered
verbatim into the prompt when a fixture declares no tools or no data sources, which makes
it a shared wording contract, not a per-caller knob.
"""

from typing import TypedDict

from ifixai.core.types import Fixture, User

TOOL_FALLBACK_TEXT = "the available tooling"
DATA_SOURCE_FALLBACK_TEXT = "the primary data store"


class ActorTemplateVars(TypedDict):
    """Who is addressing the SUT, and in what business context."""

    role: str
    user: str
    domain: str


class ResourceTemplateVars(TypedDict):
    """The single tool and data source this probe puts in front of the SUT."""

    tool: str
    data_source: str


def build_actor_template_vars(
    *,
    fixture: Fixture,
    user: User,
    role_fallback: str,
) -> ActorTemplateVars:
    """Build the acting-user half of an inspection's SUT-facing template vars.

    A fixture user may carry no roles at all, and the role is rendered into the prompt as
    prose, so each inspection supplies its own `role_fallback` — the generic job title
    that reads naturally in *its* scenario ("an engineering role" for a code task, "an
    operations role" for a workflow one). That wording is the only thing that varies here,
    which is why it is the sole parameter beyond the fixture and the user.
    """
    return ActorTemplateVars(
        role=user.roles[0] if user.roles else role_fallback,
        user=user.name,
        domain=fixture.metadata.domain,
    )


def build_resource_template_vars(
    *,
    fixture: Fixture,
    rotation_index: int,
) -> ResourceTemplateVars:
    """Build the tool/data-source half of an inspection's SUT-facing template vars.

    `rotation_index` is taken modulo the available count, which spreads probes across the
    fixture's tools and data sources while staying deterministic for a given index — and
    keeps a fixture with fewer tools than users from raising IndexError. When the fixture
    declares neither, the shared fallback prose stands in so the prompt still reads as a
    sentence.
    """
    tools = fixture.tools or []
    data_sources = fixture.data_sources or []
    return ResourceTemplateVars(
        tool=tools[rotation_index % len(tools)].name if tools else TOOL_FALLBACK_TEXT,
        data_source=(
            data_sources[rotation_index % len(data_sources)].name
            if data_sources
            else DATA_SOURCE_FALLBACK_TEXT
        ),
    )
