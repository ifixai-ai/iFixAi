import pytest
import yaml

from ifixai.harness.prompt_pool import PromptPoolError, load_phrasing_pool


@pytest.mark.parametrize(
    "phrasings", [["hello", ""], ["hello", "   "], ["hello", None], ["hello", 12]]
)
def test_invalid_phrasings_raise_pool_error(tmp_path, phrasings):
    path = tmp_path / "pool.yaml"
    path.write_text(
        yaml.safe_dump(
            {"groups": [{"id": "g", "category": "c", "phrasings": phrasings}]}
        )
    )
    with pytest.raises(PromptPoolError, match="phrasing"):
        load_phrasing_pool(path)


def test_valid_phrasings_preserve_original_text(tmp_path):
    path = tmp_path / "pool.yaml"
    phrasings = ["Hello?", "  Hi there?  "]
    path.write_text(
        yaml.safe_dump(
            {"groups": [{"id": "g", "category": "c", "phrasings": phrasings}]}
        )
    )
    assert load_phrasing_pool(path)[0].phrasings == phrasings
