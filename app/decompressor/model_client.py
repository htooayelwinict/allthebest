"""OpenAI-compatible JSON model client for decompressor prompt-chain mode."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OpenAICompatibleJSONClient:
    """HTTP client for Chat Completions APIs that support JSON responses."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        temperature: float = 0.0,
        response_format: str = "json_schema",
        provider_sort: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._response_format = response_format
        self._provider_sort = provider_sort

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        payload = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You run one decompressor stage. Return only valid JSON matching "
                        "the requested schema and allowed-label instructions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": self._response_format_payload(stage, schema),
        }
        if self._provider_sort:
            payload["provider"] = {"sort": self._provider_sort}
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Model request for stage {stage} failed with HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Model request for stage {stage} failed before receiving a response.") from exc

        return self._extract_content(stage, body)

    def _response_format_payload(self, stage: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self._response_format == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"{stage}_output",
                "schema": schema,
                "strict": False,
            },
        }

    def _extract_content(self, stage: str, body: str) -> str:
        try:
            payload = json.loads(body)
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Model response for stage {stage} did not contain JSON content.") from exc

        if isinstance(content, str):
            return content
        raise RuntimeError(f"Model response for stage {stage} returned non-text content.")
