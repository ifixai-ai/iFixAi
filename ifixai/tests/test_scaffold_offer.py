"""Both skill files make the free Pro audit offer before step 1 and show its steps last."""

import io
import os
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli import pro_offer
from ifixai.cli.main import ifixai_cli

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "ifixai" / "scaffold" / "playbook.md"
PLUGIN_SKILL = REPO_ROOT / "plugin" / "skills" / "ifixai" / "SKILL.md"
BEFORE_STEP_ONE = "Before step 1, make the free Pro audit offer in The offer below once."
LAST_STEP = (
    "10. After the findings of a real run (never a mock run), show the free Pro audit steps "
    "at the end\n    of step 9."
)
FINDINGS_INTRO = (
    "After the findings of a real run (never a mock run), show the free Pro audit steps, "
    "as they are,\nand ask nothing:"
)
CLAUDE_COMMAND = "claude mcp add --transport http --scope user ifixai https://mcp.ifixai.ai/mcp"


def offer_section(text: str) -> str:
    match = re.search(r"^## The offer\n.*?(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, "no '## The offer' section"
    return match.group(0)


def flow_section(text: str) -> str:
    match = re.search(r"^## The flow\n.*?(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, "no '## The flow' section"
    return match.group(0)


def findings_section(text: str) -> str:
    return text[text.index("## 9. Read the findings"):text.index("## Honest constraints")]


def code_blocks(text: str) -> list[str]:
    return re.findall(r"^```text\n(.*?)^```", text, flags=re.MULTILINE | re.DOTALL)


def terminal_screen(headline: tuple[str, ...], body: tuple[pro_offer.Paragraph, ...]) -> str:
    """The screen as the CLI prints it at 80 columns without colour."""
    lines = [line for text in headline for line in pro_offer.wrap(("", "", text), 79)]
    lines += [line for paragraph in body for line in pro_offer.wrap(paragraph, 79)]
    return "\n".join(lines)


def test_both_skill_files_carry_the_same_offer_steps_word_for_word() -> None:
    playbook = PLAYBOOK.read_text(encoding="utf-8")
    plugin_skill = PLUGIN_SKILL.read_text(encoding="utf-8")

    assert offer_section(playbook) == offer_section(plugin_skill)
    assert flow_section(playbook) == flow_section(plugin_skill)
    assert findings_section(playbook) == findings_section(plugin_skill)


@pytest.mark.parametrize("path", [PLAYBOOK, PLUGIN_SKILL], ids=["playbook", "plugin"])
def test_the_skill_offers_first_and_shows_the_steps_last(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    flow = flow_section(text)
    offer = offer_section(text)
    [start] = code_blocks(offer)
    findings = findings_section(text)
    [steps] = code_blocks(findings)

    assert flow.index(BEFORE_STEP_ONE) < flow.index("1. Find the agent")
    assert flow.index("9. Read the findings") < flow.index(LAST_STEP)
    assert findings.index(FINDINGS_INTRO) < findings.index(steps)
    assert findings.rstrip().endswith("```")
    assert start.split() == terminal_screen(
        pro_offer.START_HEADLINE, pro_offer.START_BODY
    ).split()
    assert steps.rstrip("\n") == terminal_screen(pro_offer.STEPS_HEADLINE, pro_offer.STEPS_BODY)
    assert "**Continue with a free Pro audit**" in offer
    assert "**Continue with open source**" in offer
    assert CLAUDE_COMMAND in " ".join(offer.split())
    assert "SF2026" in offer
    assert "dashboard" not in offer.lower()


def test_install_writes_a_claude_skill_that_offers_first_and_last(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        ifixai_cli, ["install", "--agents", "claude", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    [skill] = (tmp_path / ".claude" / "commands").glob("*.md")
    written = skill.read_text(encoding="utf-8")

    assert written.index(BEFORE_STEP_ONE) < written.index("1. Find the agent")
    assert written.index(LAST_STEP) > written.index("9. Read the findings")
    assert terminal_screen(pro_offer.STEPS_HEADLINE, pro_offer.STEPS_BODY) in written


def test_the_terminal_screen_helper_matches_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def eighty(*_: object, **__: object) -> os.terminal_size:
        return os.terminal_size((80, 24))

    monkeypatch.setattr(pro_offer.shutil, "get_terminal_size", eighty)

    assert pro_offer.render_screen(
        pro_offer.STEPS_HEADLINE, pro_offer.STEPS_BODY, io.StringIO()
    ) == terminal_screen(pro_offer.STEPS_HEADLINE, pro_offer.STEPS_BODY)
