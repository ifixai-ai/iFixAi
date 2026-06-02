import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ifixai.core.types import ConversationPlan, EvaluationCriteria, InspectionStep

_TESTS_DIR = Path(__file__).parent.parent / "inspections"
_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "definition.schema.json"

class RuleLoadError(Exception):
    pass

class RuleLoader:
    def __init__(self, rules_dir: Path | None = None) -> None:
        self.rules_dir = rules_dir or _TESTS_DIR

    def load_rules(self, test_id: str) -> ConversationPlan:
        yaml_path = self._find_rule_file(test_id)
        if yaml_path is None:
            return ConversationPlan(
                test_id=test_id,
                steps=[],
                fixture_requirements=[],
            )

        raw = self._read_yaml(yaml_path)
        return self._parse_plan(raw, test_id)

    def load_all_rules(self) -> dict[str, ConversationPlan]:
        plans: dict[str, ConversationPlan] = {}
        if not self.rules_dir.exists():
            return plans

        for yaml_path in sorted(self.rules_dir.glob("b*/definition.yaml")):
            raw = self._read_yaml(yaml_path)
            bid = raw.get("test_id", yaml_path.parent.name)
            plans[bid] = self._parse_plan(raw, bid)

        return plans

    def _find_rule_file(self, test_id: str) -> Path | None:
        if not self.rules_dir.exists():
            return None

        short_id = test_id.replace("SSCI-", "").lower()

        for yaml_path in self.rules_dir.glob(f"{short_id}_*/definition.yaml"):
            return yaml_path

        return None

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        try:
            with open(path) as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict):
                raise RuleLoadError(f"Rule file {path} must be a mapping")
            return data
        except yaml.YAMLError as exc:
            raise RuleLoadError(f"Invalid YAML in {path}: {exc}") from exc

    def _parse_plan(
        self,
        raw: dict[str, Any],
        default_id: str,
    ) -> ConversationPlan:
        steps: list[InspectionStep] = []
        for step_raw in raw.get("steps", []):
            eval_raw = step_raw.get("evaluation", {})
            criteria = EvaluationCriteria(
                expected_outcome=eval_raw.get("expected_outcome", "refuse"),
                refuse_patterns=eval_raw.get("refuse_patterns", []),
                comply_patterns=eval_raw.get("comply_patterns", []),
                required_keywords=eval_raw.get("required_keywords", []),
                forbidden_keywords=eval_raw.get("forbidden_keywords", []),
                structural_checks=eval_raw.get("structural_checks", []),
                pass_weight=eval_raw.get("pass_weight", 1.0),
                partial_weight=eval_raw.get("partial_weight", 0.5),
                deterministic_forbidden_veto=eval_raw.get(
                    "deterministic_forbidden_veto", False
                ),
            )
            steps.append(
                InspectionStep(
                    step_id=step_raw.get("step_id", len(steps) + 1),
                    prompt_template=step_raw.get("prompt_template", ""),
                    variables_from=step_raw.get("variables_from", "fixture"),
                    evaluation=criteria,
                    score=step_raw.get("score", True),
                )
            )

        return ConversationPlan(
            test_id=raw.get("test_id", default_id),
            steps=steps,
            fixture_requirements=raw.get("fixture_requirements", []),
        )

_DEFAULT_LOADER: RuleLoader | None = None

def load_inspection_definition(test_id: str) -> ConversationPlan | None:
    global _DEFAULT_LOADER
    if _DEFAULT_LOADER is None:
        _DEFAULT_LOADER = RuleLoader()

    plan = _DEFAULT_LOADER.load_rules(test_id)
    if not plan.steps:
        return None

    yaml_path = _DEFAULT_LOADER._find_rule_file(test_id)
    if yaml_path is not None and _SCHEMA_PATH.exists():
        raw = _DEFAULT_LOADER._read_yaml(yaml_path)
        with open(_SCHEMA_PATH) as fh:
            schema = json.load(fh)
        try:
            jsonschema.validate(instance=raw, schema=schema)
        except jsonschema.ValidationError as exc:
            raise RuleLoadError(
                f"Schema validation failed for {test_id}: {exc.message}"
            ) from exc

    return plan
