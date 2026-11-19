"""Real keystrokes through the arrow-key menu (needs ``questionary`` installed)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest

from ifixai.cli import pro_offer, ui
from ifixai.cli.pro_offer import RunDecision

pytest.importorskip("questionary")
application = pytest.importorskip("prompt_toolkit.application")
keyboard = pytest.importorskip("prompt_toolkit.input")
screen = pytest.importorskip("prompt_toolkit.output")

UP_ARROW = "\x1b[A"
DOWN_ARROW = "\x1b[B"
ENTER = "\r"
CTRL_C = "\x03"


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


def test_select_aborts_cancel_instead_of_answering_with_its_default(
    keys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    keys.send_text(CTRL_C)

    with pytest.raises(click.Abort):
        ui.select("Pick one", ["first", "second"], default="second")


# The menu lists the Pro audit first and starts on open source, so Enter alone
# continues the run and one Up arrow reaches the Pro audit.
@pytest.mark.parametrize(
    "typed,expected",
    [
        (ENTER, RunDecision.CONTINUE),
        (UP_ARROW + ENTER, RunDecision.ABANDON),
        (UP_ARROW + DOWN_ARROW + ENTER, RunDecision.CONTINUE),
    ],
)
def test_the_offer_end_to_end_with_real_keystrokes(
    keys: Any,
    monkeypatch: pytest.MonkeyPatch,
    typed: str,
    expected: RunDecision,
) -> None:
    monkeypatch.setattr(pro_offer, "can_prompt_user", report_terminal_attached)
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    keys.send_text(typed)

    assert pro_offer.offer_pro_version() is expected
