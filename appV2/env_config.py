"""Environment loading for AppV2 runtimes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appV2.model_client import AppV2JSONClient


TRUE_VALUES = {"1", "true", "yes", "on"}
COMMON_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_PROVIDER_SORT",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


@dataclass(frozen=True)
class AppV2RuntimeConfig:
    enabled: bool
    api_key: str | None
    model: str | None
    base_url: str
    timeout_seconds: float
    temperature: float
    response_format: str
    provider_sort: str | None
    max_tokens: int | None


def load_dotenv_values(path: str | Path = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = _strip_inline_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_appv2_runtime_config(prefix: str, dotenv_path: str | Path = ".env") -> AppV2RuntimeConfig:
    config = load_dotenv_values(dotenv_path)
    for key in (*COMMON_KEYS, *(f"{prefix}_{suffix}" for suffix in _SUFFIXES)):
        if key in os.environ:
            config[key] = os.environ[key]

    enabled = config.get(f"{prefix}_ENABLED", "").lower() in TRUE_VALUES
    api_key = config.get(f"{prefix}_API_KEY") or config.get("OPENROUTER_API_KEY") or config.get("OPENAI_API_KEY")
    model = config.get(f"{prefix}_MODEL") or config.get("OPENROUTER_MODEL") or config.get("OPENAI_MODEL") or _default_model(prefix)
    return AppV2RuntimeConfig(
        enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=config.get(f"{prefix}_BASE_URL") or config.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        timeout_seconds=float(config.get(f"{prefix}_TIMEOUT_SECONDS", "60")),
        temperature=float(config.get(f"{prefix}_TEMPERATURE", "0")),
        response_format=config.get(f"{prefix}_RESPONSE_FORMAT", "json_schema"),
        provider_sort=config.get(f"{prefix}_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT") or "latency",
        max_tokens=_optional_int(config.get(f"{prefix}_MAX_TOKENS")),
    )


def build_appv2_model_client(
    prefix: str,
    dotenv_path: str | Path = ".env",
    *,
    client_factory: type[AppV2JSONClient] = AppV2JSONClient,
) -> Any | None:
    config = load_appv2_runtime_config(prefix, dotenv_path)
    if not config.enabled:
        return None
    if not config.api_key:
        raise ValueError(f"{prefix}_ENABLED=true requires {prefix}_API_KEY, OPENROUTER_API_KEY, or OPENAI_API_KEY.")
    if not config.model:
        raise ValueError(f"{prefix}_ENABLED=true requires {prefix}_MODEL, OPENROUTER_MODEL, or OPENAI_MODEL.")
    return client_factory(
        api_key=config.api_key,
        model=config.model,
        base_url=config.base_url,
        timeout_seconds=config.timeout_seconds,
        temperature=config.temperature,
        response_format=config.response_format,
        provider_sort=config.provider_sort,
        max_tokens=config.max_tokens,
    )


_SUFFIXES = (
    "ENABLED",
    "API_KEY",
    "MODEL",
    "BASE_URL",
    "TIMEOUT_SECONDS",
    "TEMPERATURE",
    "RESPONSE_FORMAT",
    "PROVIDER_SORT",
    "MAX_TOKENS",
)


def _default_model(prefix: str) -> str | None:
    if prefix in {"APPV2_DECOMPOSER_LLM", "APPV2_PLANNER_LLM"}:
        return "openai/gpt-5.3-codex"
    if prefix == "APPV2_WORKER_LLM":
        return "xiaomi/mimo-v2.5-pro"
    return None


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].strip()
    return value


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("AppV2 max token settings must be positive or blank.")
    return parsed
