"""Provider context overflow classification for AppV2.1."""

from __future__ import annotations


class ContextOverflowPolicy:
    """Identifies provider failures caused by context/window size limits."""

    _MARKERS = (
        "context length",
        "context_length",
        "maximum context",
        "too many tokens",
        "413",
        "request too large",
    )

    def is_context_overflow(self, error: BaseException) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in self._MARKERS)
