"""Thin presentation layer over ``rich`` and ``questionary`` with safe fallback.

The only module that imports rich/questionary. Both are imported lazily and
every renderer/prompt degrades gracefully when stdout is not a TTY or NO_COLOR
is set. Rendering helpers return True when handled via rich, False otherwise so
callers keep their plain-text path.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Sequence

from ifixai.cli._branding import supports_color

_ACCENT = "rgb(232,99,42)"
_DIM = "grey62"


@lru_cache(maxsize=1)
def _rich_available() -> bool:
    try:
        import rich  # noqa: F401

        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _questionary_available() -> bool:
    try:
        import questionary  # noqa: F401

        return True
    except Exception:
        return False


def use_rich() -> bool:
    """True when styled rich output is appropriate (TTY + colour + lib)."""
    return supports_color() and _rich_available()


def is_interactive() -> bool:
    """True only when we can safely run an interactive prompt."""
    return (
        supports_color()
        and _questionary_available()
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


@lru_cache(maxsize=1)
def console():
    """Lazy singleton ``rich.Console`` writing to stdout."""
    from rich.console import Console

    return Console()


def _qstyle():
    """A branded questionary style (accent pointer/highlight)."""
    import questionary

    return questionary.Style(
        [
            ("qmark", "fg:#e8632a bold"),
            ("question", "bold"),
            ("pointer", "fg:#e8632a bold"),
            ("highlighted", "fg:#e8632a bold"),
            ("selected", "fg:#4ade80"),
            ("answer", "fg:#e8632a bold"),
            ("instruction", "fg:#9aa0a6"),
            ("text", ""),
        ]
    )


def _choices(labels, descriptions):
    """Build questionary Choice objects with optional inline descriptions."""
    import questionary

    desc = descriptions or {}
    return [
        questionary.Choice(title=label, value=label, description=desc.get(label))
        for label in labels
    ]


def select(
    message: str,
    choices: Sequence[str],
    default: str | None = None,
    descriptions: dict[str, str] | None = None,
) -> str:
    """Single-choice select; returns the default when non-interactive."""
    choices = list(choices)
    if not choices:
        raise ValueError("select() requires at least one choice")
    if not is_interactive():
        return default if default in choices else choices[0]
    import questionary

    kwargs = dict(
        choices=_choices(choices, descriptions),
        default=default or choices[0],
        style=_qstyle(),
        use_shortcuts=False,
        instruction="(↑/↓ to move, enter to select)",
    )
    if descriptions:
        kwargs["show_description"] = True
    answer = questionary.select(message, **kwargs).ask()
    return answer if answer is not None else (default or choices[0])


def multiselect(
    message: str,
    choices: Sequence[str],
    *,
    descriptions: dict[str, str] | None = None,
    hint: str = "space to toggle, ↵ to confirm",
) -> list[str]:
    """Multi-choice checkbox with descriptions. Empty list when non-interactive."""
    choices = list(choices)
    if not is_interactive():
        return []
    import questionary

    kwargs = dict(
        choices=_choices(choices, descriptions),
        style=_qstyle(),
        instruction=f"({hint})",
    )
    if descriptions:
        kwargs["show_description"] = True
    answer = questionary.checkbox(message, **kwargs).ask()
    return answer or []


def confirm(message: str, default: bool = True) -> bool:
    """Yes/no confirmation. Falls back to click.confirm when non-interactive."""
    if not is_interactive():
        return default
    import questionary

    answer = questionary.confirm(message, default=default, style=_qstyle()).ask()
    return default if answer is None else bool(answer)


def text(message: str, default: str = "") -> str:
    """Free-text prompt. Falls back to click.prompt when non-interactive."""
    if not is_interactive():
        return default
    import questionary

    answer = questionary.text(message, default=default, style=_qstyle()).ask()
    return default if answer is None else answer


def table(title: str, columns: Sequence[str], rows: Sequence[Sequence[str]]) -> bool:
    """Render a rich table. Returns False (no-op) when rich is unavailable."""
    if not use_rich():
        return False
    from rich.table import Table

    tbl = Table(title=title, title_style=f"bold {_ACCENT}", header_style="bold")
    for col in columns:
        tbl.add_column(col)
    for row in rows:
        tbl.add_row(*[str(c) for c in row])
    console().print(tbl)
    return True


def _score_style(score: float) -> str:
    if score >= 0.9:
        return "bold green"
    if score >= 0.7:
        return "bold yellow"
    return "bold red"


def _grade_color(grade: str) -> str:
    return {
        "A": "green",
        "B": "green",
        "C": "yellow",
        "D": "red",
        "F": "red",
    }.get((grade or "F")[0].upper(), "red")


def _bar(ratio: float, width: int = 18) -> str:
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "·" * (width - filled)


def render_scorecard(result) -> bool:
    """Render the premium scorecard; False when rich is unavailable (use fallback)."""
    if not use_rich():
        return False

    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from ifixai.reporting.scorecard import compute_insights

    ins = compute_insights(result)
    c = console()

    head = Text()
    if ins["self_judged"]:
        head.append("SELF-JUDGED\n", style="bold yellow")
        head.append(
            "score redacted — the provider judged its own output (biased).\n",
            style=_DIM,
        )
        head.append(
            "Add a second judge for a real grade: ", style=_DIM
        )
        head.append("--judge-provider … --eval-mode single", style="italic")
    else:
        overall = ins["overall_score"]
        if overall is None:
            head.append("Score: n/a ", style="bold yellow")
            head.append("(insufficient evidence)", style=_DIM)
        else:
            grade = ins["grade"]
            gcolor = _grade_color(grade)
            verdict = "PASS" if ins["passed"] else "FAIL"
            head.append(f" {grade} ", style=f"bold white on {gcolor}")
            head.append("   ")
            head.append(
                f" {verdict} ",
                style="bold white on green" if ins["passed"] else "bold white on red",
            )
            head.append("\n\n")
            head.append(_bar(overall, 30), style=_score_style(overall))
            head.append(f"  {overall:.1%}", style=_score_style(overall))
            head.append("  overall\n", style=_DIM)
            head.append(_bar(ins["strategic_score"], 30), style=_DIM)
            head.append(f"  {ins['strategic_score']:.1%}", style=_DIM)
            head.append("  strategic", style=_DIM)
    c.print(
        Panel(
            head,
            title="[bold]iFixAi Scorecard[/bold]",
            border_style=_ACCENT,
            expand=False,
            padding=(1, 3),
        )
    )

    tbl = Table(box=None, pad_edge=False, header_style="bold")
    tbl.add_column("Category")
    tbl.add_column("Coverage")
    tbl.add_column("Result", justify="right")
    tbl.add_column("Score", justify="right")
    for cs in result.category_scores:
        total_in_suite = len(cs.test_ids)
        if total_in_suite == 0:
            continue
        failed = cs.test_count - cs.tests_passed
        inconclusive = total_in_suite - cs.test_count
        ratio = cs.tests_passed / total_in_suite if total_in_suite else 0.0
        if cs.score is None:
            result_txt = Text("⊘ not scored", style="yellow")
            score_txt = Text("—", style=_DIM)
        elif failed > 0:
            result_txt = Text(f"✗ {failed} failed", style="red")
            score_txt = Text(f"{cs.score:.0%}", style=_score_style(cs.score))
        elif inconclusive > 0:
            result_txt = Text(f"⊘ {inconclusive} inconcl.", style="yellow")
            score_txt = Text(f"{cs.score:.0%}", style=_score_style(cs.score))
        else:
            result_txt = Text("✓ all passed", style="green")
            score_txt = Text(f"{cs.score:.0%}", style=_score_style(cs.score))
        bar = Text(_bar(ratio), style=_score_style(cs.score if cs.score else 0.0))
        coverage = Text.assemble(bar, f"  {cs.tests_passed}/{total_in_suite}")
        tbl.add_row(cs.category.value, coverage, result_txt, score_txt)
    c.print(tbl)

    sc = ins["status_counts"]
    insight = Text()
    insight.append("Tests  ", style=_DIM)
    insight.append(f"✓ {sc['pass']}", style="green")
    insight.append(f"  ✗ {sc['fail']}", style="red")
    insight.append(f"  ⊘ {sc['inconclusive']}", style="yellow")
    if sc["error"]:
        insight.append(f"  ⚠ {sc['error']} error", style="red")
    insight.append(
        f"      Coverage {ins['scored_categories']}/{ins['total_categories']} "
        "categories\n",
        style=_DIM,
    )
    if ins["weakest_pillars"] and not ins["self_judged"]:
        insight.append("Weakest  ", style=_DIM)
        parts = [
            f"{name} {score:.0%}" for name, score in ins["weakest_pillars"]
        ]
        insight.append("   ".join(parts))
    c.print(
        Panel(insight, title="Insights", border_style=_DIM, expand=False)
    )

    if ins["exploratory"]:
        expl = Text()
        for item in ins["exploratory"]:
            mark = "✓" if item["passing"] else "·"
            expl.append(f"  {mark} {item['test_id']} ", style=_DIM)
            expl.append(f"{item['name']} ", style=_DIM)
            expl.append(f"{item['score']:.0%}\n", style=_DIM)
        c.print(
            Panel(
                expl,
                title="Exploratory — frontier signal, not scored",
                border_style=_DIM,
                expand=False,
            )
        )

    return True
