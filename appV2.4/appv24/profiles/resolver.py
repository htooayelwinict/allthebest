"""Resolver and validation helpers for built-in appv24 profiles."""

from __future__ import annotations

from collections.abc import Iterable

from appv24.coding_agent.tools import allToolNames
from appv24.profiles.builtins import BUILTIN_PROFILES, DEFAULT_PROFILE_ID
from appv24.profiles.types import AppProfile, ProfileError


def normalize_profile_id(value: str | None) -> str:
    raw = (value or DEFAULT_PROFILE_ID).strip().lower()
    return raw.replace("_", "-")


def list_profiles() -> list[AppProfile]:
    return list(BUILTIN_PROFILES)


def get_profile(profile_id: str | None) -> AppProfile:
    normalized = normalize_profile_id(profile_id)
    for profile in BUILTIN_PROFILES:
        if profile.id == normalized:
            return profile
    available = ", ".join(profile.id for profile in BUILTIN_PROFILES)
    raise ProfileError(f'Unknown profile "{profile_id}". Available profiles: {available}')


def validate_builtin_profiles() -> None:
    seen: set[str] = set()
    known_tools = set(allToolNames)
    for profile in BUILTIN_PROFILES:
        if not profile.id:
            raise ProfileError("Built-in profile id cannot be empty")
        if profile.id in seen:
            raise ProfileError(f'Duplicate built-in profile id "{profile.id}"')
        seen.add(profile.id)
        _validate_tool_names(profile, known_tools)
    if DEFAULT_PROFILE_ID not in seen:
        raise ProfileError(f'Default profile "{DEFAULT_PROFILE_ID}" is not registered')


def profile_coding_app_kwargs(profile: AppProfile) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if profile.context_length is not None:
        kwargs["context_length"] = profile.context_length
    if profile.custom_prompt is not None:
        kwargs["custom_prompt"] = profile.custom_prompt
    if profile.append_system_prompt is not None:
        kwargs["append_system_prompt"] = profile.append_system_prompt
    if profile.active_tool_names is not None:
        kwargs["active_tool_names"] = list(profile.active_tool_names)
    if profile.allowed_tool_names is not None:
        kwargs["allowed_tool_names"] = list(profile.allowed_tool_names)
    if profile.excluded_tool_names:
        kwargs["excluded_tool_names"] = list(profile.excluded_tool_names)
    if profile.max_iterations is not None:
        kwargs["max_iterations"] = profile.max_iterations
    if profile.tool_loop_guardrails:
        kwargs["tool_loop_guardrails"] = dict(profile.tool_loop_guardrails)
    return kwargs


def _validate_tool_names(profile: AppProfile, known_tools: set[str]) -> None:
    for tool_name in _iter_profile_tool_names(profile):
        if tool_name not in known_tools:
            raise ProfileError(f'Profile "{profile.id}" references unknown tool "{tool_name}"')


def _iter_profile_tool_names(profile: AppProfile) -> Iterable[str]:
    yield from profile.active_tool_names or ()
    yield from profile.allowed_tool_names or ()
    yield from profile.excluded_tool_names
