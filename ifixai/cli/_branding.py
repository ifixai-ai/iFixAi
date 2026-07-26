from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

import click

from ifixai.core.types import InspectionCategory, InspectionSpec

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _Spinner:
    def __init__(self, message: str) -> None:
        self._message = message
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame = 0
        self._start_time = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._start_time = time.monotonic()
        sys.stdout.write(self._render() + "\n")
        sys.stdout.flush()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=0.5)
        self._thread = None

    def _render(self) -> str:
        glyph = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
        elapsed = int(time.monotonic() - self._start_time) if self._start_time else 0
        suffix = f"  ({elapsed}s)" if elapsed >= 3 else ""
        return _truecolor(f"  {glyph} {self._message}{suffix}", _DIM_RGB)

    def _run(self) -> None:
        while not self._stop_event.wait(0.12):
            self._frame += 1
            sys.stdout.write("\033[1F\033[2K" + self._render() + "\n")
            sys.stdout.flush()


_LOGO_LINES: tuple[str, ...] = (
    "██  ███████ ██ ██   ██   █████  ██",
    "██  ██      ██  ██ ██   ██   ██ ██",
    "██  █████   ██   ███    ███████ ██",
    "██  ██      ██  ██ ██   ██   ██ ██",
    "██  ██      ██ ██   ██  ██   ██ ██",
)

_ACCENT_RGB = (232, 99, 42)
_DIM_RGB = (110, 110, 117)

_CATEGORY_COLORS: dict[InspectionCategory, tuple[int, int, int]] = {
    InspectionCategory.FABRICATION: (255, 139, 92),
    InspectionCategory.MANIPULATION: (255, 99, 99),
    InspectionCategory.DECEPTION: (167, 139, 250),
    InspectionCategory.UNPREDICTABILITY: (251, 191, 36),
    InspectionCategory.OPACITY: (96, 165, 250),
}

_CATEGORY_ORDER: tuple[InspectionCategory, ...] = (
    InspectionCategory.FABRICATION,
    InspectionCategory.MANIPULATION,
    InspectionCategory.DECEPTION,
    InspectionCategory.UNPREDICTABILITY,
    InspectionCategory.OPACITY,
)

_BAR_WIDTH = 26
_BLOCK_FULL = "█"
_BLOCK_EMPTY = "·"


def supports_color(stream=None) -> bool:
    s = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(s, "isatty"):
        return False
    return bool(s.isatty())


def _truecolor(text: str, rgb: tuple[int, int, int], bold: bool = False) -> str:
    if not supports_color():
        return text
    r, g, b = rgb
    prefix = f"\033[38;2;{r};{g};{b}m"
    if bold:
        prefix = "\033[1m" + prefix
    return f"{prefix}{text}\033[0m"


def print_startup_banner(version: str, *, quiet: bool = False) -> None:
    if quiet or not supports_color():
        return
    click.echo()
    for line in _LOGO_LINES:
        click.echo("  " + _truecolor(line, _ACCENT_RGB, bold=True))
    click.echo()
    click.echo(_truecolor(f"  ™  ·  v{version}  ·  powered by iMe", _DIM_RGB))
    click.echo()


@dataclass
class _CategoryRow:
    category: InspectionCategory
    total: int = 0
    done: int = 0
    failed: int = 0


@dataclass
class CategoryProgress:
    rows: dict[InspectionCategory, _CategoryRow] = field(default_factory=dict)
    _printed_lines: int = 0
    _started: bool = False
    _interactive: bool = False
    _spinner: _Spinner | None = None

    @classmethod
    def from_totals(cls, totals: dict[InspectionCategory, int]) -> CategoryProgress:
        rows = {
            cat: _CategoryRow(category=cat, total=totals.get(cat, 0))
            for cat in _CATEGORY_ORDER
        }
        return cls(rows=rows)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._interactive = supports_color()
        if not self._interactive:
            return
        total = sum(r.total for r in self.rows.values())
        if total == 0:
            return
        self._spinner = _Spinner(f"Running {total} tests")
        self._spinner.start()
        self._printed_lines = 1

    def record(self, category: InspectionCategory, passing: bool) -> None:
        row = self.rows.get(category)
        if row is None:
            return
        if row.total == 0:
            row.total = max(1, row.total)
        row.done += 1
        if not passing:
            row.failed += 1
        self._redraw()

    def finalize(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        self._redraw(final=True)
        if self._interactive and self._printed_lines:
            click.echo()

    def _redraw(self, *, final: bool = False) -> None:
        if not self._started:
            return
        rendered = self._render()
        if not rendered:
            return
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        if self._interactive and self._printed_lines:
            sys.stdout.write(f"\033[{self._printed_lines}F")
            for line in rendered:
                sys.stdout.write("\033[2K")
                sys.stdout.write(line + "\n")
            sys.stdout.flush()
            self._printed_lines = len(rendered)
        elif self._interactive and not self._printed_lines:
            for line in rendered:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()
            self._printed_lines = len(rendered)
        elif not self._interactive and final:
            for line in rendered:
                click.echo(line)

    def _render(self) -> list[str]:
        out: list[str] = []
        for cat in _CATEGORY_ORDER:
            row = self.rows[cat]
            if row.total == 0:
                continue
            label = cat.value.upper().ljust(16)
            ratio = row.done / row.total if row.total else 0.0
            filled = round(ratio * _BAR_WIDTH)
            bar = _BLOCK_FULL * filled + _BLOCK_EMPTY * (_BAR_WIDTH - filled)
            colored_bar = _truecolor(bar, _CATEGORY_COLORS[cat], bold=True)
            count = f"{row.done}/{row.total}".rjust(7)
            tail = ""
            if row.done >= row.total:
                if row.failed == 0:
                    tail = "  " + _truecolor("✓", (74, 222, 128))
                else:
                    tail = "  " + _truecolor(f"✗ {row.failed} failed", (239, 68, 68))
            out.append(f"  {label}  {colored_bar}  {count}{tail}")
        return out


def category_totals_from_specs(
    specs: Iterable[object],
) -> dict[InspectionCategory, int]:
    counts: dict[InspectionCategory, int] = {cat: 0 for cat in _CATEGORY_ORDER}
    for spec in specs:
        if not isinstance(spec, InspectionSpec):
            continue
        cat = spec.category
        counts[cat] = counts.get(cat, 0) + 1
    return counts
