"""Regressions file, SARIF and the PR comment."""

from __future__ import annotations

from datetime import date

import pytest

from ifixai.harness.registry import SPEC_BY_ID
from ifixai.selfcheck import comment, regressions
from ifixai.selfcheck.result import build_result
from ifixai.selfcheck.sarif import choose_anchor, fingerprint, to_sarif
from ifixai.tests.selfcheck.scorecards import scorecard


def test_every_registered_inspection_id_is_pinnable() -> None:
    bad = [i for i in SPEC_BY_ID if not regressions.INSPECTION_ID.match(i)]
    assert not bad, bad


def test_add_and_reload_regression(tmp_path) -> None:
    path = tmp_path / ".ifixai" / "regressions.yaml"
    entry = regressions.entry_from_scorecard(scorecard(), "B08", today=date(2026, 11, 11))
    assert entry == {
        "inspection": "B08",
        "case": "B08_step2_user",
        "title": "Privilege Escalation Detection",
        "added": "2026-11-11",
        "note": "refusal clarity: granted access instead of refusing",
    }
    assert regressions.add(path, entry) is True
    assert regressions.add(path, entry) is False  # idempotent
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Findings this repository has promised not to repeat.")
    assert regressions.load(path) == [entry]
    assert regressions.local_inspections(regressions.load(path)) == ["B08"]


def test_only_failures_can_be_pinned() -> None:
    with pytest.raises(ValueError, match="did not fail"):
        regressions.entry_from_scorecard(scorecard(), "B12")
    with pytest.raises(KeyError):
        regressions.entry_from_scorecard(scorecard(), "Z99")


def test_hosted_entries_are_read_but_not_run(tmp_path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(
        "version: 1\nregressions:\n"
        "  - hosted: {run_id: run_1, finding: f-2}\n    title: Refund above limit\n"
        "  - inspection: B08\n  - inspection: B08\n",
        encoding="utf-8",
    )
    entries = regressions.load(path)
    assert regressions.local_inspections(entries) == ["B08"]
    status = regressions.check(entries, scorecard())
    assert status == {"file_entries": 3, "checked": ["B08"], "failed": ["B08"], "undecided": [], "hosted": 1}
    fixed = regressions.check(entries, scorecard({"B08": "pass"}))
    assert fixed["failed"] == [] and fixed["checked"] == ["B08"]


@pytest.mark.parametrize(
    "body",
    [
        "- just a list\n",
        "version: 2\nregressions: []\n",
        "regressions: {a: 1}\n",
        "regressions:\n  - title: neither\n",
        "regressions:\n  - inspection: B08\n    hosted: {run_id: r, finding: f}\n",
        "regressions:\n  - inspection: 'rm -rf'\n",
        "regressions:\n  - hosted: {run_id: r}\n",
        "regressions: [\n",
    ],
)
def test_bad_regression_files_are_refused(tmp_path, body) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(regressions.RegressionFileError):
        regressions.load(path)


def test_absent_file_is_empty(tmp_path) -> None:
    assert regressions.load(tmp_path / "nope.yaml") == []


def test_sarif_shape_and_labels() -> None:
    card = scorecard()
    document = to_sarif(card, anchor="ifixai.yaml", target="https://agent.example/v1", verdict="v")
    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    assert [r["id"] for r in rules] == ["B08", "B12", "B20", "B31"]
    assert all("security-severity" in r["properties"] for r in rules)
    assert run["properties"]["certified"] is False and run["properties"]["badge"] is None
    [result] = run["results"]
    assert result["ruleId"] == "B08" and rules[result["ruleIndex"]]["id"] == "B08"
    assert result["message"]["text"].startswith("Self-check, not certified: Privilege Escalation Detection failed (25%, needs 100%).")
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "ifixai.yaml"
    assert location["region"]["startLine"] == 1
    assert result["partialFingerprints"]["ifixaiFinding/v1"] == fingerprint("B08", "B08_step2_user", "https://agent.example/v1")


def test_sarif_fingerprint_is_stable_and_distinct() -> None:
    assert fingerprint("B08", "c", "t") == fingerprint("B08", "c", "t")
    assert fingerprint("B08", "c", "t") != fingerprint("B08", "c", "u")


def test_sarif_clean_run_has_no_results() -> None:
    assert to_sarif(scorecard({"B08": "pass"}))["runs"][0]["results"] == []


def test_choose_anchor(tmp_path) -> None:
    assert choose_anchor(tmp_path) == "README.md"
    (tmp_path / ".ifixai").mkdir()
    (tmp_path / ".ifixai" / "regressions.yaml").write_text("", encoding="utf-8")
    assert choose_anchor(tmp_path) == ".ifixai/regressions.yaml"
    (tmp_path / "ifixai.yaml").write_text("", encoding="utf-8")
    assert choose_anchor(tmp_path) == "ifixai.yaml"
    fixture = tmp_path / "fixtures" / "agent.yaml"
    fixture.parent.mkdir()
    fixture.write_text("", encoding="utf-8")
    assert choose_anchor(tmp_path, str(fixture)) == "fixtures/agent.yaml"
    assert choose_anchor(tmp_path, "/elsewhere/agent.yaml") == "ifixai.yaml"


def test_comment_carries_marker_verdict_and_notice() -> None:
    result = build_result(scorecard(), suite="smoke", regressions={"checked": ["B08"], "failed": ["B08"]})
    text = comment.render(result)
    assert text.startswith(comment.MARKER + "\n")
    assert f"**{result['verdict']}**" in text
    assert "| Privilege Escalation Detection (`B08`) | manipulation |" in text
    assert "Pinned regressions that failed again: `B08`." in text
    assert text.rstrip().endswith(result["notice"])


def test_comment_escapes_pipes_and_caps_rows() -> None:
    card = scorecard()
    card["test_results"][0]["evidence"][1]["actual"] = "a | b"
    card["test_results"] = card["test_results"] * 12
    text = comment.render(build_result(card))
    assert "a \\| b" in text
    assert "… and 2 more" in text


def test_comment_clean_regressions_line() -> None:
    result = build_result(scorecard({"B08": "pass"}), regressions={"checked": ["B08"], "failed": []})
    assert "Pinned regressions checked: 1, none failed again." in comment.render(result)
