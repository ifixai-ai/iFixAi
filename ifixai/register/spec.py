"""The misbehaviour report format, version 1.

Every required field is here because leaving it out breaks reproduction. ``docs/register-spec.md``
argues each one in prose; this module is the machine-checkable statement of the same thing.

The format is runtime-neutral by construction. Nothing in a report names a container, an image or
a harness command. What the agent sees (world, identities, tools, request), what counts as correct
(a condition on the final world state) and how it is scored (named binary checks over world state
or the evidence log) are stated once, in the report. A :class:`ReproductionImplementation` then
binds that description to one runtime, and a report may carry several, so Harbor is one
implementation among possible others and adding a second never changes the report's identity.

The report's identity is its :class:`BehaviourSignature`, not its id. A submission is always a
``candidate``; only a curated entry is ``accepted`` and consumes a permanent ``IFX-YYYY-NNNN`` id.
That keeps the register's quality independent of its volume: anyone may file, nobody may mint.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SPEC_VERSION = "1"

#: Canonical spelling of an accepted entry's permanent id, as the private catalogue uses it.
ENTRY_ID_PATTERN = re.compile(r"^IFX-(?P<year>\d{4})-(?P<number>\d{4}|[1-9]\d{4,})\Z", re.ASCII)
#: A candidate's local reference. Not an identifier the register owns: it is the submitter's
#: handle on their own draft, so two people can file the same behaviour without colliding.
CANDIDATE_REF_PATTERN = re.compile(r"^IFXC-[0-9a-f]{12}-\d{8}\Z", re.ASCII)

RISK_LEVELS = ("critical", "high", "medium", "low")


class StrictModel(BaseModel):
    """Unknown keys are an error: a typo in a submitted report must not be silently dropped,
    because a dropped field is a field that was not reviewed."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------- 1. world


class Entity(StrictModel):
    """An actor or object in the world, with concrete field values.

    Why required: an agent's behaviour is a function of what it can see. "a customer account" is
    not reproducible; ``{entity_id: ACC-0001, type: account, fields: {status: delinquent}}`` is.
    """

    entity_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)


class Record(StrictModel):
    """A data record the agent may read or act on.

    ``carries_untrusted_text`` marks a record whose free text came from outside the trust boundary
    (a customer note, a scraped page, a ticket body). A planted instruction lives in such a field,
    so a report about instruction-following from data must say which record carries it.
    """

    record_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    carries_untrusted_text: bool = False


class WorldState(StrictModel):
    """The starting world, as ids and field values rather than prose.

    Why required: without concrete state there is nothing for the expected condition to be a
    condition *on*, and no way to tell a run that acted on the wrong record from one that acted on
    the right one.
    """

    description: str = Field(min_length=1, description="One line of orientation for a human reader")
    entities: tuple[Entity, ...] = ()
    records: tuple[Record, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> WorldState:
        for label, ids in (
            ("entity", [entity.entity_id for entity in self.entities]),
            ("record", [record.record_id for record in self.records]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"two {label} entries share an id; ids must be unique in a world")
        return self

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in self.records)


# ------------------------------------------------------------------ 2. identity and authorization


class Role(StrictModel):
    """A role, the tools it holds, and where it may send work it may not do itself."""

    role: str = Field(min_length=1)
    holds_tools: tuple[str, ...] = ()
    may_escalate_to: tuple[str, ...] = ()


class Login(StrictModel):
    """An authenticated identity. Who is acting comes from here and from nowhere else."""

    login_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)


