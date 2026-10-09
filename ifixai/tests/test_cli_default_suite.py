"""`ifixai run` runs the 10-inspection essential suite by default; `--full-run` runs them all."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from ifixai.cli.main import ifixai_cli
from ifixai.harness.registry import ALL_SPECS, SPEC_BY_ID, resolve_category_test_ids
from ifixai.harness.suites import (
    DEFAULT_SUITE,
    ESSENTIAL_HIGH_IMPACT_IDS,
    ESSENTIAL_MEDIUM_IMPACT_IDS,
    ESSENTIAL_TEST_IDS,
    TIER_NAMES,
    resolve_suite,
    suite_catalog,
)
from ifixai.scoring.category_weights import GRADED_CATEGORIES, STRATEGIC_TEST_IDS
from ifixai.scoring.mandatory_minimums import MANDATORY_MINIMUMS

MOCK_RUN = [
    "run", "--provider", "mock", "--api-key", "not-used", "--eval-mode", "self",
    "--no-telemetry", "--no-promo", "--no-parallel", "--min-score", "0",
]
EXPLICIT_SELECTORS = [
    ["--suite", "core"],
    ["--test", "B01"],
    ["--category", "DECEPTION"],
    ["--strategic"],
]
EXPLICIT_SELECTOR_COUNTS = [
    len(resolve_suite("core")["test_ids"]),
    1,
    len(resolve_category_test_ids(["DECEPTION"])["test_ids"]),
    len(STRATEGIC_TEST_IDS),
]


@pytest.fixture
def isolated_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


def invoke_mock_run(extra_args: list[str]) -> Result:
    return CliRunner().invoke(ifixai_cli, [*MOCK_RUN, *extra_args])


def read_single_report(workdir: Path) -> dict:
    reports = list((workdir / "ifixai-results").glob("*.json"))
    assert len(reports) == 1
    return json.loads(reports[0].read_text(encoding="utf-8"))


def read_single_manifest(workdir: Path) -> dict:
    manifests = list((workdir / "runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def test_essential_suite_is_six_high_and_four_medium_impact() -> None:
    assert len(ESSENTIAL_HIGH_IMPACT_IDS) == 6
    assert len(ESSENTIAL_MEDIUM_IMPACT_IDS) == 4
    assert not set(ESSENTIAL_HIGH_IMPACT_IDS) & set(ESSENTIAL_MEDIUM_IMPACT_IDS)
    assert ESSENTIAL_TEST_IDS == [*ESSENTIAL_HIGH_IMPACT_IDS, *ESSENTIAL_MEDIUM_IMPACT_IDS]
    assert set(ESSENTIAL_TEST_IDS) <= set(SPEC_BY_ID)


def test_essential_suite_resolves_in_registry_order() -> None:
    resolution = resolve_suite("essential")
    registry_order = [spec.test_id for spec in ALL_SPECS if spec.test_id in ESSENTIAL_TEST_IDS]
    assert resolution["unknown"] == []
    assert resolution["test_ids"] == registry_order
    assert len(resolution["test_ids"]) == 10


def test_essential_suite_carries_every_mandatory_minimum() -> None:
    """A run missing a mandatory minimum has its overall score withheld."""
    assert set(MANDATORY_MINIMUMS) <= set(ESSENTIAL_TEST_IDS)


def test_essential_suite_holds_no_ungraded_probe() -> None:
    for test_id in ESSENTIAL_TEST_IDS:
        spec = SPEC_BY_ID[test_id]
        assert not (spec.is_exploratory or spec.is_advisory or spec.is_attestation), test_id


def test_essential_suite_covers_every_graded_pillar() -> None:
    covered = {SPEC_BY_ID[test_id].category for test_id in ESSENTIAL_TEST_IDS}
    assert GRADED_CATEGORIES <= covered


def test_default_suite_is_a_listed_tier() -> None:
    assert DEFAULT_SUITE == "essential"
    assert DEFAULT_SUITE in TIER_NAMES
    counts = {row["name"]: row["count"] for row in suite_catalog()}
    assert counts[DEFAULT_SUITE] == 10


def test_dry_run_defaults_to_the_essential_suite(isolated_workdir: Path) -> None:
    result = invoke_mock_run(["--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Estimated tests:  10" in result.output
    assert "--full-run" in result.output


def test_dry_run_full_run_covers_every_inspection(isolated_workdir: Path) -> None:
    result = invoke_mock_run(["--dry-run", "--full-run"])
    assert result.exit_code == 0, result.output
    assert f"Estimated tests:  {len(SPEC_BY_ID)}" in result.output
    assert "Default run" not in result.output


@pytest.mark.parametrize(
    "selector,expected_count", zip(EXPLICIT_SELECTORS, EXPLICIT_SELECTOR_COUNTS)
)
def test_explicit_selector_is_not_replaced_by_the_default(
    isolated_workdir: Path, selector: list[str], expected_count: int
) -> None:
    result = invoke_mock_run(["--dry-run", *selector])
    assert result.exit_code == 0, result.output
    assert f"  Estimated tests:  {expected_count}" in result.output.splitlines()
    assert "Default run" not in result.output


@pytest.mark.parametrize("selector", EXPLICIT_SELECTORS)
def test_full_run_rejects_an_explicit_selector(
    isolated_workdir: Path, selector: list[str]
) -> None:
    result = invoke_mock_run(["--full-run", *selector])
    assert result.exit_code == 1, result.output
    assert "cannot be combined" in result.output
    assert not (isolated_workdir / "runs").exists()
    assert not (isolated_workdir / "ifixai-results").exists()


def test_config_suite_beats_the_default(isolated_workdir: Path) -> None:
    (isolated_workdir / "ifixai.yaml").write_text("suite: core\n", encoding="utf-8")
    result = invoke_mock_run(["--dry-run"])
    assert result.exit_code == 0, result.output
    assert f"Estimated tests:  {len(resolve_suite('core')['test_ids'])}" in result.output


def test_full_run_flag_beats_the_config_suite(isolated_workdir: Path) -> None:
    (isolated_workdir / "ifixai.yaml").write_text("suite: core\n", encoding="utf-8")
    result = invoke_mock_run(["--dry-run", "--full-run"])
    assert result.exit_code == 0, result.output
    assert f"Estimated tests:  {len(SPEC_BY_ID)}" in result.output


def test_full_run_rejects_a_selector_even_with_a_config_suite(isolated_workdir: Path) -> None:
    (isolated_workdir / "ifixai.yaml").write_text("suite: core\n", encoding="utf-8")
    result = invoke_mock_run(["--full-run", "--test", "B01"])
    assert result.exit_code == 1, result.output
    assert "cannot be combined" in result.output
    assert not (isolated_workdir / "runs").exists()


def test_default_run_scores_the_ten_essential_inspections(isolated_workdir: Path) -> None:
    result = invoke_mock_run([])
    assert result.exit_code == 0, result.output
    assert f"Default run: 10 of {len(SPEC_BY_ID)} inspections" in result.output
    report = read_single_report(isolated_workdir)
    assert sorted(row["test_id"] for row in report["test_results"]) == sorted(ESSENTIAL_TEST_IDS)
    assert report["overall"]["score"] is not None
    assert report["mandatory_minimums"]["not_run"] == []
    assert read_single_manifest(isolated_workdir)["mode_filter"] == resolve_suite("essential")["test_ids"]


def test_default_run_resumes_without_a_selector(isolated_workdir: Path) -> None:
    assert invoke_mock_run([]).exit_code == 0
    run_id = read_single_manifest(isolated_workdir)["run_id"]
    resumed = invoke_mock_run(["--resume", run_id])
    assert resumed.exit_code == 0, resumed.output
    assert "10 of 10 inspections restored" in resumed.output


def test_default_run_cannot_be_resumed_as_a_full_run(isolated_workdir: Path) -> None:
    assert invoke_mock_run([]).exit_code == 0
    run_id = read_single_manifest(isolated_workdir)["run_id"]
    resumed = invoke_mock_run(["--resume", run_id, "--full-run"])
    assert resumed.exit_code == 1, resumed.output
    assert "run configuration changed" in resumed.output


def test_full_run_resumes_without_repeating_the_flag(isolated_workdir: Path) -> None:
    """A run started before the default changed, or with --full-run, resumes bare."""
    assert invoke_mock_run(["--full-run"]).exit_code == 0
    run_id = read_single_manifest(isolated_workdir)["run_id"]
    resumed = invoke_mock_run(["--resume", run_id])
    assert resumed.exit_code == 0, resumed.output
    assert f"{len(SPEC_BY_ID)} of {len(SPEC_BY_ID)} inspections restored" in resumed.output
    assert "Default run" not in resumed.output


def test_full_run_executes_every_inspection(isolated_workdir: Path) -> None:
    result = invoke_mock_run(["--full-run"])
    assert result.exit_code == 0, result.output
    report = read_single_report(isolated_workdir)
    assert sorted(row["test_id"] for row in report["test_results"]) == sorted(SPEC_BY_ID)
    assert report["metadata"]["run_mode"] == "full"
    assert read_single_manifest(isolated_workdir)["mode_filter"] == ["all"]
