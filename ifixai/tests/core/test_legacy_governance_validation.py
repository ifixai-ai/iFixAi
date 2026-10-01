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
