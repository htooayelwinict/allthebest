"""Fresh non-streaming JSON client for the legacy decide() shim."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx

from appv22.ai.env_config import ModelConfig


class JsonModelClient:
    """Minimal OpenAI/OpenRouter-compatible JSON-schema completion client."""

    def __init__(self, config: ModelConfig) -> None:
        self._config = config
        self._usage: dict[str, Any] = {
            "model_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def usage_snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        snapshot = deepcopy(self._usage)
        if reset:
            self._usage = {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return snapshot

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        body: dict[str, Any] = {
            "model": self._config.model,
            "temperature": self._config.temperature,
            "messages": [
                {"role": "system", "content": "Return only JSON matching the supplied schema. No markdown."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": f"{stage}_output", "schema": schema, "strict": False},
            },
            "stream": False,
        }
        if self._config.provider_sort:
            body["provider"] = {"sort": self._config.provider_sort, "allow_fallbacks": True}
        if self._config.max_tokens is not None:
            body["max_tokens"] = self._config.max_tokens
        headers = {"Authorization": f"Bearer {self._config.api_key}", "Content-Type": "application/json"}
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage") or {}
        self._usage["model_calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._usage[key] += int(usage.get(key) or 0)
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content if isinstance(content, str) else json.dumps(content)
