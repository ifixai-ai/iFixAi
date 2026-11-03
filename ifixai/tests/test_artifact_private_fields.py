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


KWARGS = dict(live=True, transport="http", sut_model=None,
              judge_model=None, honesty_note="", previous=None)
# Synthetic only; one per "sk-" key family the scrubber knows.
SK_KEYS = [f"sk-{family}{TOKEN[3:]}" for family in ("", "proj-", "or-v1-", "orca-", "ant-api03-", "cp-")]


@pytest.mark.parametrize("name", ["helpdesk-assistant-production", "risk-assessment-assistant-eu"])
def test_artifact_keeps_agent_names_that_contain_sk(name):
    result = TestRunResult(system_name=name, test_results=[TestResult(test_id="B19")])
    assert _build_payload(result, **KWARGS)["meta"]["system_name"] == name
    assert "REDACTED" not in render_artifact(result, **KWARGS)


@pytest.mark.parametrize("key", SK_KEYS)
@pytest.mark.parametrize("before", ["", "helpdesk ", "key=", "'", '"', "Bearer "])
def test_artifact_scrubs_sk_keys_after_a_separator(key, before):
    result = TestRunResult(system_name=f"{before}{key}", test_results=[TestResult(test_id="B19")])
    assert TOKEN[3:] not in render_artifact(result, **KWARGS)
