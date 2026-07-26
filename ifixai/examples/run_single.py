import asyncio
import os

from ifixai.api import run_single


async def main() -> None:
    result = await run_single(
        test_id="B08",
        provider="openai",
        api_key=os.environ.get("OPENAI_API_KEY", "sk-..."),
        fixture="healthcare",
    )
    print(f"{result.test_id}: {result.score:.0%} {'PASS' if result.passed else 'FAIL'}")
    for ev in result.evidence:
        print(f"  {ev.test_case_id}: {ev.evaluation_result}")


if __name__ == "__main__":
    asyncio.run(main())
