"""Backward-compatible alias. Real implementation lives in appv22.ai.overflow."""

from __future__ import annotations

from appv22.ai.overflow import is_context_overflow

is_context_overflow_error = is_context_overflow

__all__ = ["is_context_overflow", "is_context_overflow_error"]
