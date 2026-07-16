from typing import TypedDict


class EvalModeResolution(TypedDict):
    mode: str
    auto_selected_judge: str | None


class InteractiveConfig(TypedDict, total=False):
    provider: str
    api_key: str
    endpoint: str | None
    model: str | None
    auth_method: str | None
    extra_headers: dict[str, str] | None
