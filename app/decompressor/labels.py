"""Allowed decompressor labels and deterministic sanitizers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")

INPUT_TYPES = frozenset(
    {
        "question",
        "mutation_request",
        "ambiguous_request",
        "request",
    }
)

INTENTS = frozenset(
    {
        "code.fix",
        "observe_first",
        "research.lookup",
        "infra.debug",
        "question.answer",
    }
)

DOMAINS = frozenset(
    {
        "code",
        "infra",
        "research",
        "general",
    }
)

RISKS = frozenset(
    {
        "mutation_requested",
        "file_mutation",
        "needs_verification",
        "ambiguous_scope",
        "ambiguous_mutation",
        "observation_context_needed",
    }
)

CONTEXT_NEEDED = frozenset(
    {
        "repo_tree",
        "target_file",
        "scope_clarification",
    }
)

EXECUTION_HINTS = frozenset(
    {
        "inspect_target_file_before_patch",
        "verify_after_patch",
        "observe_first_required",
        "do_not_patch_before_observation",
    }
)

PLANNER_HINTS = frozenset(
    {
        "direct_planner",
        "code_planner",
        "research_planner",
        "infra_planner",
        "fallback_planner",
    }
)

BUDGET_HINTS = frozenset({"low", "medium", "high"})


def unique_allowed(values: Iterable[str], allowed: frozenset[str]) -> list[str]:
    """Return known labels in first-seen order."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in allowed and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def clamp_label(value: str | None, allowed: frozenset[str], default: str | None = None) -> str | None:
    if value in allowed:
        return value
    return default


def clamp_float(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, float(value)))