class EscalationChannel(StrictModel):
    channel_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Identity(StrictModel):
    """Roles, logins and escalation paths.

    Why required: "the agent used a tool it should not have" is meaningless without a statement of
    which role holds which tool. ``identity_source`` is fixed to ``login`` and cannot be set to
    anything else: a report in which identity could be claimed in conversation would be a report
    about a different, much less interesting failure, and would make the twin unconstructible.
    """

    identity_source: Literal["login"] = "login"
    roles: tuple[Role, ...] = Field(min_length=1)
    logins: tuple[Login, ...] = Field(min_length=1)
    escalation_channels: tuple[EscalationChannel, ...] = ()

    @model_validator(mode="after")
    def _coherent(self) -> Identity:
        role_names = {role.role for role in self.roles}
        if len(role_names) != len(self.roles):
            raise ValueError("two roles share a name")
        login_ids = {login.login_id for login in self.logins}
        if len(login_ids) != len(self.logins):
            raise ValueError("two logins share an id")
        for login in self.logins:
            unknown = [role for role in login.roles if role not in role_names]
            if unknown:
                raise ValueError(f"login {login.login_id} holds undeclared role(s): {', '.join(unknown)}")
        channels = {channel.channel_id for channel in self.escalation_channels}
        for role in self.roles:
            unknown = [channel for channel in role.may_escalate_to if channel not in channels]
            if unknown:
                raise ValueError(f"role {role.role} escalates to undeclared channel(s): {', '.join(unknown)}")
        return self

    def login(self, login_id: str) -> Login:
        for candidate in self.logins:
            if candidate.login_id == login_id:
                return candidate
        raise KeyError(login_id)

    def tools_of(self, login_id: str) -> frozenset:
        """Every tool the login holds through any of its roles."""
        held = {role.role: role.holds_tools for role in self.roles}
        return frozenset(tool for role in self.login(login_id).roles for tool in held.get(role, ()))

    def channels_of(self, login_id: str) -> frozenset:
        reachable = {role.role: role.may_escalate_to for role in self.roles}
        return frozenset(c for role in self.login(login_id).roles for c in reachable.get(role, ()))


# --------------------------------------------------------------------------------------- 3. tools


class Tool(StrictModel):
    """A tool the world offers.

    Why required: ``signature`` makes a call checkable (did it pass the requested record, or a
    different one?). ``risk`` is what a severity judgement rests on. ``mutates_state`` decides
    whether the expected condition can be a state condition at all: for a read-only tool, the
    evidence log is the only place the call is visible.
    """

    tool_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    signature: str = Field(min_length=1, description="e.g. refund(record_id: str, amount: int) -> Receipt")
    description: str = ""
    risk: Literal["critical", "high", "medium", "low"]
    mutates_state: bool


# ------------------------------------------------------------------------------------- 4. request


class Request(StrictModel):
    """What was asked, verbatim, and by which login.

    Why required: paraphrase is not reproduction. A summarised request loses the exact wording that
    carried the pressure, the claim of authority or the planted instruction, and two readers will
    rebuild two different tasks from it.
    """

    text: str = Field(min_length=1, description="Verbatim, exactly as sent")
    sent_by: str = Field(min_length=1, description="A login_id from `identity.logins`")
    channel: str = Field(default="chat", min_length=1)


# ---------------------------------------------------------------------------- 5. expected behavior


class WorldCondition(StrictModel):
    """One condition on the world after the run, or on the evidence log of the run.

    ``subject`` names what is being asserted about: a record id, a tool id, or a channel id.
    ``assertion`` is a small declarative statement, never a sentence to be judged. The permitted
    predicates are deliberately few; a report that needs a new one is a request to version the
    spec, not a licence to write prose here.
    """

    name: str = Field(min_length=1)
    description: str = Field(min_length=1, description="What a human should understand this to mean")
    predicate: Literal[
        "record_field_equals",
        "record_field_unchanged",
        "record_exists",
        "tool_called_on_record",
        "tool_not_called",
        "escalated_to_channel",
        "reply_sent",
    ]
    subject: str = Field(min_length=1)
    field: str = ""
    value: Any = None

    @model_validator(mode="after")
    def _predicate_arguments(self) -> WorldCondition:
        if self.predicate in ("record_field_equals", "record_field_unchanged") and not self.field:
            raise ValueError(f"{self.name}: predicate {self.predicate} needs `field`")
        if self.predicate == "record_field_equals" and self.value is None:
            raise ValueError(f"{self.name}: predicate record_field_equals needs `value`")
        return self

    @property
    def reads_world_state(self) -> bool:
        return self.predicate.startswith("record_")


class Expected(StrictModel):
    """What correct behaviour leaves behind.

    Why required, and why it is a condition on state: an agent that says "I have escalated this to
    your supervisor" and does nothing must fail, and an agent that does the right thing while
    phrasing it oddly must pass. There is no field anywhere in this format for the wording of a
    reply, and ``reply_sent`` asserts only that one was sent. That is not an oversight: grading on
    wording is how a register fills up with findings that do not survive a model upgrade.
    """

    summary: str = Field(min_length=1)
    world_conditions: tuple[WorldCondition, ...] = Field(min_length=1)
    forbidden_effects: tuple[WorldCondition, ...] = ()


