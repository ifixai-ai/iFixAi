import asyncio
import os
from pathlib import Path

from ifixai.api import run_inspections
from ifixai.core.fixture_loader import (
    list_test_coverage,
    load_fixture,
    validate_fixture,
)

CUSTOM_FIXTURE_PATH = Path("my_domain/fixture.yaml")

async def main() -> None:
    errors = validate_fixture(CUSTOM_FIXTURE_PATH)
    if errors:
        print("Fixture validation failed:")
        for error in errors:
            print(f"  - {error}")
        return

    fixture = load_fixture(CUSTOM_FIXTURE_PATH)
    coverage = list_test_coverage(fixture)
    print(f"Fixture: {fixture.metadata.name} (v{fixture.metadata.version})")
    print(f"Domain:  {fixture.metadata.domain}")
    print(f"Roles:   {len(fixture.roles)}")
    print(f"Tools:   {len(fixture.tools)}")
    print(f"Test cases covering {len(coverage)} tests:")
    for test_id, count in sorted(coverage.items()):
        print(f"  {test_id}: {count} test case(s)")

    result = await run_inspections(
        provider="openai",
        api_key=os.environ.get("OPENAI_API_KEY", "sk-..."),
        fixture=fixture,
        system_name="CustomDomainAssistant",
        system_version="0.1",
    )
    print(f"\nGrade: {result.grade.value} ({result.overall_score:.0%})")

    if result.gaps:
        print("\nGovernance gaps to address:")
        for gap in result.gaps:
            print(f"  [{gap.priority.upper()}] {gap.test_name}: {gap.gap_description}")

if __name__ == "__main__":
    asyncio.run(main())
