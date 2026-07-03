"""Profile data contracts for appv24 built-in profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AppProfile:
    """Passive startup configuration for one appv24 profile."""

    id: str
    display_name: str
    description: str
    custom_prompt: str | None = None
    append_system_prompt: str | None = None
    active_tool_names: tuple[str, ...] | None = None
    allowed_tool_names: tuple[str, ...] | None = None
    excluded_tool_names: tuple[str, ...] = ()
    default_provider: str | None = None
    default_model: str | None = None
    thinking_level: str | None = None
    context_length: int | None = None
    max_iterations: int | None = None
    tool_loop_guardrails: Mapping[str, object] = field(default_factory=dict)


class ProfileError(ValueError):
    """Raised when profile selection or built-in profile validation fails."""
