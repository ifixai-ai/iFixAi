"""Strict validation of canonical JSONL capture rows, independent of `ifixai.trajectory`.

The field names, types and constraints mirror the canonical trajectory models exactly (schema
version "1": a header row, then one event per row), so a capture that passes here also loads
with the trajectory package's `load_jsonl_capture`. On top of the per-row schema it checks the
shape an audit needs: one first start, one last end, contiguous sequences, nondecreasing time,
unique ids, and parents that precede their children.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from ifixai.trace_intake._core import (
    MAX_BYTES,
    MAX_EVENTS,
    TraceIntakeError,
    read_bounded,
    strict_json_loads,
)

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=200, pattern=r"^\S(?:[\s\S]*\S)?$")
]
Resource = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Timestamp = Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)]
Amount = Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def exact_resource(value: str) -> str:
    if "*" in value:
        raise ValueError("resource must be exact; wildcards belong only in resource_scope")
    return value


def valid_scope(value: str) -> str:
    if value.count("*") > 1 or ("*" in value and not value.endswith("*")):
        raise ValueError("resource_scope supports at most one terminal wildcard")
    return value


class StartPayload(Strict):
    kind: Literal["start"] = "start"
    label: str = Field(default="", max_length=1000)


class ApprovalPayload(Strict):
    kind: Literal["approval"] = "approval"
    actor_id: Identifier
    subject_id: Identifier
    action: Identifier
    resource: Resource
    arguments_digest: Digest
    expires_ns: Timestamp
    uses: int = Field(default=1, ge=1, le=100, strict=True)
    human: bool = Field(default=True, strict=True)
    check_resource = field_validator("resource")(exact_resource)


class RevocationPayload(Strict):
    kind: Literal["revocation"] = "revocation"
    actor_id: Identifier
    grant_id: Identifier


class DelegationPayload(Strict):
    kind: Literal["delegation"] = "delegation"
    actor_id: Identifier
    subject_id: Identifier
    action: Identifier
    resource_scope: Resource
    expires_ns: Timestamp
    parent_grant_id: Identifier | None = None
    check_scope = field_validator("resource_scope")(valid_scope)


class ActionPayload(Strict):
    kind: Literal["action"] = "action"
    actor_id: Identifier
    action: Identifier
    resource: Resource
    arguments_digest: Digest
    approval_id: Identifier | None = None
    delegation_id: Identifier | None = None
    cost_units: Amount = 0
    outcome: Literal["completed", "failed", "unknown"] = "completed"
    check_resource = field_validator("resource")(exact_resource)


class SourcePayload(Strict):
    kind: Literal["source"] = "source"
    actor_id: Identifier
    resource: Resource
    content_digest: Digest
    check_resource = field_validator("resource")(exact_resource)


class Citation(Strict):
    source_event_id: Identifier
    content_digest: Digest


class OutputPayload(Strict):
    kind: Literal["output"] = "output"
    actor_id: Identifier
    citations: tuple[Citation, ...] = Field(default=(), max_length=1000)
    label: str = Field(default="", max_length=1000)


class EndPayload(Strict):
    kind: Literal["end"] = "end"
    complete: bool = Field(strict=True)
    declared_events: int = Field(ge=2, le=100000, strict=True)


Payload = Annotated[
    StartPayload
    | ApprovalPayload
    | RevocationPayload
    | DelegationPayload
    | ActionPayload
    | SourcePayload
    | OutputPayload
    | EndPayload,
    Field(discriminator="kind"),
]


class EventRow(Strict):
    event_id: Identifier
    trace_id: TraceId
    tenant_id: Identifier
    sequence: int = Field(ge=1, le=100000, strict=True)
    time_ns: Timestamp
    parents: tuple[Identifier, ...] = Field(default=(), max_length=32)
    payload: Payload


class HeaderRow(Strict):
    schema_version: Literal["1"] = "1"
    trace_id: TraceId
    tenant_id: Identifier
    collector_id: Identifier
    capture_complete: bool = Field(strict=True)
    source_format: Literal["jsonl"] = "jsonl"


class PayloadOnly(Strict):
    payload: Payload


EXPLICIT_KINDS = ("approval", "revocation", "delegation", "action", "source", "output")


def validate_payload(raw: Any) -> dict[str, Any]:
    """One canonical payload, strictly; the normalized dict as it would be serialized."""
    try:
        return PayloadOnly.model_validate({"payload": raw}).model_dump(mode="json")["payload"]
    except ValidationError as error:
        raise TraceIntakeError(f"invalid canonical payload: {first_error(error)}") from error


def first_error(error: ValidationError) -> str:
    detail = error.errors()[0]
    location = ".".join(str(part) for part in detail["loc"])
    return f"{location}: {detail['msg']}" if location else detail["msg"]


def validate_rows(rows: list[dict[str, Any]]) -> None:
    """Refuse rows that are not a well-formed canonical capture."""
    if not rows:
        raise TraceIntakeError("a capture needs a header row")
    if len(rows) > MAX_EVENTS + 1:
        raise TraceIntakeError(f"a capture holds at most {MAX_EVENTS} events")
    try:
        header = HeaderRow.model_validate(rows[0])
        events = [EventRow.model_validate(row) for row in rows[1:]]
    except ValidationError as error:
        raise TraceIntakeError(f"invalid capture row: {first_error(error)}") from error
    if len(events) < 2:
        raise TraceIntakeError("a capture needs at least a start and an end event")
    seen: set[str] = set()
    previous = -1
    for index, event in enumerate(events, 1):
        problem = None
        if event.trace_id != header.trace_id or event.tenant_id != header.tenant_id:
            problem = "trace or tenant differs from the header"
        elif event.sequence != index:
            problem = "sequence is not contiguous from 1"
        elif event.event_id in seen:
            problem = "event_id is duplicated"
        elif event.time_ns < previous:
            problem = "time_ns decreases"
        elif len(set(event.parents)) != len(event.parents):
            problem = "parents repeat"
        elif any(parent not in seen for parent in event.parents):
            problem = "a parent does not precede its child"
        elif index > 1 and not event.parents:
            problem = "only the start event may have no parents"
        if problem:
            raise TraceIntakeError(f"event {index}: {problem}")
        seen.add(event.event_id)
        previous = event.time_ns
    kinds = [event.payload.kind for event in events]
    if kinds[0] != "start" or kinds.count("start") != 1 or events[0].parents:
        raise TraceIntakeError("the first event, and only it, must be a parentless start")
    if kinds[-1] != "end" or kinds.count("end") != 1:
        raise TraceIntakeError("the last event, and only it, must be an end")
    end = events[-1].payload
    if not isinstance(end, EndPayload) or end.declared_events != len(events):
        raise TraceIntakeError("end.declared_events must equal the event count")
    if end.complete != header.capture_complete:
        raise TraceIntakeError("end.complete must equal the header's capture_complete")


def load_capture_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read and validate a canonical JSONL capture written by this package."""
    text = read_bounded(path)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise TraceIntakeError("capture exceeds the 64 MiB limit")
    rows = []
    for line in text.split("\n"):
        if line.strip():
            rows.append(strict_json_loads(line))
            if len(rows) > MAX_EVENTS + 1:
                raise TraceIntakeError(f"a capture holds at most {MAX_EVENTS} events")
    validate_rows(rows)
    return rows
