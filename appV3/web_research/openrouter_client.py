from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .schemas import AgentDecision


class OpenRouterJSONClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = 90.0,
        temperature: float = 0.0,
        provider_sort: str | None = "latency",
        max_tokens: int = 4096,
    ) -> None:
        try:
            from openrouter import OpenRouter
        except ImportError as exc:  # pragma: no cover - uv script dependency path
            raise SystemExit("openrouter is not installed. Run with uv run --script.") from exc

        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.provider_sort = provider_sort
        self.max_tokens = max_tokens
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout_ms": int(timeout_seconds * 1000)}
        if base_url:
            kwargs["server_url"] = base_url
        self._client = OpenRouter(**kwargs)

    def decide(self, messages: list[dict[str, str]]) -> AgentDecision:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "appv3_web_research_decision",
                    "schema": AgentDecision.model_json_schema(),
                    "strict": False,
                },
            },
            "plugins": [{"id": "response-healing"}],
            "stream": False,
            "max_tokens": self.max_tokens,
            "timeout_ms": int(self.timeout_seconds * 1000),
        }
        if self.provider_sort:
            kwargs["provider"] = {"sort": self.provider_sort, "allow_fallbacks": True}
        try:
            response = self._client.chat.send(**kwargs)
        except Exception as exc:  # pragma: no cover - network/API variability
            detail = sdk_error_detail(exc)
            message = "OpenRouter web_research decision failed."
            if detail:
                message = f"{message} {detail}"
            raise RuntimeError(message) from exc
        return parse_decision(extract_response_content(response))


def sdk_error_detail(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, str) and body.strip():
        return f"OpenRouter error body: {body.strip()[:500]}"
    raw_response = getattr(exc, "raw_response", None)
    text = getattr(raw_response, "text", None)
    if isinstance(text, str) and text.strip():
        return f"OpenRouter error body: {text.strip()[:500]}"
    return ""


def extract_response_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except Exception as exc:
        raise RuntimeError("OpenRouter response did not contain message content.") from exc
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        combined = "".join(parts).strip()
        if combined:
            return combined
    raise RuntimeError("OpenRouter response content was not text.")


def parse_decision(raw: str | dict[str, Any]) -> AgentDecision:
    if isinstance(raw, dict):
        return AgentDecision.model_validate(raw)
    try:
        return AgentDecision.model_validate_json(raw)
    except ValidationError:
        extracted = extract_json_object(raw)
        return AgentDecision.model_validate_json(extracted)


def extract_json_object(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text
