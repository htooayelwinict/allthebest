"""OpenRouter/OpenAI-compatible JSON client for AppV2 runtimes."""

from __future__ import annotations

from typing import Any

from openrouter import OpenRouter


class AppV2JSONClient:
    """Small SDK-backed client that requests structured JSON payloads."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        temperature: float = 0.0,
        response_format: str = "json_schema",
        provider_sort: str | None = "latency",
        max_tokens: int | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._response_format = response_format
        self._provider_sort = provider_sort
        self._max_tokens = max_tokens
        self._timeout_ms = int(timeout_seconds * 1000)
        self._client = OpenRouter(api_key=api_key, server_url=base_url.rstrip("/"), timeout_ms=self._timeout_ms)

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": "Return only JSON matching the supplied schema. No markdown."},
                {"role": "user", "content": prompt},
            ],
            "response_format": self._response_format_payload(stage, schema),
            "stream": False,
            "timeout_ms": self._timeout_ms,
        }
        if self._provider_sort:
            kwargs["provider"] = {"sort": self._provider_sort, "allow_fallbacks": True}
        if self._max_tokens is not None:
            kwargs["max_completion_tokens"] = self._max_tokens
        if self._response_format in {"json_schema", "json_object"}:
            kwargs["plugins"] = [{"id": "response-healing"}]

        try:
            response = self._client.chat.send(**kwargs)
        except Exception as exc:  # pragma: no cover - network/provider variability
            raise RuntimeError(f"Model request for stage {stage} failed before receiving a response.") from exc
        return self._extract_content(stage, response)

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

    def _extract_content(self, stage: str, response: Any) -> str:
        try:
            content = response.choices[0].message.content
        except Exception as exc:
            raise RuntimeError(f"Model response for stage {stage} did not contain JSON content.") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")
                for part in content
            ).strip()
            if text:
                return text
        raise RuntimeError(f"Model response for stage {stage} returned non-text content.")
