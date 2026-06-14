"""OpenRouter/OpenAI-compatible JSON client for AppV2.1 providers."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]{12,})\b"),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_-])([\"']?(?:api[-_ ]?key|x-api-key|authorization|access[-_ ]?token|"
        r"refresh[-_ ]?token|id[-_ ]?token|token|secret)[\"']?\s*[:=]\s*)([\"']?)"
        r"([^\"'\s,;{}\[\]]{12,})([\"']?)"
    ),
    re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{8,})\b"),
    re.compile(r"\b(ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"),
)


class AppV21JSONClient:
    """Small SDK-backed client that requests structured JSON payloads."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        temperature: float = 0.0,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        seed: int | None = None,
        stop: list[str] | None = None,
        response_format: str = "json_schema",
        provider_sort: str | None = "latency",
        max_tokens: int | None = None,
    ) -> None:
        from openrouter import OpenRouter

        self._api_key_for_redaction = api_key
        self._model = model
        self._temperature = temperature
        self._top_p = top_p
        self._frequency_penalty = frequency_penalty
        self._presence_penalty = presence_penalty
        self._seed = seed
        self._stop = list(stop or [])
        self._response_format = response_format
        self._provider_sort = provider_sort
        self._max_tokens = max_tokens
        self._timeout_ms = int(timeout_seconds * 1000)
        self._client = OpenRouter(api_key=api_key, server_url=base_url.rstrip("/"), timeout_ms=self._timeout_ms)
        self._usage: dict[str, Any] = self._empty_usage()

    @staticmethod
    def _empty_usage() -> dict[str, Any]:
        return {
            "model_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "cost": 0.0,
            "stages": [],
        }

    def reset_usage(self) -> None:
        self._usage = self._empty_usage()

    def usage_snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        usage = deepcopy(self._usage)
        if reset:
            self.reset_usage()
        return usage

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
            kwargs["max_tokens"] = self._max_tokens
        if self._top_p is not None:
            kwargs["top_p"] = self._top_p
        if self._frequency_penalty is not None:
            kwargs["frequency_penalty"] = self._frequency_penalty
        if self._presence_penalty is not None:
            kwargs["presence_penalty"] = self._presence_penalty
        if self._seed is not None:
            kwargs["seed"] = self._seed
        if self._stop:
            kwargs["stop"] = self._stop
        if self._response_format in {"json_schema", "json_object"}:
            kwargs["plugins"] = [{"id": "response-healing"}]
        try:
            response = self._client.chat.send(**kwargs)
        except Exception as exc:  # pragma: no cover - network/provider variability
            provider_error = _redact_secret(str(exc), [self._api_key_for_redaction])
            raise RuntimeError(
                f"Model request for stage {stage} failed before receiving a response "
                f"provider_error_type={type(exc).__name__} provider_error={provider_error}"
            ) from None
        self._record_usage(stage=stage, response_usage=self._extract_usage(response))
        return self._extract_content(stage, response)

    def _record_usage(self, *, stage: str, response_usage: dict[str, Any]) -> None:
        self._usage["model_calls"] += 1
        normalized = self._normalize_usage(response_usage)
        self._usage["stages"].append({"stage": stage, **normalized})
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "cost",
        ):
            self._usage[key] += normalized.get(key, 0.0 if key == "cost" else 0)

    def _normalize_usage(self, usage: dict[str, Any]) -> dict[str, Any]:
        prompt_tokens = _safe_int(_get_value(usage, "prompt_tokens"))
        completion_tokens = _safe_int(_get_value(usage, "completion_tokens"))
        input_tokens = prompt_tokens or _safe_int(_get_value(usage, "input_tokens"))
        output_tokens = completion_tokens or _safe_int(_get_value(usage, "output_tokens"))
        cache_read_tokens = _safe_int(_get_value(usage, "prompt_tokens_details", "cached_tokens"))
        cache_read_tokens += _safe_int(_get_value(usage, "input_tokens_details", "cached_tokens"))
        cache_write_tokens = _safe_int(_get_value(usage, "prompt_tokens_details", "cache_write_tokens"))
        reasoning_tokens = _safe_int(_get_value(usage, "completion_tokens_details", "reasoning_tokens"))
        reasoning_tokens += _safe_int(_get_value(usage, "output_tokens_details", "reasoning_tokens"))
        cost = _safe_float(_get_value(usage, "cost"))
        upstream_cost = _get_value(usage, "cost_details", "upstream_inference_cost")
        if upstream_cost is not None:
            cost = cost or _safe_float(upstream_cost)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": _safe_int(_get_value(usage, "total_tokens")),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cost": cost,
            "raw_usage": _to_python_value(usage),
        }

    def _response_format_payload(self, stage: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self._response_format == "json_object":
            return {"type": "json_object"}
        return {"type": "json_schema", "json_schema": {"name": f"{stage}_output", "schema": schema, "strict": False}}

    def _extract_usage(self, response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        return _to_python_value(usage) if usage is not None else {}

    def _extract_content(self, stage: str, response: Any) -> str:
        try:
            content = response.choices[0].message.content
        except Exception as exc:
            raise RuntimeError(f"Model response for stage {stage} did not contain JSON content.") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = "".join(part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "") for part in content).strip()
            if text:
                return text
        raise RuntimeError(f"Model response for stage {stage} returned non-text content.")


def _get_value(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _to_python_value(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _safe_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _redact_secret(message: str, secrets: list[str]) -> str:
    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = SECRET_PATTERNS[0].sub(r"\1[redacted]", redacted)
    redacted = SECRET_PATTERNS[1].sub(r"\1\2[redacted]\4", redacted)
    for pattern in SECRET_PATTERNS[2:]:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted
