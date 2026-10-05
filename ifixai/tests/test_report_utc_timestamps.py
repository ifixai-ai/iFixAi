import json
from datetime import datetime

import pytest

from ifixai.core.types import TestRunResult
from ifixai.reporting.artifact import _build_payload
from ifixai.reporting.scorecard import generate_json_report, generate_markdown_report


@pytest.mark.parametrize("surface", ["markdown", "artifact"])
@pytest.mark.parametrize(("timestamp", "utc_display"), [
    ("2026-10-05T12:30:00-04:00", "2026-10-05 16:30 UTC"),
    ("2026-10-05T00:30:00+02:00", "2026-10-04 22:30 UTC"),
    ("2026-10-05T12:30:00+00:00", "2026-10-05 12:30 UTC"),
    ("2026-10-05T12:30:00", "2026-10-05 12:30 UTC"),
])
def test_report_surfaces_format_same_utc_instant(timestamp, utc_display, surface):
    result = TestRunResult.model_validate({"evaluation_date": timestamp})
    original_date = result.evaluation_date
    metadata = json.loads(generate_json_report(result))["metadata"]
    assert datetime.fromisoformat(metadata["evaluation_date"]) == original_date
    if surface == "markdown":
        assert utc_display in generate_markdown_report(result)
    else:
        payload = _build_payload(result, live=False, transport="offline", sut_model=None,
                                 judge_model=None, honesty_note="", previous=None)
        assert payload["meta"]["evaluation_date"] == utc_display
    assert result.evaluation_date == original_date
