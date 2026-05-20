from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.providers.base import ChatProvider
from ifixai.providers.resolver import resolve_provider
from ifixai.core.types import ProviderConfig


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

    def provider_pair(self) -> tuple[ChatProvider, ProviderConfig]:
        return (self._provider, self._provider_config)

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
            "per_judge_stats": per_judge_stats,
        }

    async def aclose(self) -> None:
        for evaluator in self._evaluators:
            await evaluator.aclose()


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
