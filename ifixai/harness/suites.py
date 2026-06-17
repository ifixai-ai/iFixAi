"""Named test suites for the ``--suite`` selector (tiers and themes)."""

from __future__ import annotations

from typing import TypedDict

from ifixai.core.types import InspectionCategory

_CORE_PILLARS: frozenset[InspectionCategory] = frozenset(
    {
        InspectionCategory.FABRICATION,
        InspectionCategory.MANIPULATION,
        InspectionCategory.DECEPTION,
        InspectionCategory.UNPREDICTABILITY,
        InspectionCategory.OPACITY,
    }
)


class SuiteResolution(TypedDict):
    test_ids: list[str]
    unknown: list[str]


def _core_ids() -> list[str]:
    from ifixai.harness.registry import ALL_SPECS

    return [s.test_id for s in ALL_SPECS if s.category in _CORE_PILLARS]


def _extended_ids() -> list[str]:
    from ifixai.harness.registry import ALL_SPECS

    return [s.test_id for s in ALL_SPECS if s.category not in _CORE_PILLARS]


def _all_ids() -> list[str]:
    from ifixai.harness.registry import ALL_SPECS

    return [s.test_id for s in ALL_SPECS]


def _strategic_ids() -> list[str]:
    from ifixai.harness.registry import STRATEGIC_TESTS

    return list(STRATEGIC_TESTS)


def _smoke_ids() -> list[str]:
    return _strategic_ids()[:3]


_TIER_RESOLVERS: dict[str, "callable[[], list[str]]"] = {
    "smoke": _smoke_ids,
    "strategic": _strategic_ids,
    "core": _core_ids,
    "extended": _extended_ids,
    "all": _all_ids,
}

_TIER_DESCRIPTIONS: dict[str, str] = {
    "smoke": "Fastest sanity check (3 strategic inspections).",
    "strategic": "The headline strategic set used for the strategic score.",
    "core": "The 32 graded five-pillar inspections (B-series).",
    "extended": "The 13 frontier inspections (P/C/S/X-series).",
    "all": "Every registered inspection.",
}


class _Theme(TypedDict):
    categories: list[str]
    extra_ids: list[str]
    description: str


_THEMES: dict[str, _Theme] = {
    "security": {
        "categories": ["MANIPULATION"],
        "extra_ids": ["P01", "P22", "P27", "P13"],
        "description": "Injection, privilege escalation, sabotage, power elevation.",
    },
    "reliability": {
        "categories": ["UNPREDICTABILITY"],
        "extra_ids": ["B16", "B17", "B29", "B32", "C02", "C05", "C11"],
        "description": "Consistency, reproducibility, silent failure, uncertainty calibration.",
    },
    "compliance": {
        "categories": ["OPACITY"],
        "extra_ids": ["B03", "B23", "P08", "X04", "X11"],
        "description": "Auditability, traceability, regulatory readiness, oversight gates.",
    },
    "frontier": {
        "categories": [],
        "extra_ids": [],
        "description": "The frontier / extended-risk roster (P/C/S/X-series).",
    },
}


TIER_NAMES: list[str] = list(_TIER_RESOLVERS)
THEME_NAMES: list[str] = list(_THEMES)
SUITE_NAMES: list[str] = TIER_NAMES + THEME_NAMES


def _dedup(ids: list[str]) -> list[str]:
    from ifixai.harness.registry import ALL_SPECS

    wanted = set(ids)
    return [s.test_id for s in ALL_SPECS if s.test_id in wanted]


def resolve_suite(name: str) -> SuiteResolution:
    """Expand a suite name into its concrete, de-duplicated test IDs."""
    from ifixai.harness.registry import resolve_category_test_ids

    key = name.strip().lower()

    if key in _TIER_RESOLVERS:
        return {"test_ids": _dedup(_TIER_RESOLVERS[key]()), "unknown": []}

    if key in _THEMES:
        theme = _THEMES[key]
        if key == "frontier":
            return {"test_ids": _extended_ids(), "unknown": []}
        from_categories = resolve_category_test_ids(theme["categories"])["test_ids"]
        return {
            "test_ids": _dedup([*from_categories, *theme["extra_ids"]]),
            "unknown": [],
        }

    return {"test_ids": [], "unknown": [name]}


def suite_catalog() -> list[dict[str, object]]:
    """Rows for ``ifixai list suites``: name, kind, count, description."""
    rows: list[dict[str, object]] = []
    for tier in TIER_NAMES:
        rows.append(
            {
                "name": tier,
                "kind": "tier",
                "count": len(resolve_suite(tier)["test_ids"]),
                "description": _TIER_DESCRIPTIONS.get(tier, ""),
            }
        )
    for theme in THEME_NAMES:
        rows.append(
            {
                "name": theme,
                "kind": "theme",
                "count": len(resolve_suite(theme)["test_ids"]),
                "description": _THEMES[theme]["description"],
            }
        )
    return rows
