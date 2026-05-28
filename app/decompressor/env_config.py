"""Environment loading for optional decompressor LLM wiring."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.decompressor.model_client import OpenAICompatibleJSONClient


TRUE_VALUES = {"1", "true", "yes", "on"}
CONFIG_KEYS = (
    "DECOMPRESSOR_LLM_ENABLED",
    "DECOMPRESSOR_LLM_API_KEY",
    "DECOMPRESSOR_LLM_MODEL",
    "DECOMPRESSOR_LLM_BASE_URL",
    "DECOMPRESSOR_LLM_TIMEOUT_SECONDS",
    "DECOMPRESSOR_LLM_TEMPERATURE",
    "DECOMPRESSOR_LLM_RESPONSE_FORMAT",
    "DECOMPRESSOR_LLM_PROVIDER_SORT",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


def load_dotenv_values(path: str | Path = ".env") -> dict[str, str]:
    """Parse a small `.env` file without adding a runtime dependency."""

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


def build_decompressor_model_client(
    dotenv_path: str | Path = ".env",
    *,
    client_factory: type[OpenAICompatibleJSONClient] = OpenAICompatibleJSONClient,
) -> Any | None:
    """Build a model client from `.env`/process env when explicitly enabled."""

    config = load_dotenv_values(dotenv_path)
    for key in CONFIG_KEYS:
        if key in os.environ:
            config[key] = os.environ[key]

    enabled = config.get("DECOMPRESSOR_LLM_ENABLED", "").lower() in TRUE_VALUES
    if not enabled:
        return None

    api_key = config.get("DECOMPRESSOR_LLM_API_KEY") or config.get("OPENAI_API_KEY")
    model = config.get("DECOMPRESSOR_LLM_MODEL") or config.get("OPENAI_MODEL")
    if not api_key:
        raise ValueError("DECOMPRESSOR_LLM_ENABLED=true requires DECOMPRESSOR_LLM_API_KEY or OPENAI_API_KEY.")
    if not model:
        raise ValueError("DECOMPRESSOR_LLM_ENABLED=true requires DECOMPRESSOR_LLM_MODEL or OPENAI_MODEL.")

    return client_factory(
        api_key=api_key,
        model=model,
        base_url=config.get("DECOMPRESSOR_LLM_BASE_URL", "https://api.openai.com/v1"),
        timeout_seconds=float(config.get("DECOMPRESSOR_LLM_TIMEOUT_SECONDS", "30")),
        temperature=float(config.get("DECOMPRESSOR_LLM_TEMPERATURE", "0")),
        response_format=config.get("DECOMPRESSOR_LLM_RESPONSE_FORMAT", "json_schema"),
        provider_sort=config.get("DECOMPRESSOR_LLM_PROVIDER_SORT") or None,
    )


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].strip()
    return value
