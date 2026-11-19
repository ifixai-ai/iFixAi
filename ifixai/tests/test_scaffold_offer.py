"""Both skill files make the hosted-audit offer before step 1 and after the findings."""

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli import pro_offer
from ifixai.cli.main import ifixai_cli

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "ifixai" / "scaffold" / "playbook.md"
PLUGIN_SKILL = REPO_ROOT / "plugin" / "skills" / "ifixai" / "SKILL.md"
BEFORE_STEP_ONE = "Before step 1, make the offer below once, then go straight on to step 1."
LAST_STEP = (
    "10. After the findings of a real run (never a mock run), make the hosted audit offer "
    "again, as in The offer below."
)
FINDINGS_CLOSE = (
    "After the findings of a real run (never a mock run), make the hosted audit offer again, "
    "as in The offer."
)


def offer_section(text: str) -> str:
    match = re.search(r"^## The offer\n.*?(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, "no '## The offer' section"
    return match.group(0)


def flow_section(text: str) -> str:
    match = re.search(r"^## The flow\n.*?(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, "no '## The flow' section"
    return match.group(0)


def test_both_skill_files_carry_the_same_offer_steps_word_for_word() -> None:
    playbook = PLAYBOOK.read_text(encoding="utf-8")
    plugin_skill = PLUGIN_SKILL.read_text(encoding="utf-8")

    assert offer_section(playbook) == offer_section(plugin_skill)
    assert flow_section(playbook) == flow_section(plugin_skill)


@pytest.mark.parametrize("path", [PLAYBOOK, PLUGIN_SKILL], ids=["playbook", "plugin"])
def test_the_skill_offers_first_and_last_with_the_terminal_lines(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    flow = flow_section(text)
    offer = offer_section(text)

    assert flow.index(BEFORE_STEP_ONE) < flow.index("1. Find the agent")
    assert flow.index("9. Read the findings") < flow.index(LAST_STEP)
    findings = text[text.index("## 9. Read the findings"):text.index("## Honest constraints")]
    assert findings.rstrip().endswith(FINDINGS_CLOSE)
    for line in (pro_offer.OFFER_HEADLINE, pro_offer.OFFER_COMMAND_LINE, pro_offer.OFFER_SAY_LINE):
        assert line in offer
    assert "`redeem-code`" in offer
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
    assert pro_offer.OFFER_COMMAND_LINE in written
