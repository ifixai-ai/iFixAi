from pathlib import Path

import pytest
import yaml

from ifixai.core.fixture_loader import (
    FixtureValidationError,
    load_fixture,
    validate_fixture,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("regulations", "B01"),
        ("regulations", None),
        ("policies", ""),
        ("policies", None),
    ],
)
def test_invalid_legacy_governance_fields_are_not_discarded(
    tmp_path: Path, field: str, value: object
) -> None:
    fixture = yaml.safe_load(
        (Path(__file__).parents[2] / "fixtures/examples/healthcare.yaml").read_text()
    )
    fixture.pop("metadata")  # Exercise the legacy normalization path.
    fixture[field] = value
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(fixture))

    errors = validate_fixture(path)
    assert any(f"{value!r} is not of type" in error for error in errors)

    with pytest.raises(FixtureValidationError):
        load_fixture(path)


def test_missing_legacy_governance_fields_keep_defaults(tmp_path: Path) -> None:
    fixture = yaml.safe_load(
        (Path(__file__).parents[2] / "fixtures/examples/healthcare.yaml").read_text()
    )
    for field in ("metadata", "regulations", "policies"):
        fixture.pop(field)
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(fixture))

    assert validate_fixture(path) == []


@pytest.mark.parametrize("key", ["b01_tool_governance", "b02_response_consistency", "b08_privilege_escalation"])
def test_legacy_mapping_cases_load_with_schema_test_identifiers(tmp_path, key):
    fixture = yaml.safe_load(
        (Path(__file__).parents[2] / "fixtures/examples/healthcare.yaml").read_text()
    )
    fixture.pop("metadata")
    fixture["test_cases"] = {key: [{
        "id": "legacy-case", "scenario": "Read only the authorized record",
        "expected": "allow", "user_role": "reader", "tool": "read_record",
    }]}
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
    assert validate_fixture(path) == []
    restored = load_fixture(path)
    assert len(restored.test_cases) == 1
    case = restored.test_cases[0]
    assert case.test == f"SSCI-{key.split('_', 1)[0].upper()}"
    assert (case.test_id, case.tool_id, case.expected_result) == (
        "legacy-case", "read_record", "allow",
    )
