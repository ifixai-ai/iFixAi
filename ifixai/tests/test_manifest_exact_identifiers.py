import pytest
from pydantic import ValidationError

from ifixai.core.types import RunMode
from ifixai.evaluation.manifest import RunManifest, is_valid_run_nonce
from ifixai.evaluation.types import ModelDescriptor


def manifest(**changes):
    return RunManifest(
        **dict(
            run_id="run",
            timestamp="now",
            mode=RunMode.STANDARD,
            model_under_test=ModelDescriptor(
                provider="mock", model_id="test", version="1"
            ),
            normalizer_version="1",
            test_versions={},
            fixture_digest="a" * 64,
        )
        | changes
    )


def test_nonce_helper_rejects_trailing_newline():
    assert not is_valid_run_nonce("a" * 16 + "\n")


@pytest.mark.parametrize(
    "field,value", [("run_nonce", "a" * 16 + "\n"), ("fixture_digest", "a" * 64 + "\n")]
)
def test_manifest_rejects_trailing_newline(field, value):
    with pytest.raises(ValidationError):
        manifest(**{field: value})


def test_exact_identifiers_are_accepted():
    assert is_valid_run_nonce("a" * 16)
    assert manifest(run_nonce="a" * 16).run_nonce == "a" * 16
