"""Find a generation's final text and a retrieval's documents in common payload shapes.

Only the shapes listed here are read; anything else yields no text rather than a guess.
"""

from __future__ import annotations

from typing import Any

from ifixai.trace_intake._core import strict_json_loads
from ifixai.trace_intake.builder import Document

ASSISTANT_ROLES = ("assistant", "ai", "model")
MAX_DEPTH = 8


def maybe_json(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("{", "["):
        try:
            return strict_json_loads(value)
        except ValueError:
            return value
    return value


def content_text(content: Any) -> str | None:
    """A message `content` as plain text: a string, or the text parts of a part list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text", "output_text"):
                text = part.get("text", part.get("content"))
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts) if parts else None
    return None


def message_text(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    for key in ("content", "parts"):
        if key in message:
            text = content_text(message[key])
            if text:
                return text
    kwargs = message.get("kwargs")
    if isinstance(kwargs, dict):
        return message_text(kwargs)
    return None


def final_text(value: Any, depth: int = 0) -> str | None:
    """The final assistant text of a generation output, or None when none is stated."""
    if depth > MAX_DEPTH:
        return None
    value = maybe_json(value)
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        for item in reversed(value):
            if isinstance(item, dict) and item.get("role") in ASSISTANT_ROLES:
                return message_text(item)
        if value and all(isinstance(item, dict) and "type" in item for item in value):
            return content_text(value)
        return None
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("choices"), list) and value["choices"]:
        choice = value["choices"][0]
        if isinstance(choice, dict):
            return message_text(choice.get("message")) or final_text(choice.get("text"), depth + 1)
    generations = value.get("generations")
    if isinstance(generations, list) and generations:
        first = generations[0]
        if isinstance(first, list) and first:
            first = first[0]
        if isinstance(first, dict):
            return (
                final_text(first.get("text"), depth + 1)
                or message_text(first.get("message"))
            )
    if value.get("role") in ASSISTANT_ROLES:
        return message_text(value)
    for key in ("output", "completion", "text", "content", "message", "messages", "response"):
        if key in value:
            text = final_text(value[key], depth + 1)
            if text:
                return text
    return None


def documents_from(value: Any) -> list[Document]:
    """Retrieved documents from a `documents` list (LangChain, LlamaIndex, plain dicts)."""
    value = maybe_json(value)
    if isinstance(value, dict):
        value = value.get("documents", value.get("output", value.get("results")))
        value = maybe_json(value)
    if not isinstance(value, list):
        return []
    documents = []
    for item in value:
        if isinstance(item, dict):
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            doc_id = item.get("id") or metadata.get("id") or metadata.get("source")
            content = item.get("page_content", item.get("content", item.get("text", item)))
            documents.append(Document(doc_id, content))
        elif isinstance(item, str):
            documents.append(Document(None, item))
    return documents
