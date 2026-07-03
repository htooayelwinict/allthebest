"""Register built-in api providers. Port of providers/register-builtins.ts."""

from __future__ import annotations

from appv24.ai.env_config import ModelConfig
from appv24.ai.providers.appv2_env import create_appv2_env_provider
from appv24.ai.stream import register_api_provider


def register_builtin_providers(
    prefix: str = "APPV24_WORKER_LLM",
    dotenv_path: str = ".env",
    *,
    config: ModelConfig | None = None,
) -> None:
    register_api_provider(create_appv2_env_provider(prefix, dotenv_path, config=config))
