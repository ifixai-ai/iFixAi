"""Real keystrokes through the arrow-key menu (needs ``questionary`` installed)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ifixai.cli import pro_offer, ui
from ifixai.cli.pro_offer import PRO_URL, RunDecision

pytest.importorskip("questionary")
application = pytest.importorskip("prompt_toolkit.application")
keyboard = pytest.importorskip("prompt_toolkit.input")
screen = pytest.importorskip("prompt_toolkit.output")

UP_ARROW = "\x1b[A"
DOWN_ARROW = "\x1b[B"
ENTER = "\r"
CTRL_C = "\x03"


class BrowserRecorder:
    """Stands in for ``webbrowser.open`` and records the URLs it was asked for."""

    def __init__(self) -> None:
        self.opened_urls: list[str] = []

    def __call__(self, url: str) -> bool:
        self.opened_urls.append(url)
        return True


def report_terminal_attached() -> bool:
    return True


@pytest.fixture(autouse=True)
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the offer's shown-once marker in ``tmp_path``, never the real home."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture
def keys() -> Iterator[Any]:
    """A keyboard the test types on; the menus read from it instead of a console."""
    with keyboard.create_pipe_input() as pipe:
        with application.create_app_session(input=pipe, output=screen.DummyOutput()):
            yield pipe


def test_enter_alone_picks_the_default(keys: Any) -> None:
    keys.send_text(ENTER)

    assert ui.select_or_abort("Pick one", ["first", "second"], "second") == "second"


def test_arrows_move_the_pointer_before_enter(keys: Any) -> None:
    keys.send_text(UP_ARROW + ENTER)

    assert ui.select_or_abort("Pick one", ["first", "second"], "second") == "first"


def test_ctrl_c_raises_instead_of_answering_with_the_default(keys: Any) -> None:
    keys.send_text(CTRL_C)

    with pytest.raises(KeyboardInterrupt):
        ui.select_or_abort("Pick one", ["first", "second"], "second")


def test_the_existing_select_still_answers_a_cancel_with_its_default(
    keys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    keys.send_text(CTRL_C)

    assert ui.select("Pick one", ["first", "second"], default="second") == "second"


@pytest.mark.parametrize(
    "typed,expected,expected_urls",
    [
        (ENTER, RunDecision.CONTINUE, []),
        (UP_ARROW + ENTER + ENTER, RunDecision.CONTINUE, [PRO_URL]),
        (UP_ARROW + ENTER + DOWN_ARROW + ENTER, RunDecision.ABANDON, [PRO_URL]),
    ],
)
def test_the_offer_end_to_end_with_real_keystrokes(
    keys: Any,
    monkeypatch: pytest.MonkeyPatch,
    typed: str,
    expected: RunDecision,
    expected_urls: list[str],
) -> None:
    browser = BrowserRecorder()
    monkeypatch.setattr(pro_offer.webbrowser, "open", browser)
    monkeypatch.setattr(pro_offer, "can_prompt_user", report_terminal_attached)
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    keys.send_text(typed)

    assert pro_offer.offer_pro_version() is expected
    assert browser.opened_urls == expected_urls
