import pytest

from ifixai.harness.validator import LayoutValidationError, validate_layout


@pytest.mark.parametrize(
    "folder_name",
    ["b33_new_inspection", "v11_new_inspection", "b01-wrong-separator", "B01_uppercase"],
)
def test_validator_rejects_inspection_like_folders_with_invalid_names(
    tmp_path, folder_name
):
    (tmp_path / folder_name).mkdir()

    with pytest.raises(LayoutValidationError, match="invalid inspection folder name"):
        validate_layout(tmp_path)


def test_validator_ignores_python_cache_and_support_folders(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "_shared").mkdir()

    assert validate_layout(tmp_path) == []
