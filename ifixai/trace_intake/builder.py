"""Turn vendor-neutral steps into canonical capture rows.

Each source mapper reduces its export to `Step`s that say only what the trace states: a tool
execution (action), a retrieval (source), a generation's final text (output), or a structural
span that carries no fact (agent, chain, plain span). This module orders them, binds ids and
parents, digests arguments and content, and wraps them in one start and one end event. It never
creates an approval, revocation or delegation; those come only from an explicit, iFixAi-marked
canonical event carried by the trace itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ifixai.trace_intake._core import (
    MAX_EVENTS,
    TRACE_ID_RE,
    UUID_RE,
    TraceIntakeError,
    Warnings,
    canonical_digest,
    cap_text,
    has_truncation_marker,
    is_identifier,
    short_hash,
    strict_json_loads,
)
from ifixai.trace_intake.schema import EXPLICIT_KINDS, validate_payload, validate_rows

START_ID = "ifixai-import-start"
END_ID = "ifixai-import-end"
UNATTRIBUTED_ACTOR = "unattributed-agent"
UNKNOWN_TOOL = "unknown-tool"
EVENT_FIELDS = {"event_id", "trace_id", "tenant_id", "sequence", "time_ns", "parents", "payload"}


class Missing:
    """Marks a field the trace does not state, as distinct from a stated null."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = Missing()


@dataclass
class Document:
    doc_id: Any
    content: Any


@dataclass
class Step:
    step_id: str
    parent_id: str | None = None
    kind: str = "structural"  # action | source | output | structural
    start_ns: int | None = None
    end_ns: int | None = None
    actor_id: str | None = None
    agent: str | None = None
    action: str | None = None
    arguments: Any = MISSING
    outcome: str = "unknown"
    documents: list[Document] = field(default_factory=list)
    retrieval_resource: str | None = None
    text: str | None = None
    explicit: Any = None


@dataclass
class ParsedTrace:
    source: str
    raw_trace_id: str
    steps: list[Step]
    label: str = ""
    declared_complete: bool = False
    root_start_ns: int | None = None
    root_end_ns: int | None = None


@dataclass
class Draft:
    event_id: str
    time_ns: int
    order: tuple[int, int]
    payload: dict[str, Any]
    step: Step
    explicit_parents: tuple[str, ...] | None = None


def canonical_trace_id(source: str, raw: str, warnings: Warnings | None = None) -> str:
    """32 lowercase hex digits: the source id itself, a UUID without dashes, or a digest."""
    value = raw.strip().lower()
    if TRACE_ID_RE.match(value):
        return value
    if UUID_RE.match(raw.strip()):
        return value.replace("-", "")
    if warnings is not None:
        warnings.add(
            "trace_id_derived",
            "source trace id is not 32 hex digits; the canonical id is derived from its SHA-256",
            raw[:80],
        )
    return short_hash(f"{source}:{raw}", 32)


def select_trace(groups: dict[str, Any], trace_id: str | None, source: str) -> str:
    """The one raw trace id to import; several traces need an explicit selection."""
    if not groups:
        raise TraceIntakeError(f"{source} input holds no trace")
    if trace_id is None:
        if len(groups) != 1:
            raise TraceIntakeError(
                f"{source} input holds {len(groups)} traces; select one with --trace-id"
            )
        return next(iter(groups))
    wanted = trace_id.strip()
    for raw in groups:
        if raw == wanted or canonical_trace_id(source, raw) == wanted.lower():
            return raw
    raise TraceIntakeError("selected trace is absent from the input")


def safe_identifier(raw: Any, warnings: Warnings, code: str, what: str) -> str:
    text = raw if isinstance(raw, str) else str(raw)
    if is_identifier(text):
        return text
    warnings.add(code, f"{what} is not a canonical identifier; replaced by a digest", text[:80])
    return f"id-{short_hash(text)}"


def safe_resource(raw: str, warnings: Warnings) -> str:
    text = raw
    if "*" in text:
        warnings.add("resource_wildcard_escaped", "a '*' in a resource was escaped as %2A")
        text = text.replace("*", "%2A")
    if not 1 <= len(text) <= 1000:
        warnings.add("resource_rewritten", "resource is empty or too long; replaced by a digest")
        text = f"resource:{short_hash(raw)}"
    return text


