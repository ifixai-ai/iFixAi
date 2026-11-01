"""Every artifact text field obeys the existing credential-scrubbing boundary."""

import json

import pytest

from ifixai.core.types import EvidenceItem, TestResult, TestRunResult
from ifixai.reporting.artifact import _build_payload, render_artifact

# Synthetic only; not a credential.
TOKEN = "sk-" + "synthetic_artifact_token_1234567890"


@pytest.mark.parametrize("field", ["metadata", "error", "evaluation", "abort"])
def test_artifact_scrubs_known_credentials_in_all_embedded_text(field):
    result = TestRunResult(test_results=[TestResult(test_id="B19")])
    if field == "metadata":
        result.system_name = TOKEN
        result.fixture_name = TOKEN
    elif field == "error":
        result.test_results[0].error_message = TOKEN
    elif field == "evaluation":
        result.test_results[0].evidence = [EvidenceItem(test_case_id="probe", evaluation_result=TOKEN)]
    else:
        result.partial = True
        result.abort_reason = TOKEN
    kwargs = dict(live=True, transport="http", sut_model=None,
                  judge_model=None, honesty_note="", previous=None)
    payload = _build_payload(result, **kwargs)
    assert TOKEN not in json.dumps(payload)
    assert TOKEN not in render_artifact(result, **kwargs)
    # Rendering does not mutate the original evidence object.
    assert TOKEN in result.model_dump_json()
