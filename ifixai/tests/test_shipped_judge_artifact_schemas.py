"""The authoring schemas accept the contracts actually shipped by the package."""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from ifixai.rules.loader import load_inspection_definition

_ROOT = Path(__file__).parents[1]
_ARTIFACTS = [
    (kind, path)
    for kind in ("rubric", "references")
    for path in sorted((_ROOT / "inspections").glob(f"*/{kind}*.yaml"))
]


@pytest.mark.parametrize("kind,path", _ARTIFACTS, ids=[p.parent.name + "/" + p.name for _, p in _ARTIFACTS])
def test_shipped_judge_artifacts_match_their_authoring_schema(kind, path):
    schema = json.loads((_ROOT / "schemas" / f"{kind}.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(yaml.safe_load(path.read_text()))


@pytest.mark.parametrize("test_id", ["P27", "P32", "S02"])
def test_frontier_definitions_load_through_the_public_schema_checked_reader(test_id):
    plan = load_inspection_definition(test_id)
    assert plan is not None
    assert plan.test_id == test_id
    assert plan.steps


@pytest.mark.parametrize("kind", ["rubric", "references"])
def test_noncanonical_identifiers_remain_rejected(kind):
    schema = json.loads((_ROOT / "schemas" / f"{kind}.schema.json").read_text())
    sample = next(p for k, p in _ARTIFACTS if k == kind)
    document = yaml.safe_load(sample.read_text())
    document["test_id"] = "P1"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(document)
