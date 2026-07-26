import asyncio
import os

from ifixai.api import run_inspections
from ifixai.reporting.scorecard import generate_json_report, generate_markdown_report


async def main() -> None:
    result = await run_inspections(
        provider="openai",
        api_key=os.environ.get("OPENAI_API_KEY", "sk-..."),
        fixture="enterprise",
        system_name="MyAssistant",
        system_version="1.0",
    )
    print(f"Grade: {result.grade.value} ({result.overall_score:.0%})")
    print(f"Strategic Score: {result.strategic_score:.0%}")
    print(f"Gaps: {len(result.gaps)}")

    with open("scorecard.json", "w") as f:
        f.write(generate_json_report(result))
    with open("scorecard.md", "w") as f:
        f.write(generate_markdown_report(result))

if __name__ == "__main__":
    asyncio.run(main())
