from __future__ import annotations

from collections.abc import Mapping
from string import Formatter

_FORMATTER = Formatter()


class MissingPlaceholderError(KeyError):

    def __init__(self, key: str, template_snippet: str = "") -> None:
        self.key = key
        self.template_snippet = template_snippet
        message = f"template references {{{key}}} but no value was supplied"
        if template_snippet:
            message = f"{message} (in: {template_snippet!r})"
        super().__init__(message)


class _StrictFormatDict(dict):  # type: ignore[type-arg]

    def __init__(self, context: Mapping[str, object], template: str) -> None:
        super().__init__(context)
        self._template = template

    def __missing__(self, key: str) -> str:
        snippet = self._template[:80] if self._template else ""
        raise MissingPlaceholderError(key, snippet)


def extract_placeholders(template: str) -> set[str]:
    fields: set[str] = set()
    for _, field, format_spec, _ in _FORMATTER.parse(template):
        if field:
            fields.add(field.split(".", 1)[0].split("[", 1)[0])
            fields.update(extract_placeholders(format_spec))
    return fields


def render(template: str, context: Mapping[str, object]) -> str:
    return template.format_map(_StrictFormatDict(context, template))
