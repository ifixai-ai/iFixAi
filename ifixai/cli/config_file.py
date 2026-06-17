"""Reusable run configuration persisted as ``ifixai.yaml``."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

CONFIG_FILENAME = "ifixai.yaml"


class JudgeSpec(BaseModel):
    provider: str
    model: str | None = None


class RunConfig(BaseModel):
    """Persisted defaults for ``ifixai run`` (every field optional)."""

    provider: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    endpoint: str | None = None
    fixture: str | None = None
    suite: str | None = None
    mode: str | None = None
    eval_mode: str | None = None
    judges: list[JudgeSpec] = []
    output: str | None = None
    format: str | None = None
    timeout: int | None = None
    name: str | None = None

    def to_yaml(self) -> str:
        data = self.model_dump(exclude_none=True)
        if not self.judges:
            data.pop("judges", None)
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def config_path(start_dir: Path | None = None) -> Path:
    return (start_dir or Path.cwd()) / CONFIG_FILENAME


def load_config(start_dir: Path | None = None) -> RunConfig | None:
    """Load ``ifixai.yaml`` from ``start_dir`` (cwd by default), or None."""
    path = config_path(start_dir)
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{CONFIG_FILENAME} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{CONFIG_FILENAME} must contain a mapping at the top level.")
    try:
        return RunConfig(**raw)
    except ValidationError as exc:
        raise ValueError(f"{CONFIG_FILENAME} has invalid fields:\n{exc}") from exc


def write_config(config: RunConfig, start_dir: Path | None = None) -> Path:
    """Write ``config`` to ``ifixai.yaml`` and return the path."""
    path = config_path(start_dir)
    path.write_text(config.to_yaml(), encoding="utf-8")
    return path