# ----------------------------------------------------------------------------------------- 6. twin


class Twin(StrictModel):
    """The permitted counterpart: the same request and the same record, from a role that holds the
    tool.

    Why required: without it, a deployment that refuses everything scores perfectly. The twin is
    what turns "it did something it should not have" into "it discriminates correctly", and it is
    the only control that distinguishes a safety property from a capability loss. The validator
    enforces that the request text is identical and that the twin's login really does hold the
    tool the subject's login does not.
    """

    login_id: str = Field(min_length=1, description="A login whose roles DO hold the tool")
    request_text: str = Field(min_length=1, description="Must equal `request.text`, word for word")
    record_id: str = Field(min_length=1, description="Must equal the record the subject request named")
    expected: Expected


# -------------------------------------------------------------------------- 7. grading contract


class GradingCheck(StrictModel):
    """One named, binary check.

    ``source`` is restricted to the two things that can be recomputed by anyone holding the run's
    artefacts. There is no ``llm_judge`` member, and adding one would be a different spec: a check
    whose value depends on a model's opinion is not reproducible, and a register built on such
    checks cannot tell a real regression from a judge drift.
    """

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: Literal["world_state", "evidence_log"]
    conditions: tuple[str, ...] = Field(min_length=1, description="Names of `expected` conditions this check reads")


class Grading(StrictModel):
    """How a trial becomes a 0 or a 1.

    Why required: two people grading the same transcript by eye get two numbers. The combination is
    conjunctive and binary so the score has one meaning — every named check passed — and a partial
    credit scheme cannot quietly turn a failure into a pass.
    """

    checks: tuple[GradingCheck, ...] = Field(min_length=1)
    combination: Literal["all_checks_and"] = "all_checks_and"

    @model_validator(mode="after")
    def _unique(self) -> Grading:
        names = [check.name for check in self.checks]
        if len(set(names)) != len(names):
            raise ValueError("two grading checks share a name")
        return self


# ---------------------------------------------------------------------------- 8. calibration


class CalibrationRun(StrictModel):
    """One calibration run and the score it earned."""

    description: str = Field(min_length=1)
    score: int = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, description="Where the run can be seen: bundle hash, log path, job name")


class Calibration(StrictModel):
    """Proof that the task can be passed and can be failed.

    Why required, and why it is non-negotiable: a reproduction that has never been shown to reward
    a correct run is an assertion, not a finding. The reference solution scoring 1 rules out a task
    that is impossible or mis-specified; doing nothing scoring 0 rules out a task that any agent
    passes by inaction, which is the single most common way a misbehaviour "finding" turns out to
    be a broken grader.
    """

    reference_solution: CalibrationRun
    null_action: CalibrationRun
    calibrated_on: date

    @model_validator(mode="after")
    def _scores(self) -> Calibration:
        if self.reference_solution.score != 1:
            raise ValueError("the reference solution must score 1; a task nothing can pass is mis-specified")
        if self.null_action.score != 0:
            raise ValueError("doing nothing must score 0; a task inaction passes measures nothing")
        return self


# ------------------------------------------------------------------------- 9. determinism


class Determinism(StrictModel):
    """What was pinned and what was allowed to move.

    Why required: without the seed, the temperature and the step cap, a different occurrence count
    is not evidence of a different model — it may be evidence of a different setting. Stating what
    is *allowed* to vary is the part people omit, and it is the part that tells a re-runner whether
    their mismatch is a failure to reproduce or an expected wobble.
    """

    seed: Optional[int] = None
    temperature: float = Field(ge=0.0)
    step_cap: int = Field(gt=0)
    allowed_variation: tuple[str, ...] = Field(
        default=(),
        description="What may differ between runs without being a failure to reproduce",
    )


# ---------------------------------------------------------------------- 10. observed result


class ObservedResult(StrictModel):
    """One measured rate: counts, never a bare percentage.

    Why counts: "42% of the time" cannot be given a confidence interval, cannot be pooled with
    another run, and hides whether the denominator was 12 trials or 1,200. Occurrences out of
    trials can be recomputed and cannot be rounded into a better number.
    """

    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1, description="The exact identifier the runs called")
    harness: str = Field(min_length=1, description="The agent scaffold the model ran inside")
    harness_version: str = Field(min_length=1)
    occurrences: int = Field(ge=0)
    trials: int = Field(gt=0)
    observed_on: date
    note: str = ""

    @model_validator(mode="after")
    def _counts(self) -> ObservedResult:
        if self.occurrences > self.trials:
            raise ValueError(f"{self.model}: {self.occurrences} occurrences in only {self.trials} trials")
        return self

    @property
    def rate(self) -> float:
        return self.occurrences / self.trials


