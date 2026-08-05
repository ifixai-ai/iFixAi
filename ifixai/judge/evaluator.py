from ifixai.core.types import ClassifierPair, ProviderConfig
from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.providers.resolver import resolve_provider


class JudgeEvaluator:

    def __init__(self, config: JudgeConfig) -> None:
        self._config = config
        self._provider = resolve_provider(config.provider)
        self._provider_config = ProviderConfig(
            provider=config.provider,
            api_key=config.api_key,
            model=config.model,
            endpoint=config.endpoint,
            timeout=config.timeout,
        )
        self._call_count = 0
        self._cap_reached = False
        self._fallback_grades: dict[str, int] = {}
        self._transport_failures: dict[str, int] = {}

    def provider_pair(self) -> ClassifierPair:
        return ClassifierPair(provider=self._provider, config=self._provider_config)

    @property
    def provider_name(self) -> str:
        """The judge provider's registry name (e.g. ``openrouter``).

        Public accessor so the fallback chain can be looked up without reaching
        into ``_provider_config``.
        """
        return self._provider_config.provider

    def note_fallback_grade(self, model: str) -> None:
        """Record that `model` graded a probe in place of the configured judge.

        A run where a third of the verdicts came from a different model is a
        materially different measurement, so the substitution is counted and
        surfaced in the run stats rather than left in the logs.
        """
        self._fallback_grades[model] = self._fallback_grades.get(model, 0) + 1

    def note_transport_failure(self, model: str) -> None:
        """Record one judge call that never returned a verdict.

        A grade that only succeeded on the third attempt looks identical to a
        clean one in the scorecard. Counting the failed attempts is what lets
        the report say how hard the judge had to work for the number.
        """
        self._transport_failures[model] = self._transport_failures.get(model, 0) + 1

    @property
    def temperature(self) -> float:
        """The judge provider's sampling temperature.

        Public accessor so callers (e.g. the judge-path inspections' determinism
        guard) can read the temperature without reaching into ``_provider_config``.
        """
        return self._provider_config.temperature

    @property
    def cap_reached(self) -> bool:
        return self._cap_reached

    def get_stats(self) -> dict[str, object]:
        return {
            "total_calls": self._call_count,
            "items_escalated": self._call_count,
            "cap_reached": self._cap_reached,
            "items_capped": 0,
            "judge_model": self._config.model or self._config.provider,
            "judge_provider": self._config.provider,
            "fallback_grades": dict(self._fallback_grades),
            "judge_transport_failures": dict(self._transport_failures),
        }

    async def aclose(self) -> None:
        await self._provider.aclose()


class EnsembleJudgeEvaluator:

    def __init__(self, config: JudgeConfig) -> None:
        if config.providers is None or len(config.providers) < 2:
            raise ValueError(
                f"EnsembleJudgeEvaluator requires >=2 providers, got "
                f"{len(config.providers) if config.providers else 0}"
            )
        self._config = config
        self._evaluators: list[JudgeEvaluator] = [
            JudgeEvaluator(_single_config_for(spec, config))
            for spec in config.providers
        ]

    @property
    def cap_reached(self) -> bool:
        return all(e.cap_reached for e in self._evaluators)

    @property
    def evaluators(self) -> list[JudgeEvaluator]:
        return list(self._evaluators)

    def get_stats(self) -> dict[str, object]:
        per_judge_stats = [e.get_stats() for e in self._evaluators]
        return {
            "total_calls": sum(s["total_calls"] for s in per_judge_stats),
            "items_escalated": sum(s["items_escalated"] for s in per_judge_stats),
            "cap_reached": self.cap_reached,
            "items_capped": sum(s["items_capped"] for s in per_judge_stats),
            "judge_model": "ensemble",
            "judge_provider": f"ensemble({len(self._evaluators)})",
            "fallback_grades": merge_counts(per_judge_stats, "fallback_grades"),
            "judge_transport_failures": merge_counts(
                per_judge_stats, "judge_transport_failures"
            ),
            "per_judge_stats": per_judge_stats,
        }

    async def aclose(self) -> None:
        for evaluator in self._evaluators:
            await evaluator.aclose()


def merge_counts(
    per_judge_stats: list[dict[str, object]], key: str
) -> dict[str, int]:
    """Sum each ensemble member's per-model counter under `key` into one tally."""
    merged: dict[str, int] = {}
    for stats in per_judge_stats:
        counts = stats.get(key) or {}
        if not isinstance(counts, dict):
            continue
        for model, count in counts.items():
            merged[model] = merged.get(model, 0) + int(count)
    return merged


def _single_config_for(
    spec: JudgeProviderSpec,
    parent: JudgeConfig,
) -> JudgeConfig:
    return JudgeConfig(
        provider=spec.provider,
        model=spec.model,
        api_key=spec.api_key,
        temperature=parent.temperature,
        max_calls_per_run=parent.max_calls_per_run,
        timeout=parent.timeout,
    )
