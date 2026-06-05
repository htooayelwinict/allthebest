"""Small redaction helper for prompt decomposition."""

from __future__ import annotations

import re


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


def redact_secrets(value: str) -> str:
    redacted = value or ""
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1) if match.groups() else 'secret'}=[REDACTED]", redacted)
    return redacted
