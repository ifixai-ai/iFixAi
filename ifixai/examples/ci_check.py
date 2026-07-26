import asyncio
import os
import sys

from ifixai.api import run_inspections
from ifixai.reporting.scorecard import generate_json_report


def read_env_config() -> dict[str, str]:
    return {
        "provider": os.environ.get("IFIXAI_PROVIDER", "openai"),
        "api_key": os.environ.get("IFIXAI_API_KEY", ""),
        "fixture": os.environ.get("IFIXAI_FIXTURE", "enterprise"),
        "model": os.environ.get("IFIXAI_MODEL", ""),
        "system_name": os.environ.get("IFIXAI_SYSTEM_NAME", "ci-target"),
        "system_version": os.environ.get("IFIXAI_SYSTEM_VERSION", "1.0"),
        "min_score": os.environ.get("IFIXAI_MIN_SCORE", "0.70"),
        "min_strategic": os.environ.get("IFIXAI_MIN_STRATEGIC", "0.80"),
        "report_path": os.environ.get("IFIXAI_REPORT_PATH", ""),
    }

async def run_ci_check() -> int:
    config = read_env_config()

    if not config["api_key"]:
        print("ERROR: IFIXAI_API_KEY environment variable is required")
        return 1

    min_score = float(config["min_score"])
    min_strategic = float(config["min_strategic"])

    print(f"ifixai CI Check — provider={config['provider']}, fixture={config['fixture']}")
    print(f"Thresholds: overall>={min_score:.0%}, strategic>={min_strategic:.0%}")
    print()

    result = await run_inspections(
        provider=config["provider"],
        api_key=config["api_key"],
        fixture=config["fixture"],
        model=config["model"] or None,
        system_name=config["system_name"],
        system_version=config["system_version"],
    )

    if config["report_path"]:
        with open(config["report_path"], "w") as f:
            f.write(generate_json_report(result))
        print(f"Report written to {config['report_path']}")

    print(f"Grade:          {result.grade.value}")
    print(f"Overall Score:  {result.overall_score:.0%}")
    print(f"Strategic Score: {result.strategic_score:.0%}")

    passed_count = sum(1 for br in result.test_results if br.passed)
    total_count = len(result.test_results)
    print(f"Tests:     {passed_count}/{total_count} passed")

    if result.gaps:
        print(f"Gaps:           {len(result.gaps)}")

    is_passing = True

    if result.overall_score < min_score:
        print(f"\nFAIL: Overall score {result.overall_score:.0%} < {min_score:.0%}")
        is_passing = False

    if result.strategic_score < min_strategic:
        print(f"\nFAIL: Strategic score {result.strategic_score:.0%} < {min_strategic:.0%}")
        is_passing = False

    if not result.mandatory_minimums_passed:
        print(f"\nFAIL: Mandatory minimums not met: {result.mandatory_minimum_violations}")
        is_passing = False

    if is_passing:
        print("\nPASS: All ifixai thresholds met.")
        return 0

    return 1

if __name__ == "__main__":
    exit_code = asyncio.run(run_ci_check())
    sys.exit(exit_code)
