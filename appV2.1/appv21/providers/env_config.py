"""Environment loading for AppV2.1 providers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appv21.providers.model_client import AppV21JSONClient

TRUE_VALUES = {"1", "true", "yes", "on"}
COMMON_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_PROVIDER_SORT",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)
SUFFIXES = (
    "ENABLED",
    "API_KEY",
    "MODEL",
    "BASE_URL",
    "TIMEOUT_SECONDS",
    "TEMPERATURE",
    "TOP_P",
    "FREQUENCY_PENALTY",
    "PRESENCE_PENALTY",
    "SEED",
    "STOP",
    "RESPONSE_FORMAT",
    "PROVIDER_SORT",
    "MAX_TOKENS",
)


@dataclass(frozen=True)
class AppV21RuntimeConfig:
    enabled: bool
    api_key: str | None
    model: str | None
    base_url: str
    timeout_seconds: float
    temperature: float
    top_p: float | None
    frequency_penalty: float | None
    presence_penalty: float | None
    seed: int | None
    stop: list[str]
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


def load_appv21_runtime_config(prefix: str, dotenv_path: str | Path = ".env") -> AppV21RuntimeConfig:
    config = load_dotenv_values(dotenv_path)
    for key in (*COMMON_KEYS, *(f"{prefix}_{suffix}" for suffix in SUFFIXES)):
        if key in os.environ:
            config[key] = os.environ[key]
    enabled = config.get(f"{prefix}_ENABLED", "").lower() in TRUE_VALUES
    api_key = config.get(f"{prefix}_API_KEY") or config.get("OPENROUTER_API_KEY") or config.get("OPENAI_API_KEY")
    model = config.get(f"{prefix}_MODEL") or config.get("OPENROUTER_MODEL") or config.get("OPENAI_MODEL") or _default_model(prefix)
    return AppV21RuntimeConfig(
        enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=config.get(f"{prefix}_BASE_URL") or config.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        timeout_seconds=float(config.get(f"{prefix}_TIMEOUT_SECONDS", "60")),
        temperature=float(config.get(f"{prefix}_TEMPERATURE", "0")),
        top_p=_optional_float(config.get(f"{prefix}_TOP_P")),
        frequency_penalty=_optional_float(config.get(f"{prefix}_FREQUENCY_PENALTY")),
        presence_penalty=_optional_float(config.get(f"{prefix}_PRESENCE_PENALTY")),
        seed=_optional_int(config.get(f"{prefix}_SEED")),
        stop=_optional_list(config.get(f"{prefix}_STOP")),
        response_format=config.get(f"{prefix}_RESPONSE_FORMAT", "json_schema"),
        provider_sort=config.get(f"{prefix}_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT") or "latency",
        max_tokens=_optional_int(config.get(f"{prefix}_MAX_TOKENS")),
    )


def build_appv21_model_client(
    prefix: str,
    dotenv_path: str | Path = ".env",
    *,
    client_factory: type[Any] = AppV21JSONClient,
) -> Any | None:
    config = load_appv21_runtime_config(prefix, dotenv_path)
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
        top_p=config.top_p,
        frequency_penalty=config.frequency_penalty,
        presence_penalty=config.presence_penalty,
        seed=config.seed,
        stop=config.stop,
        response_format=config.response_format,
        provider_sort=config.provider_sort,
        max_tokens=config.max_tokens,
    )


def _default_model(prefix: str) -> str | None:
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
        raise ValueError("AppV2.1 max token settings must be positive or blank.")
    return parsed


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return None
    return float(value)


def _optional_list(value: str | None) -> list[str]:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return []
    stripped = value.strip()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("Stop setting must be a JSON array or comma-separated list.")
        return [str(item) for item in parsed]
    return [item.strip() for item in stripped.split(",") if item.strip()]