def parse_arguments(value: Any) -> Any:
    """A JSON-encoded argument string is digested as the object it encodes."""
    if isinstance(value, str):
        try:
            return strict_json_loads(value)
        except ValueError:
            return value
    return value


class Builder:
    def __init__(
        self,
        parsed: ParsedTrace,
        tenant_id: str,
        collector_id: str,
        max_text: int,
        warnings: Warnings,
    ) -> None:
        self.parsed = parsed
        self.tenant_id = tenant_id
        self.collector_id = collector_id
        self.max_text = max_text
        self.warnings = warnings
        self.trace_id = canonical_trace_id(parsed.source, parsed.raw_trace_id, warnings)
        self.steps: dict[str, Step] = {}
        self.event_ids: dict[str, str] = {}
        self.step_events: dict[str, list[str]] = {}

    def index(self) -> None:
        for step in self.parsed.steps:
            if step.step_id in self.steps:
                raise TraceIntakeError(f"duplicate span id {step.step_id[:80]!r}")
            self.steps[step.step_id] = step
            self.event_ids[step.step_id] = safe_identifier(
                step.step_id, self.warnings, "span_id_rewritten", "span id"
            )
            if step.start_ns is not None and step.end_ns is not None and step.end_ns < step.start_ns:
                raise TraceIntakeError(f"span {step.step_id[:80]!r} ends before it starts")
        if len(set(self.event_ids.values())) != len(self.event_ids):
            raise TraceIntakeError("span ids collide after normalization")
        if {START_ID, END_ID} & set(self.event_ids.values()):
            raise TraceIntakeError("a span id collides with a reserved import event id")
        for step in self.parsed.steps:
            if step.parent_id is not None and step.parent_id not in self.steps:
                self.warnings.add(
                    "orphan_parent",
                    "span names a parent that is absent from the export; attached to the start",
                    step.step_id[:80],
                )
        for step in self.parsed.steps:
            self.ancestors(step)

    def ancestors(self, step: Step) -> list[Step]:
        chain: list[Step] = []
        seen = {step.step_id}
        current = step
        while current.parent_id is not None and current.parent_id in self.steps:
            if current.parent_id in seen:
                raise TraceIntakeError("span parents form a cycle")
            seen.add(current.parent_id)
            current = self.steps[current.parent_id]
            chain.append(current)
        return chain

    def actor(self, step: Step) -> str:
        for candidate in [step, *self.ancestors(step)]:
            stated = candidate.actor_id or candidate.agent
            if stated:
                return safe_identifier(stated, self.warnings, "actor_rewritten", "actor id")
        self.warnings.add(
            "actor_unattributed",
            f"no agent or actor id is stated; events use the placeholder {UNATTRIBUTED_ACTOR!r}",
            step.step_id[:80],
        )
        return UNATTRIBUTED_ACTOR

    def time_of(self, step: Step, fallback: int) -> int:
        if step.end_ns is not None:
            return step.end_ns
        if step.start_ns is not None:
            self.warnings.add(
                "end_time_missing", "span has no end time; its start time is used",
                step.step_id[:80],
            )
            return step.start_ns
        self.warnings.add(
            "timestamp_missing",
            "span has no timestamps; it is ordered at its nearest timed ancestor",
            step.step_id[:80],
        )
        for ancestor in self.ancestors(step):
            known = ancestor.start_ns if ancestor.start_ns is not None else ancestor.end_ns
            if known is not None:
                return known
        return fallback

    def check_truncated(self, value: Any, step: Step) -> None:
        if has_truncation_marker(value):
            self.warnings.add(
                "content_truncated",
                "span content carries a truncation or redaction marker upstream",
                step.step_id[:80],
            )

    def drafts_for(self, step: Step, order: int, fallback: int) -> list[Draft]:
        if step.explicit is not None:
            draft = self.explicit_draft(step, order, fallback)
            return [draft] if draft else []
        if step.kind == "structural":
            return []
        if step.kind == "output" and not step.text:
            return []
        time_ns = self.time_of(step, fallback)
        base_id = self.event_ids[step.step_id]
        if step.kind == "action":
            return [Draft(base_id, time_ns, (order, 0), self.action_payload(step), step)]
        if step.kind == "source":
            return self.source_drafts(step, base_id, time_ns, order)
        if step.kind == "output":
            self.check_truncated(step.text, step)
            payload = {
                "kind": "output",
                "actor_id": self.actor(step),
                "label": cap_text(step.text or "", self.max_text),
            }
            return [Draft(base_id, time_ns, (order, 0), validate_payload(payload), step)]
        raise TraceIntakeError(f"unsupported step kind {step.kind!r}")

    def action_payload(self, step: Step) -> dict[str, Any]:
        if step.action:
            action = safe_identifier(step.action, self.warnings, "tool_name_rewritten", "tool name")
        else:
            self.warnings.add(
                "tool_name_missing", "tool span states no tool name", step.step_id[:80]
            )
            action = UNKNOWN_TOOL
        if step.arguments is MISSING:
            self.warnings.add(
                "tool_arguments_missing",
                "tool span states no arguments; the digest covers null",
                step.step_id[:80],
            )
            arguments = None
        else:
            arguments = parse_arguments(step.arguments)
            self.check_truncated(arguments, step)
        if step.outcome == "unknown":
            self.warnings.add(
                "outcome_unknown",
                "tool span states no success or error status; outcome is unknown",
                step.step_id[:80],
            )
        payload = {
            "kind": "action",
            "actor_id": self.actor(step),
            "action": action,
            "resource": safe_resource(f"tool:{action}", self.warnings),
            "arguments_digest": canonical_digest(arguments),
            "outcome": step.outcome,
        }
        return validate_payload(payload)

    def source_drafts(self, step: Step, base_id: str, time_ns: int, order: int) -> list[Draft]:
        documents = step.documents or [Document(None, None)]
        if not step.documents:
            self.warnings.add(
                "retrieval_documents_missing",
                "retrieval span itemizes no documents; one source covers the span",
                step.step_id[:80],
            )
        actor = self.actor(step)
        fallback_resource = step.retrieval_resource or f"retrieval:{base_id}"
        drafts = []
        for position, document in enumerate(documents):
            event_id = base_id if len(documents) == 1 else f"{base_id}#doc{position}"
            event_id = safe_identifier(event_id, self.warnings, "span_id_rewritten", "span id")
            if document.doc_id not in (None, ""):
                resource = str(document.doc_id)
            else:
                if step.documents:
                    self.warnings.add(
                        "document_id_missing",
                        "retrieved document states no id; the resource names its position",
                        step.step_id[:80],
                    )
                resource = (
                    fallback_resource if len(documents) == 1
                    else f"{fallback_resource}#{position}"
                )
            self.check_truncated(document.content, step)
            payload = {
                "kind": "source",
                "actor_id": actor,
                "resource": safe_resource(resource, self.warnings),
                "content_digest": canonical_digest(document.content),
            }
            drafts.append(
                Draft(event_id, time_ns, (order, position), validate_payload(payload), step)
            )
        return drafts

    def explicit_draft(self, step: Step, order: int, fallback: int) -> Draft | None:
        raw = step.explicit
        if isinstance(raw, str):
            try:
                raw = strict_json_loads(raw)
            except ValueError as error:
                raise TraceIntakeError("ifixai.event is not valid JSON") from error
        if not isinstance(raw, dict):
            raise TraceIntakeError("ifixai.event must be a JSON object")
        if "payload" in raw:
            unknown = set(raw) - EVENT_FIELDS
            if unknown:
                raise TraceIntakeError("ifixai.event has fields a canonical event does not")
            event, payload_raw = raw, raw["payload"]
        else:
            event, payload_raw = {}, raw
        payload = validate_payload(payload_raw)
        event_id = self.event_ids[step.step_id]
        if payload["kind"] not in EXPLICIT_KINDS:
            self.warnings.add(
                "explicit_boundary_ignored",
                "explicit start/end events are ignored; the importer writes its own",
                step.step_id[:80],
            )
            return None
        stated_time = event.get("time_ns")
        if step.end_ns is None and step.start_ns is None and type(stated_time) is int:
            time_ns = stated_time
        else:
            time_ns = self.time_of(step, fallback)
        bound = (
            event.get("event_id", event_id) == event_id
            and str(event.get("trace_id", self.trace_id)).lower() == self.trace_id
            and event.get("tenant_id", self.tenant_id) == self.tenant_id
            and event.get("time_ns", time_ns) == time_ns
        )
        if not bound:
            raise TraceIntakeError(
                "ifixai.event is not bound to its span's id, trace, tenant and time"
            )
        parents = event.get("parents")
        explicit_parents = None
        if parents is not None:
            if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
                raise TraceIntakeError("ifixai.event parents must be a list of ids")
            explicit_parents = tuple(parents)
        return Draft(event_id, time_ns, (order, 0), payload, step, explicit_parents)

    def parent_of(self, draft: Draft, emitted: set[str]) -> list[str]:
        if draft.explicit_parents is not None:
            parents = list(dict.fromkeys(draft.explicit_parents))
            if parents and len(parents) <= 32 and all(p in emitted for p in parents):
                return parents
            self.warnings.add(
                "explicit_parents_invalid",
                "explicit parents do not all precede the event; the span tree is used",
                draft.step.step_id[:80],
            )
        for ancestor in self.ancestors(draft.step):
            for candidate in self.step_events.get(ancestor.step_id, []):
                if candidate in emitted:
                    return [candidate]
        return [START_ID]

    def build(self) -> list[dict[str, Any]]:
        self.index()
        parsed = self.parsed
        known = [
            moment
            for step in parsed.steps
            for moment in (step.start_ns, step.end_ns)
            if moment is not None
        ] + [moment for moment in (parsed.root_start_ns, parsed.root_end_ns) if moment is not None]
        if not known:
            self.warnings.add("no_timestamps", "the trace states no timestamps at all")
        start_time = min(known) if known else 0
        drafts: list[Draft] = []
        for order, step in enumerate(parsed.steps):
            drafts.extend(self.drafts_for(step, order, start_time))
            if len(drafts) + 2 > MAX_EVENTS:
                raise TraceIntakeError(f"import would exceed the {MAX_EVENTS} event limit")
        drafts.sort(key=lambda draft: (draft.time_ns, draft.order))
        ids = [draft.event_id for draft in drafts]
        if len(set(ids)) != len(ids):
            raise TraceIntakeError("derived event ids collide")
        for draft in drafts:
            self.step_events.setdefault(draft.step.step_id, []).append(draft.event_id)
        kinds = {draft.payload["kind"] for draft in drafts}
        if "output" in kinds and "source" in kinds:
            self.warnings.add(
                "citations_not_stated",
                "the trace does not state which sources an output used; outputs carry no citations",
            )
        if not drafts:
            self.warnings.add("no_facts", "no span in the trace maps to a canonical fact")
        start_time = min([start_time, *(draft.time_ns for draft in drafts)])
        end_time = max([*known, start_time, *(draft.time_ns for draft in drafts)])
        complete = parsed.declared_complete and not self.warnings
        if parsed.declared_complete and not complete:
            self.warnings.add(
                "completeness_not_honoured",
                "the trace declares itself complete, but the import found gaps",
            )
        total = len(drafts) + 2
        rows: list[dict[str, Any]] = [
            {
                "schema_version": "1",
                "trace_id": self.trace_id,
                "tenant_id": self.tenant_id,
                "collector_id": self.collector_id,
                "capture_complete": complete,
                "source_format": "jsonl",
            },
            self.row(START_ID, 1, start_time, [], validate_payload(
                {"kind": "start", "label": cap_text(parsed.label, self.max_text)}
            )),
        ]
        emitted = {START_ID}
        for sequence, draft in enumerate(drafts, 2):
            rows.append(
                self.row(
                    draft.event_id, sequence, draft.time_ns,
                    self.parent_of(draft, emitted), draft.payload,
                )
            )
            emitted.add(draft.event_id)
        last = drafts[-1].event_id if drafts else START_ID
        rows.append(
            self.row(END_ID, total, end_time, [last], validate_payload(
                {"kind": "end", "complete": complete, "declared_events": total}
            ))
        )
        validate_rows(rows)
        return rows

    def row(
        self, event_id: str, sequence: int, time_ns: int, parents: list[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "trace_id": self.trace_id,
            "tenant_id": self.tenant_id,
            "sequence": sequence,
            "time_ns": time_ns,
            "parents": parents,
            "payload": payload,
        }


def build_rows(
    parsed: ParsedTrace,
    *,
    tenant_id: str,
    collector_id: str,
    max_text: int,
    warnings: Warnings,
) -> list[dict[str, Any]]:
    return Builder(parsed, tenant_id, collector_id, max_text, warnings).build()
