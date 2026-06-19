"""Context-overflow detection. Port of pi overflow.ts + appv22 provider_errors.py."""

from __future__ import annotations

import re
from typing import Any

_OVERFLOW_PATTERNS = (
    re.compile(r"prompt is too long", re.I),
    re.compile(r"request_too_large", re.I),
    re.compile(r"input is too long for requested model", re.I),
    re.compile(r"exceeds the context window", re.I),
    re.compile(r"exceeds (?:the )?(?:model'?s )?maximum context length(?: of [\d,]+ tokens?|\s*\([\d,]+\))?", re.I),
    re.compile(r"input token count.*exceeds the maximum", re.I),
    re.compile(r"maximum prompt length is \d+", re.I),
    re.compile(r"reduce the length of the messages", re.I),
    re.compile(r"maximum context length is \d+ tokens", re.I),
    re.compile(r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?", re.I),
    re.compile(r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)", re.I),
    re.compile(r"exceeds the limit of \d+", re.I),
    re.compile(r"exceeds the available context size", re.I),
    re.compile(r"greater than the context length", re.I),
    re.compile(r"context window exceeds limit", re.I),
    re.compile(r"exceeded model token limit", re.I),
    re.compile(r"too large for model with \d+ maximum context length", re.I),
    re.compile(r"model_context_window_exceeded", re.I),
    re.compile(r"prompt too long; exceeded (?:max )?context length", re.I),
    re.compile(r"context[_ ]length[_ ]exceeded", re.I),
    re.compile(r"too many tokens", re.I),
    re.compile(r"token limit exceeded", re.I),
    re.compile(r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.I),
)

_NON_OVERFLOW_PATTERNS = (
    re.compile(r"^(Throttling error|Service unavailable):", re.I),
    re.compile(r"rate limit", re.I),
    re.compile(r"too many requests", re.I),
)


def is_context_overflow(error: "BaseException | Any") -> bool:
    text = _error_text(error)
    if not text:
        return False
    if any(pattern.search(text) for pattern in _NON_OVERFLOW_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in _OVERFLOW_PATTERNS)


def _error_text(error: "BaseException | Any") -> str:
    parts = [str(error)]
    for attr in ("message", "error", "body", "response"):
        value = getattr(error, attr, None)
        if value:
            parts.append(str(value))
    status_code = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status_code in {400, 413} and len(" ".join(parts).strip()) <= 16:
        parts.append(f"{status_code} status code (no body)")
    return " ".join(part for part in parts if part).strip()
