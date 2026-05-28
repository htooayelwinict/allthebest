"""Secret redaction helpers for model-bound prompt text."""

from __future__ import annotations

import re


_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b([A-Z0-9_/-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret))"
            r"\b\s*[:=]\s*['\"]?([^\s'\",;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]+"), "authorization: bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
)


def redact_secrets(text: str) -> str:
    """Redact common key, token, password, and secret patterns before prompting."""

    redacted = text
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
