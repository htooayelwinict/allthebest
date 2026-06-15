"""Provider context overflow classification for AppV2.1."""

from __future__ import annotations


class ContextOverflowPolicy:
    """Identifies provider failures caused by context/window size limits."""

    _MARKERS = (
        "context length",
        "context_length",
        "maximum context",
        "too many tokens",
        "request too large",
    )
    _HTTP_413_MARKERS = (
        "http 413",
        "413 request too large",
        "413 payload too large",
    )

    def is_context_overflow(self, error: BaseException) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in self._MARKERS) or any(marker in message for marker in self._HTTP_413_MARKERS)
