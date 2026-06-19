from __future__ import annotations

from appv22.ai.overflow import is_context_overflow
from appv22.runtime.provider_errors import is_context_overflow_error


def test_detects_overflow_messages() -> None:
    assert is_context_overflow("This prompt is too long for the model")
    assert is_context_overflow("context_length_exceeded")
    assert is_context_overflow("input token count of 200000 exceeds the maximum")


def test_ignores_rate_limit_and_throttling() -> None:
    assert not is_context_overflow("Throttling error: slow down")
    assert not is_context_overflow("rate limit reached, too many requests")
    assert not is_context_overflow("")


def test_runtime_alias_still_works() -> None:
    assert is_context_overflow_error("exceeds the context window") is True
