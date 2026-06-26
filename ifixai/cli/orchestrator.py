import io
import os
import sys
import threading

import click

from ifixai.api import run_inspections, run_selected, run_single, run_strategic
from ifixai.core.concurrency import ConcurrencyGovernor
from ifixai.core.fixture_loader import load_fixture
from ifixai.harness.registry import ALL_SPECS, SPEC_BY_ID
from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.providers.resolver import (
    _PROVIDER_CREDENTIAL_ENV_VARS,
    detect_available_credentials,
    select_cross_provider_judge,
)
from ifixai.scoring.category_weights import STRATEGIC_TEST_IDS
from ifixai.core.types import (
    TestResult,
    TestRunResult,
    EvaluationPipelineConfig,
    InspectionCategory,
    TestStatus,
)
from ifixai.cli.schemas import EvalModeResolution


def _enable_windows_vt_processing() -> bool:
    """Turn on virtual-terminal processing on the Windows console.

    The animated progress display uses ANSI cursor-up (`\\033[{n}A`) to
    redraw inspection lines in place. Older Windows consoles print those
    escapes literally, so each redraw stacks new lines instead of
    overwriting and the user sees the same inspection name reprinted on
    every spinner frame. Returns True when VT processing is on; False on
    any failure (caller should fall back to the plain progress printer).
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        invalid_handle_value = ctypes.c_void_p(-1).value
        if handle in (0, invalid_handle_value):
            return False

        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False

        return bool(
            kernel32.SetConsoleMode(
                handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
        )
    except (OSError, AttributeError, ImportError):
        return False


def _lookup_env_api_key(provider_name: str) -> str | None:
    env_vars = _PROVIDER_CREDENTIAL_ENV_VARS.get(provider_name.lower(), ())
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            return value
    return None


def _resolve_standard_eval_mode(
    sut_provider: str | None,
    judge_provider: tuple[str, ...],
    sut_api_key: str | None = None,
) -> EvalModeResolution:
    if judge_provider:
        return EvalModeResolution(mode="semantic", auto_selected_judge=None)
    sut_name = (sut_provider or "").lower()
    available = detect_available_credentials(os.environ)
    if sut_name and sut_name not in available and sut_api_key:
        available = [sut_name] + available
    distinct = [p for p in available if p != sut_name]
    if distinct:
        chosen = select_cross_provider_judge(sut_name, available)
        if chosen is not None:
            return EvalModeResolution(mode="semantic", auto_selected_judge=chosen)
    if not available:
        click.echo(
            click.style(
                "Error: no provider credentials found in the environment.\n"
                "Set at least one of OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                "GEMINI_API_KEY, AZURE_OPENAI_API_KEY, OPENROUTER_API_KEY, "
                "HUGGINGFACE_API_TOKEN, or AWS credentials (for bedrock).",
                fg="red",
            ),
            err=True,
        )
        sys.exit(2)
    click.echo(
        click.style(
            "Error: Standard mode needs a second distinct-provider credential to "
            "avoid self-judge. Either set a second provider key (e.g. "
            "ANTHROPIC_API_KEY alongside OPENAI_API_KEY), or pass --eval-mode self "
            "to opt into the biased self-judge path explicitly.",
            fg="red",
        ),
        err=True,
    )
    sys.exit(2)


def _resolve_judge_label(eval_mode: str, judge_provider: tuple[str, ...]) -> str:
    if eval_mode == "self":
        return "self"
    if eval_mode == "deterministic":
        return "(none)"
    if len(judge_provider) >= 2:
        return f"ensemble({len(judge_provider)})"
    if len(judge_provider) == 1:
        return judge_provider[0]
    return "(unconfigured)"


def _eval_mode_declaration(
    eval_mode: str,
    sut_provider: str | None,
    judge_providers: tuple[str, ...],
    judge_models: tuple[str, ...],
) -> str:
    if eval_mode == "self":
        return (
            "Evaluation mode: self "
            f"(system-under-test '{sut_provider}' acts as its own judge -- "
            "Standard mode default)"
        )
    if eval_mode == "deterministic":
        return (
            "Evaluation mode: deterministic "
            "(structural inspections only -- text-fallback tests will be inconclusive)"
        )
    if eval_mode == "semantic":
        judge_id = judge_providers[0] if judge_providers else "(none)"
        model = f"/{judge_models[0]}" if judge_models else ""
        return f"Evaluation mode: semantic (judge: {judge_id}{model})"
    if eval_mode == "full":
        judges = "+".join(judge_providers) if judge_providers else "(none)"
        return f"Evaluation mode: full (ensemble: {judges})"
    return f"Evaluation mode: {eval_mode}"


def _build_judge_config(
    eval_mode: str,
    sut_provider: str | None,
    sut_api_key: str,
    sut_model: str | None,
    sut_endpoint: str | None = None,
    judge_providers: tuple[str, ...] = (),
    judge_api_keys: tuple[str, ...] = (),
    judge_models: tuple[str, ...] = (),
    max_calls: int = 200,
    timeout: int = 30,
) -> JudgeConfig | None:
    if eval_mode == "deterministic":
        return None
    if eval_mode == "self":
        return JudgeConfig(
            provider=sut_provider or "",
            api_key=sut_api_key,
            model=sut_model,
            endpoint=sut_endpoint,
            max_calls_per_run=max_calls,
            timeout=timeout,
        )
    if eval_mode in ("single", "semantic"):
        return JudgeConfig(
            provider=judge_providers[0],
            api_key=judge_api_keys[0] if judge_api_keys else "",
            model=judge_models[0] if judge_models else None,
            max_calls_per_run=max_calls,
            timeout=timeout,
        )
    if eval_mode == "full":
        specs = [
            JudgeProviderSpec(
                provider=p,
                api_key=judge_api_keys[i] if i < len(judge_api_keys) else "",
                model=judge_models[i] if i < len(judge_models) else None,
            )
            for i, p in enumerate(judge_providers)
        ]
        return JudgeConfig(
            providers=specs,
            max_calls_per_run=max_calls,
            timeout=timeout,
        )
    return None


def _print_error_summary(result: TestRunResult) -> None:
    """Surface configuration failures (TestStatus.ERROR) as a loud red banner.

    ERROR is distinct from INCONCLUSIVE: it means the run was incomplete,
    not that the system was scored and returned an unscorable verdict.
    Listing the affected test IDs lets operators see exactly which
    benchmarks did not execute.
    """
    from ifixai.providers.base import friendly_provider_message

    errored = [br for br in result.test_results if br.status == TestStatus.ERROR]
    if not errored:
        return

    total = len(result.test_results)
    test_ids = ", ".join(sorted(br.test_id for br in errored))
    click.echo(
        click.style(
            f"Run incomplete: {len(errored)} of {total} benchmarks could not "
            f"execute (configuration failure): {test_ids}",
            fg="red",
            bold=True,
        )
    )

    grouped: dict[str, list[str]] = {}
    for br in errored:
        reason = br.error_message or br.error or "no detail available"
        grouped.setdefault(reason, []).append(br.test_id)

    for reason, ids in grouped.items():
        friendly = friendly_provider_message(reason)
        affected = ", ".join(sorted(ids))
        if friendly is not None:
            click.echo(click.style(f"  {friendly}", fg="yellow", bold=True))
            click.echo(click.style(f"    Affected: {affected}", fg="red"))
            click.echo(click.style(f"    Details:  {reason}", dim=True))
        else:
            click.echo(click.style(f"  - {affected}: {reason}", fg="red"))


# Basic 16-color ANSI foreground codes only. xterm-256 (`38;5;`) and truecolor
# (`38;2;`) render as the default foreground (gray) on terminals that lack
# extended-color support, so the bars must stay in the universally-supported
# SGR 30-37 / 90-97 range. Ordered to alternate bright/normal and jump hue so
# adjacent categories always contrast. Excludes black/white/gray, which would
# be invisible against the terminal background.
_BAR_BASE_COLORS: tuple[str, ...] = (
    "91",  # bright red
    "92",  # bright green
    "94",  # bright blue
    "93",  # bright yellow
    "95",  # bright magenta
    "96",  # bright cyan
    "31",  # red
    "32",  # green
    "34",  # blue
    "33",  # yellow
    "35",  # magenta
    "36",  # cyan
)
# Intensity prefixes layered on top of the base colors once they are exhausted,
# so a 13th+ category reuses a hue but at a visibly different intensity (dim,
# then bold). 12 colors x 3 intensities = 36 distinct bars before any repeat.
_BAR_INTENSITIES: tuple[str, ...] = ("", "\033[2m", "\033[1m")


def _build_category_bar_palette() -> dict[str, str]:
    """Assign each InspectionCategory a distinct, stable bar color.

    Colors are taken from the basic 16-color ANSI palette so they render on
    every terminal (256-color codes showed as gray on consoles without
    extended-color support). Deterministic — the same category always renders
    the same color — and it scales past the 12 base colors by cycling an
    intensity modifier (normal -> dim -> bold), so new categories keep getting
    a distinct bar without the old single-fallback-color collapse.
    """
    palette: dict[str, str] = {}
    base_count = len(_BAR_BASE_COLORS)
    intensity_count = len(_BAR_INTENSITIES)
    for index, category in enumerate(InspectionCategory):
        color = _BAR_BASE_COLORS[index % base_count]
        intensity = _BAR_INTENSITIES[(index // base_count) % intensity_count]
        palette[category.value] = f"{intensity}\033[{color}m"
    return palette


_CATEGORY_BAR_COLOR: dict[str, str] = _build_category_bar_palette()
_RESET = "\033[0m"
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class BenchmarkProgressDisplay:
    """Live animated display: pre-prints all benchmarks then updates in-place.

    The display owns the terminal during the run. Any concurrent stdout or
    stderr write from the inspection pipeline (httpx retries, asyncio
    diagnostics, library warnings) would shift the cursor between spinner
    frames and corrupt the in-place redraw, producing repeated rows for
    the same inspection. To prevent that, `start()` swaps `sys.stdout` and
    `sys.stderr` for an in-memory buffer; the display's own writes go to
    a saved real-stdout reference. `stop()` restores the streams and
    flushes the captured buffer below the display so nothing is lost.
    """

    def __init__(self, tests: list[tuple[str, str]]) -> None:
        self._tests = tests  # [(test_id, name), ...]
        self._results: dict[str, TestResult] = {}
        self._frame_idx = 0
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._real_stdout = sys.stdout
        self._real_stderr = sys.stderr
        self._capture_buffer: io.StringIO | None = None
        self._streams_swapped = False

    def start(self) -> None:
        self._real_stdout = sys.stdout
        self._real_stderr = sys.stderr
        for test_id, name in self._tests:
            self._real_stdout.write(
                f"  {_YELLOW}⠋{_RESET} {_DIM}{test_id}{_RESET} {name}\n"
            )
        self._real_stdout.flush()

        # Redirect stray writes to a buffer so they cannot corrupt the
        # cursor math between redraws. The buffer is replayed in stop().
        self._capture_buffer = io.StringIO()
        sys.stdout = self._capture_buffer
        sys.stderr = self._capture_buffer
        self._streams_swapped = True

        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def update(self, test_id: str, index: int, total: int, result: TestResult) -> None:
        with self._lock:
            self._results[test_id] = result

    def stop(self) -> None:
        self._done.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._redraw(final=True)

        if self._streams_swapped:
            sys.stdout = self._real_stdout
            sys.stderr = self._real_stderr
            self._streams_swapped = False
            captured = self._capture_buffer.getvalue() if self._capture_buffer else ""
            if captured.strip():
                self._real_stderr.write("\n")
                self._real_stderr.write(captured)
                self._real_stderr.flush()
            self._capture_buffer = None

    def _animate(self) -> None:
        while not self._done.wait(timeout=0.1):
            self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
            self._redraw()

    def _build_lines(self, final: bool = False) -> list[str]:
        lines: list[str] = []
        with self._lock:
            frame = _SPINNER_FRAMES[self._frame_idx]
            for test_id, name in self._tests:
                if test_id in self._results:
                    result = self._results[test_id]
                    if result.insufficient_evidence:
                        icon = f"{_YELLOW}⊘{_RESET}"
                        status = f"{_YELLOW}INCONCLUSIVE{_RESET}"
                        lines.append(
                            f"  {icon} {_BOLD}{test_id}{_RESET} {name} "
                            f"... {status} (insufficient evidence)"
                        )
                        continue
                    if result.passing:
                        icon = f"{_GREEN}✓{_RESET}"
                        status = f"{_GREEN}PASS{_RESET}"
                    else:
                        icon = f"{_RED}✗{_RESET}"
                        status = f"{_RED}FAIL{_RESET}"
                    lines.append(
                        f"  {icon} {_BOLD}{test_id}{_RESET} {name} "
                        f"... {status} ({result.score:.0%})"
                    )
                else:
                    spinner = "·" if final else frame
                    lines.append(
                        f"  {_YELLOW}{spinner}{_RESET} {_DIM}{test_id}{_RESET} {name}"
                    )
        return lines

    def _redraw(self, final: bool = False) -> None:
        n = len(self._tests)
        if n == 0:
            return
        lines = self._build_lines(final=final)
        out = self._real_stdout
        out.write(f"\033[{n}A")
        for line in lines:
            out.write(f"\r\033[K{line}\n")
        out.flush()


def _print_category_summary(result: TestRunResult) -> None:
    if not result.category_scores:
        return
    bar_width = 22
    click.echo()
    for cs in result.category_scores:
        color = _CATEGORY_BAR_COLOR.get(cs.category.value, "\033[96m")
        bar = color + ("█" * bar_width) + _RESET
        total_in_suite = len(cs.test_ids)
        failed = cs.test_count - cs.tests_passed
        inconclusive = total_in_suite - cs.test_count
        count_str = f"{cs.test_count}/{total_in_suite}"
        if total_in_suite == 0:
            count_str = "—"
            fail_str = f"{_DIM}not in this suite{_RESET}"
        elif cs.test_count == 0:
            fail_str = f"{_YELLOW}⊘ {inconclusive} inconclusive{_RESET}"
        elif failed > 0 and inconclusive > 0:
            fail_str = f"{_RED}× {failed} failed{_RESET}, {_YELLOW}⊘ {inconclusive} inconclusive{_RESET}"
        elif failed > 0:
            fail_str = f"{_RED}× {failed} failed{_RESET}"
        elif inconclusive > 0:
            fail_str = f"{_GREEN}✓ {cs.tests_passed} passed{_RESET}, {_YELLOW}⊘ {inconclusive} inconclusive{_RESET}"
        else:
            fail_str = f"{_GREEN}✓ all passed{_RESET}"
        name = cs.category.value.ljust(16)
        click.echo(f"  {name} [{bar}]  {count_str:>5}  {fail_str}")
    click.echo()


def _progress_callback_plain(
    bid: str,
    index: int,
    total: int,
    bench_result: TestResult,
) -> None:
    """Fallback used when stdout is not a TTY (e.g. piped/redirected)."""
    if bench_result.insufficient_evidence:
        click.echo(
            f"  [{index}/{total}] {bid} {bench_result.name} ... "
            f"{click.style('INCONCLUSIVE', fg='yellow')} (insufficient evidence)"
        )
        return
    status_label = (
        click.style("PASS", fg="green")
        if bench_result.passing
        else click.style("FAIL", fg="red")
    )
    click.echo(
        f"  [{index}/{total}] {bid} {bench_result.name} ... "
        f"{status_label} ({bench_result.score:.0%})"
    )


def _build_display_tests(
    strategic: bool,
    test_ids: tuple[str, ...],
) -> list[tuple[str, str]]:
    if test_ids:
        display: list[tuple[str, str]] = []
        for raw_id in test_ids:
            uid = raw_id.upper()
            spec = SPEC_BY_ID.get(uid)
            display.append((uid, spec.name if spec else uid))
        return display
    if strategic:
        strategic_set = set(STRATEGIC_TEST_IDS)
        return [(s.test_id, s.name) for s in ALL_SPECS if s.test_id in strategic_set]
    return [(s.test_id, s.name) for s in ALL_SPECS]


async def execute_tests(
    provider: str,
    api_key: str,
    fixture: str,
    endpoint: str | None,
    model: str | None,
    system_prompt: str | None,
    strategic: bool,
    test_ids: tuple[str, ...],
    timeout: int,
    system_name: str,
    system_version: str,
    pipeline_config: EvaluationPipelineConfig | None = None,
    judge_config: JudgeConfig | None = None,
    governor: ConcurrencyGovernor | None = None,
    sut_temperature: float = 0.0,
    sut_seed: int | None = None,
    run_nonce: str | None = None,
    self_judged: bool = False,
    progress_callback=None,
    holdout_ids: dict[str, str] | None = None,
) -> TestRunResult | None:

    try:
        loaded_fixture = load_fixture(fixture)
    except FileNotFoundError as exc:
        click.echo(click.style(f"Fixture error: {exc}", fg="red"))
        return None

    # Animated in-place display needs ANSI cursor escapes. On Windows,
    # those are inert unless VT processing is enabled on the console
    # handle; if it cannot be enabled, fall back to the plain printer
    # so each inspection prints once on completion instead of stacking
    # the same name on every spinner frame.
    use_display = (
        progress_callback is None
        and sys.stdout.isatty()
        and _enable_windows_vt_processing()
    )
    display: BenchmarkProgressDisplay | None = None

    if use_display:
        display = BenchmarkProgressDisplay(_build_display_tests(strategic, test_ids))
        display.start()
        effective_callback = display.update
    else:
        effective_callback = progress_callback or _progress_callback_plain

    try:
        if len(test_ids) == 1:
            test_id = test_ids[0]
            single_result = await run_single(
                test_id=test_id,
                provider=provider,
                api_key=api_key,
                fixture=fixture,
                endpoint=endpoint,
                model=model,
                system_prompt=system_prompt,
                timeout=timeout,
                pipeline_config=pipeline_config,
                judge_config=judge_config,
                sut_temperature=sut_temperature,
                sut_seed=sut_seed,
                run_nonce=run_nonce,
                holdout_ids=holdout_ids,
            )
            if display:
                display.update(test_id, 1, 1, single_result)
            else:
                status_label = (
                    click.style("PASS", fg="green")
                    if single_result.passing
                    else click.style("FAIL", fg="red")
                )
                click.echo(
                    f"  [1/1] {test_id} {single_result.name} ... "
                    f"{status_label} ({single_result.score:.0%})"
                )
            return TestRunResult(
                system_name=system_name,
                system_version=system_version,
                provider=provider,
                fixture_name=loaded_fixture.metadata.name,
                overall_score=single_result.score,
                strategic_score=single_result.score,
                test_results=[single_result],
                run_mode="single",
            )

        if len(test_ids) >= 2:
            selected_result = await run_selected(
                test_ids={tid.upper() for tid in test_ids},
                provider=provider,
                api_key=api_key,
                fixture=fixture,
                system_name=system_name,
                system_version=system_version,
                endpoint=endpoint,
                model=model,
                system_prompt=system_prompt,
                timeout=timeout,
                progress_callback=effective_callback,
                pipeline_config=pipeline_config,
                judge_config=judge_config,
                governor=governor,
                sut_temperature=sut_temperature,
                sut_seed=sut_seed,
                run_nonce=run_nonce,
                holdout_ids=holdout_ids,
            )
            selected_result.self_judged = self_judged
            return selected_result

        if strategic:
            strategic_result = await run_strategic(
                provider=provider,
                api_key=api_key,
                fixture=fixture,
                system_name=system_name,
                system_version=system_version,
                endpoint=endpoint,
                model=model,
                system_prompt=system_prompt,
                timeout=timeout,
                progress_callback=effective_callback,
                pipeline_config=pipeline_config,
                judge_config=judge_config,
                governor=governor,
                sut_temperature=sut_temperature,
                sut_seed=sut_seed,
                run_nonce=run_nonce,
                holdout_ids=holdout_ids,
            )
            strategic_result.self_judged = self_judged
            return strategic_result

        inspections_result = await run_inspections(
            provider=provider,
            api_key=api_key,
            fixture=fixture,
            system_name=system_name,
            system_version=system_version,
            endpoint=endpoint,
            model=model,
            system_prompt=system_prompt,
            timeout=timeout,
            progress_callback=effective_callback,
            pipeline_config=pipeline_config,
            judge_config=judge_config,
            governor=governor,
            sut_temperature=sut_temperature,
            sut_seed=sut_seed,
            run_nonce=run_nonce,
        )
        inspections_result.self_judged = self_judged
        return inspections_result

    except Exception as exc:
        click.echo(click.style(f"Test execution failed: {exc}", fg="red"))
        return None

    finally:
        if display:
            display.stop()
