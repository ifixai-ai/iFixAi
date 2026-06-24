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


def _verdict_style(passed: bool) -> str:
    return "bold white on green" if passed else "bold white on red"


def _row_style(score: float | None) -> str:
    if score is None:
        return "yellow"
    if score >= 0.9:
        return "bright_green"
    if score >= 0.7:
        return "yellow"
    return "red"


def render_scorecard(result) -> bool:
    """Render the premium scorecard; False when rich is unavailable (use fallback)."""
    if not use_rich():
        return False

    from rich import box as rich_box
    from rich.align import Align
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    from ifixai.reporting.scorecard import compute_insights

    ins = compute_insights(result)
    c = console()

    c.print()

    if ins["self_judged"]:
        result_body = Text(justify="center")
        result_body.append("⚠  SELF-JUDGED\n\n", style="bold yellow")
        result_body.append(
            "The provider graded its own output — this result is biased\n"
            "and not citable. Add a second judge for a real grade:\n",
            style=_DIM,
        )
        result_body.append("  --judge-provider … --eval-mode single", style="italic")
        c.print(
            Panel(
                Align.center(result_body),
                title="[bold]iFixAi Result[/bold]",
                border_style="yellow",
                padding=(1, 4),
                expand=True,
            )
        )
    else:
        overall = ins["overall_score"]
        if overall is None:
            result_body = Text(justify="center")
            result_body.append("n/a\n\n", style="bold yellow")
            result_body.append("Insufficient evidence across all inspections.", style=_DIM)
            c.print(
                Panel(
                    Align.center(result_body),
                    title="[bold]iFixAi Result[/bold]",
                    border_style="yellow",
                    padding=(1, 4),
                    expand=True,
                )
            )
        else:
            grade = ins["grade"]
            gcolor = _grade_color(grade)
            passed = ins["passed"]
            verdict_label = "PASS" if passed else "FAIL"
            verdict_color = "green" if passed else "red"

            grade_txt = Text(f"  {grade}  ", style=f"bold white on {gcolor}", justify="center")
            verdict_txt = Text(f"  {verdict_label}  ", style=f"bold white on {verdict_color}", justify="center")

            badges = Columns(
                [
                    Align.center(grade_txt),
                    Align.center(verdict_txt),
                ],
                equal=True,
                expand=False,
            )

            body = Text(justify="center")
            body.append("\n")
            bar_overall = _bar(overall, 36)
            body.append(f"{bar_overall}", style=_score_style(overall))
            body.append(f"  {overall:.1%}", style=f"bold {_score_style(overall)}")
            body.append("  overall score\n", style=_DIM)

            strategic = ins["strategic_score"]
            bar_strategic = _bar(strategic, 36)
            body.append(f"{bar_strategic}", style=_score_style(strategic))
            body.append(f"  {strategic:.1%}", style=_score_style(strategic))
            body.append("  strategic (top 8)\n", style=_DIM)

            from rich.console import Group
            panel_content = Group(
                Align.center(badges),
                Align.center(body),
            )
            c.print(
                Panel(
                    panel_content,
                    title=f"[bold {_ACCENT}]iFixAi Result[/bold {_ACCENT}]",
                    border_style=_ACCENT,
                    padding=(1, 4),
                    expand=True,
                )
            )

    c.print()

    tbl = Table(
        title="Scorecard by Category",
        title_style=f"bold {_ACCENT}",
        box=rich_box.ROUNDED,
        border_style="grey42",
        header_style=f"bold {_ACCENT}",
        show_lines=True,
        expand=True,
    )
    tbl.add_column("#", style="grey62", justify="center", width=3)
    tbl.add_column("Category", style="bold", min_width=20)
    tbl.add_column("Score", justify="center", min_width=8)
    tbl.add_column("Progress", min_width=24)
    tbl.add_column("Tests", justify="center", width=12)
    tbl.add_column("Result", justify="center", min_width=16)

    idx = 0
    for cs in result.category_scores:
        total_in_suite = len(cs.test_ids)
        if total_in_suite == 0:
            continue
        idx += 1
        failed = cs.test_count - cs.tests_passed
        inconclusive = total_in_suite - cs.test_count
        ratio = cs.tests_passed / total_in_suite if total_in_suite else 0.0
        row_style = _row_style(cs.score)

        score_txt = Text(
            "—" if cs.score is None else f"{cs.score:.0%}",
            style=f"bold {row_style}",
            justify="center",
        )
        bar_txt = Text(_bar(ratio, 20), style=row_style)

        tests_txt = Text(
            f"{cs.tests_passed}/{total_in_suite}",
            style=row_style,
            justify="center",
        )

        if cs.score is None:
            result_txt = Text("⊘  not scored", style="yellow", justify="center")
        elif failed > 0:
            result_txt = Text(f"✗  {failed} failed", style="bold red", justify="center")
        elif inconclusive > 0:
            result_txt = Text(f"⊘  {inconclusive} inconclusive", style="yellow", justify="center")
        else:
            result_txt = Text("✓  all passed", style="bold green", justify="center")

        tbl.add_row(
            str(idx),
            cs.category.value,
            score_txt,
            bar_txt,
            tests_txt,
            result_txt,
        )

    c.print(tbl)
    c.print()

    sc = ins["status_counts"]
    summary = Table(box=None, pad_edge=False, show_header=False, expand=True)
    summary.add_column("label", style=_DIM)
    summary.add_column("value")

    tests_txt = Text()
    tests_txt.append(f"✓ {sc['pass']} passed", style="green")
    tests_txt.append("   ")
    tests_txt.append(f"✗ {sc['fail']} failed", style="red")
    tests_txt.append("   ")
    tests_txt.append(f"⊘ {sc['inconclusive']} inconclusive", style="yellow")
    if sc["error"]:
        tests_txt.append(f"   ⚠ {sc['error']} error", style="bold red")

    summary.add_row("Tests", tests_txt)
    summary.add_row(
        "Coverage",
        Text(
            f"{ins['scored_categories']} of {ins['total_categories']} categories scored",
            style=_DIM,
        ),
    )

    if ins["weakest_pillars"] and not ins["self_judged"]:
        weakest_txt = Text()
        for i, (name, score) in enumerate(ins["weakest_pillars"]):
            if i:
                weakest_txt.append("   ")
            weakest_txt.append(f"{name} ", style="bold")
            weakest_txt.append(f"{score:.0%}", style=_score_style(score))
        summary.add_row("Weakest pillars", weakest_txt)

    c.print(Panel(summary, title="Run Summary", border_style=_DIM, expand=True))

    if ins["exploratory"]:
        expl_tbl = Table(
            box=rich_box.SIMPLE,
            header_style="bold grey62",
            show_header=True,
            expand=True,
        )
        expl_tbl.add_column("ID", style=_DIM, width=6)
        expl_tbl.add_column("Name")
        expl_tbl.add_column("Score", justify="right", width=8)
        expl_tbl.add_column("Signal", justify="center", width=8)
        for item in ins["exploratory"]:
            mark = Text("✓", style="green") if item["passing"] else Text("·", style=_DIM)
            expl_tbl.add_row(
                item["test_id"],
                item["name"],
                Text(f"{item['score']:.0%}", style=_DIM),
                mark,
            )
        c.print(
            Panel(
                expl_tbl,
                title="[grey62]Exploratory — frontier signal, excluded from grade[/grey62]",
                border_style=_DIM,
                expand=True,
            )
        )

    c.print(Rule(style="grey23"))
    c.print()
    return True
