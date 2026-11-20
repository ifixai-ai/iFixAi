"""Real keystrokes through the arrow-key menu (needs ``questionary`` installed)."""

from collections.abc import Iterator
from typing import Any

import click
import pytest

from ifixai.cli import ui

pytest.importorskip("questionary")
application = pytest.importorskip("prompt_toolkit.application")
keyboard = pytest.importorskip("prompt_toolkit.input")
screen = pytest.importorskip("prompt_toolkit.output")

UP_ARROW = "\x1b[A"
ENTER = "\r"
CTRL_C = "\x03"


def report_terminal_attached() -> bool:
    return True


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


def test_select_aborts_cancel_instead_of_answering_with_its_default(
    keys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    keys.send_text(CTRL_C)

    with pytest.raises(click.Abort):
        ui.select("Pick one", ["first", "second"], default="second")