# ------------------------------------------------------------------- 11. provenance and rights


class SourceKind(str, Enum):
    synthetic = "synthetic"
    audit_derived = "audit_derived"
    public_incident = "public_incident"
    researcher = "researcher"


class Provenance(StrictModel):
    """Where the report came from, and whether it may be published.

    Why required: the register is public and permanent. A report derived from an audit of somebody
    else's system carries two risks a synthetic one does not — somebody's data and somebody's
    contract — so an ``audit_derived`` report is rejected unless both flags are set. The flags are
    assertions by a named human, not something a tool may infer: de-identification can be checked
    mechanically, clearance cannot.
    """

    source_kind: SourceKind
    reference: str = Field(min_length=1, description="The sweep, audit, incident or paper this came from")
    de_identified: bool = False
    rights_cleared: bool = False
    confirmed_by: str = Field(default="", description="Who asserted the flags; required when either is set")

    @model_validator(mode="after")
    def _rules(self) -> Provenance:
        if self.source_kind is SourceKind.audit_derived and not (self.de_identified and self.rights_cleared):
            raise ValueError(
                "an audit_derived report needs de_identified and rights_cleared both true: customer data and "
                "customer contracts both have to be cleared before anything is published"
            )
        if (self.de_identified or self.rights_cleared) and not self.confirmed_by.strip():
            raise ValueError("the de-identified / rights-cleared flags are an assertion by a person; name them")
        return self


# --------------------------------------------------------- behaviour signature and reproductions


class BehaviourSignature(StrictModel):
    """What makes two reports the same behaviour.

    The triple matches the private catalogue's clustering key exactly, so a report filed here and a
    cluster found there land on the same entry. ``digest`` is derived, never authored: the
    validator recomputes it and rejects a mismatch, so a submitter cannot dodge de-duplication by
    editing it.
    """

    family: str = Field(min_length=1, description="The task family, e.g. authority_boundary")
    failure: str = Field(min_length=1, description="The failed check, e.g. authority.violation")
    trigger: str = Field(min_length=1, description="The condition present in every failing trial")
    digest: str = Field(default="", description="Derived; see ifixai.register.signature")


class ReproductionStatus(str, Enum):
    """Whether this implementation still does what the report says it does."""

    calibrates = "calibrates"
    #: Rot: the implementation no longer calibrates on the runtime version named here. The report
    #: keeps its id and its findings; only this binding is stale.
    rot = "rot"
    unverified = "unverified"


class ReproductionImplementation(StrictModel):
    """One runtime that reproduces the report.

    Plural by design. The report is the definition; this is a binding of the definition to a
    runtime that happened to be available. Harbor is the reference implementation because it is the
    one iFixAi maintains and calibrates, not because the register depends on it: a second runtime
    can be appended to an accepted entry without reissuing its id, and an entry whose only
    implementation has rotted still describes a real, re-implementable behaviour.
    """

    runtime: str = Field(min_length=1, description="e.g. harbor, inspect, a project's own harness")
    version: str = Field(min_length=1)
    image_digest: str = Field(default="", description="sha256 of the environment image, when the runtime has one")
    task_id: str = Field(min_length=1)
    calibration: Calibration
    status: ReproductionStatus = ReproductionStatus.calibrates
    last_verified_on: Optional[date] = None
    rot_reason: str = ""
    reference_implementation: bool = False
    how_to_run: str = ""

    @model_validator(mode="after")
    def _status_rules(self) -> ReproductionImplementation:
        if self.status is ReproductionStatus.rot and not self.rot_reason.strip():
            raise ValueError(f"{self.runtime} {self.task_id}: status rot must say what stopped calibrating")
        if self.status is ReproductionStatus.calibrates and self.last_verified_on is None:
            raise ValueError(f"{self.runtime} {self.task_id}: a calibrating implementation needs last_verified_on")
        return self


# ------------------------------------------------------------------------------- the report


class ReportState(str, Enum):
    """Anything submitted is a candidate. Only curation produces an accepted entry, and only an
    accepted entry consumes a permanent id. Volume accumulates in one bucket; quality is asserted
    in the other."""

    candidate = "candidate"
    accepted = "accepted"
    rejected = "rejected"


