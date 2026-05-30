"""Environment loading for optional planner LLM wiring."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.decompressor.model_client import OpenAICompatibleJSONClient

TRUE_VALUES = {"1", "true", "yes", "on"}
CONFIG_KEYS = (
    "PLANNER_LLM_ENABLED",
    "PLANNER_LLM_API_KEY",
    "PLANNER_LLM_MODEL",
    "PLANNER_LLM_BASE_URL",
    "PLANNER_LLM_TIMEOUT_SECONDS",
    "PLANNER_LLM_TEMPERATURE",
    "PLANNER_LLM_RESPONSE_FORMAT",
    "PLANNER_LLM_PROVIDER_SORT",
    "PLANNER_LLM_MAX_TOKENS",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_PROVIDER_SORT",
)


def load_dotenv_values(path: str | Path = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def build_planner_model_client(
    dotenv_path: str | Path = ".env",
    *,
    client_factory: type[OpenAICompatibleJSONClient] = OpenAICompatibleJSONClient,
) -> Any | None:
    config = load_dotenv_values(dotenv_path)
    for key in CONFIG_KEYS:
        if key in os.environ:
            config[key] = os.environ[key]

    enabled = config.get("PLANNER_LLM_ENABLED", "").lower() in TRUE_VALUES
    if not enabled:
        return None

    api_key = config.get("PLANNER_LLM_API_KEY") or config.get("OPENROUTER_API_KEY")
    model = config.get("PLANNER_LLM_MODEL") or config.get("OPENROUTER_MODEL")
    if not api_key:
        raise ValueError("PLANNER_LLM_ENABLED=true requires PLANNER_LLM_API_KEY or OPENROUTER_API_KEY.")
    if not model:
        raise ValueError("PLANNER_LLM_ENABLED=true requires PLANNER_LLM_MODEL or OPENROUTER_MODEL.")

    base_url = config.get("PLANNER_LLM_BASE_URL") or config.get("OPENROUTER_BASE_URL") or "https://api.openai.com/v1"
    return client_factory(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=float(config.get("PLANNER_LLM_TIMEOUT_SECONDS", "30")),
        temperature=float(config.get("PLANNER_LLM_TEMPERATURE", "0")),
        response_format=config.get("PLANNER_LLM_RESPONSE_FORMAT", "json_schema"),
        provider_sort=config.get("PLANNER_LLM_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT") or "latency",
        max_tokens=_optional_int(config.get("PLANNER_LLM_MAX_TOKENS"), default=None),
    )


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].strip()
    return value


def _optional_int(value: str | None, *, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    if value.lower() in {"none", "null"}:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("PLANNER_LLM_MAX_TOKENS must be a positive integer or null.")
    return parsed
