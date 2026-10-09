"""A rubric says what to grade; only the harness says how the judge must answer.

`build_judge_prompt` appends the output contract (`{"dimensions": [...]}`). A rubric template
that also prescribes its own JSON shape gives the judge two conflicting instructions: a judge
that follows the rubric's shape has every verdict discarded as a contract error. That happened
on a real run with google/gemini-2.5-flash (581 of 1,428 grading attempts dropped) because six
templates still ended with the pre-harness instruction B14 and B17 had already lost.
"""

import json
from pathlib import Path

import pytest
import yaml

from ifixai.core.types import AnalyticRubric
from ifixai.evaluation.analytic_judge import (
    JudgeContractError,
    build_judge_prompt,
    parse_rubric_verdict,
)

INSPECTIONS_DIR = Path(__file__).resolve().parents[1] / "inspections"
RUBRIC_PATHS = sorted(INSPECTIONS_DIR.glob("*/rubric*.yaml"))
PRE_HARNESS_REPLY_KEYS = ('"verdict"', '"dimension_scores"', '"weighted_score"')
ONCE_CONFLICTING_FOLDERS = [
    "b07_hallucination_rate",
    "b08_privilege_escalation",
    "b09_policy_violation",
    "b11_system_controllability",
    "b12_prompt_injection",
    "b13_plan_traceability",
]


def load_rubric_file(path: Path) -> AnalyticRubric:
    return AnalyticRubric(**yaml.safe_load(path.read_text(encoding="utf-8")))


def describe_rubric_path(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def extract_rubric_guidance(prompt: str) -> str:
    """The part of the judge prompt that comes verbatim from the rubric template."""
    start = prompt.find("Inspection guidance:")
    if start == -1:
        return ""
    return prompt[start : prompt.find("Evaluation context:")]


def test_every_inspection_rubric_is_covered() -> None:
    assert len(RUBRIC_PATHS) >= 40
    assert {path.parent.name for path in RUBRIC_PATHS} >= set(ONCE_CONFLICTING_FOLDERS)


@pytest.mark.parametrize("path", RUBRIC_PATHS, ids=describe_rubric_path)
def test_rubric_guidance_does_not_prescribe_a_reply_shape(path: Path) -> None:
    prompt = build_judge_prompt(load_rubric_file(path), context="contract check")
    guidance = extract_rubric_guidance(prompt)
    assert prompt.count("OUTPUT CONTRACT") == 1
    assert "Respond with exactly one JSON object" not in guidance
    for key in PRE_HARNESS_REPLY_KEYS:
        assert key not in guidance, f"{describe_rubric_path(path)} still prescribes {key}"


@pytest.mark.parametrize("folder", ONCE_CONFLICTING_FOLDERS)
def test_rubric_keeps_its_grading_guidance(folder: str) -> None:
    """Only the reply-shape instruction goes; the per-dimension guidance stays."""
    rubric = load_rubric_file(INSPECTIONS_DIR / folder / "rubric.yaml")
    guidance = extract_rubric_guidance(build_judge_prompt(rubric, context="contract check"))
    for dimension in rubric.dimensions:
        assert dimension.name in guidance, f"{folder} guidance lost {dimension.name}"


@pytest.mark.parametrize("folder", ONCE_CONFLICTING_FOLDERS)
def test_pre_harness_reply_shape_is_still_rejected(folder: str) -> None:
    """The parser stays strict: the fix is one instruction in the prompt, not two shapes."""
    rubric = load_rubric_file(INSPECTIONS_DIR / folder / "rubric.yaml")
    reply = json.dumps(
        {
            "verdict": "pass",
            "confidence": 1.0,
            "dimension_scores": {dimension.name: 1 for dimension in rubric.dimensions},
            "weighted_score": 1.0,
            "reasoning": "fine",
        }
    )
    with pytest.raises(JudgeContractError):
        parse_rubric_verdict(reply, rubric)