class MisbehaviourReport(StrictModel):
    """One documented, reproducible kind of AI-agent misbehaviour."""

    spec_version: Literal["1"] = "1"
    state: ReportState = ReportState.candidate
    id: Optional[str] = Field(default=None, description="IFX-YYYY-NNNN; only an accepted entry has one")
    candidate_ref: Optional[str] = Field(default=None, description="The submitter's handle on their own draft")

    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, description="Plain-language description a non-specialist can follow")

    world: WorldState
    identity: Identity
    tools: tuple[Tool, ...] = Field(min_length=1)
    request: Request
    expected: Expected
    twin: Twin
    grading: Grading
    observed: tuple[ObservedResult, ...] = Field(min_length=1)
    determinism: Determinism
    provenance: Provenance
    signature: BehaviourSignature
    reproductions: tuple[ReproductionImplementation, ...] = Field(min_length=1)

    severity: Literal["critical", "high", "medium", "low"] = "medium"
    severity_rationale: str = ""
    detected_by: tuple[str, ...] = Field(default=(), description="iFixAi inspection ids, when the filer used them")
    caveats: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    rejection_reason: str = ""
    created_on: date
    updated_on: date

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and ENTRY_ID_PATTERN.match(value) is None:
            raise ValueError(f"{value!r} is not an IFX-YYYY-NNNN id")
        return value

    @field_validator("candidate_ref")
    @classmethod
    def _ref_shape(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and CANDIDATE_REF_PATTERN.match(value) is None:
            raise ValueError(f"{value!r} is not an IFXC-<digest12>-<YYYYMMDD> candidate reference")
        return value

    @model_validator(mode="after")
    def _state_rules(self) -> MisbehaviourReport:
        if self.state is ReportState.candidate:
            if self.id is not None:
                raise ValueError(
                    "a candidate carries no IFX id: permanent ids are issued by curation, never claimed in a "
                    "submission"
                )
            # candidate_ref is left open here on purpose: it is derived from the signature and the
            # creation date, so a freshly built draft cannot carry it yet. The validator requires it
            # of anything actually submitted (`validator._state`).
        else:
            if self.id is None:
                raise ValueError(f"a {self.state.value} report needs its IFX-YYYY-NNNN id")
            match = ENTRY_ID_PATTERN.match(self.id)
            assert match is not None  # the field validator already checked the shape
            if int(match["year"]) != self.created_on.year:
                raise ValueError(f"{self.id} carries year {match['year']} but was created in {self.created_on.year}")
        if self.state is ReportState.rejected and not self.rejection_reason.strip():
            raise ValueError("a rejected report keeps its id and records why in `rejection_reason`")
        if self.updated_on < self.created_on:
            raise ValueError("updated_on is before created_on")
        return self

    # -- convenience used by the validator, the signature and the renderers ---------------

    @property
    def tool_ids(self) -> tuple[str, ...]:
        return tuple(tool.tool_id for tool in self.tools)

    def tool(self, tool_id: str) -> Tool:
        for candidate in self.tools:
            if candidate.tool_id == tool_id:
                return candidate
        raise KeyError(tool_id)

    @property
    def tools_under_test(self) -> tuple[str, ...]:
        """Every tool the expected behaviour or the forbidden effects name, in order."""
        named: list[str] = []
        for condition in (*self.expected.world_conditions, *self.expected.forbidden_effects):
            if condition.predicate.startswith("tool_") and condition.subject not in named:
                named.append(condition.subject)
        return tuple(named)

    @property
    def prose_fields(self) -> tuple[str, ...]:
        """Every free-text field a reader sees. The scrubber walks this as well as the world, so a
        name dropped into a caveat is caught with the same net as a name in a record."""
        parts = [self.title, self.summary, self.severity_rationale, self.rejection_reason, self.request.text]
        parts += [self.expected.summary, self.twin.request_text, self.twin.expected.summary]
        parts += [condition.description for condition in self.expected.world_conditions]
        parts += [condition.description for condition in self.expected.forbidden_effects]
        parts += [check.description for check in self.grading.checks]
        parts += [observation.note for observation in self.observed]
        parts += [repro.how_to_run + " " + repro.rot_reason for repro in self.reproductions]
        parts += list(self.caveats)
        return tuple(part for part in parts if part)
