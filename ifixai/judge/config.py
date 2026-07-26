

from typing import Optional

from pydantic import BaseModel, model_validator


class JudgeProviderSpec(BaseModel):

    provider: str
    model: Optional[str] = None
    api_key: str = ""

class JudgeConfig(BaseModel):

    provider: str = ""
    model: Optional[str] = None
    api_key: str = ""
    endpoint: Optional[str] = None

    providers: Optional[list[JudgeProviderSpec]] = None

    temperature: float = 0.0
    max_calls_per_run: int = 200
    timeout: int = 30

    @model_validator(mode="after")
    def _validate_exclusive(self) -> "JudgeConfig":
        single_set = bool(self.provider)
        ensemble_set = self.providers is not None
        if single_set and ensemble_set:
            raise ValueError(
                "JudgeConfig: set EITHER 'provider' (single-judge) OR "
                "'providers' (ensemble), not both"
            )
        if not single_set and not ensemble_set:
            raise ValueError(
                "JudgeConfig: must set either 'provider' or 'providers'"
            )
        if ensemble_set and len(self.providers) < 2:
            raise ValueError(
                f"JudgeConfig: ensemble form requires >=2 providers, got "
                f"{len(self.providers)}"
            )
        return self
