from __future__ import annotations

import pytest

from appv24.coding_agent.tools import allToolNames
from appv24.profiles import (
    DEFAULT_PROFILE_ID,
    ProfileError,
    get_profile,
    list_profiles,
    profile_coding_app_kwargs,
    validate_builtin_profiles,
)


def test_builtin_profile_names_are_unique_and_default_is_coding() -> None:
    profiles = list_profiles()
    ids = [profile.id for profile in profiles]

    assert DEFAULT_PROFILE_ID == "coding"
    assert ids[0] == "coding"
    assert len(ids) == len(set(ids))
    assert {"coding", "read-only", "planning", "research-lite", "sre-lite", "security-lab-lite"} <= set(ids)


def test_builtin_profiles_reference_existing_builtin_tools_only() -> None:
    validate_builtin_profiles()
    known_tools = set(allToolNames)

    for profile in list_profiles():
        for field_value in (
            profile.active_tool_names,
            profile.allowed_tool_names,
            profile.excluded_tool_names,
        ):
            for tool_name in field_value or ():
                assert tool_name in known_tools


def test_coding_profile_is_noop_for_current_default_behavior() -> None:
    profile = get_profile("coding")

    assert profile.custom_prompt is None
    assert profile.append_system_prompt is None
    assert profile.active_tool_names is None
    assert profile.allowed_tool_names is None
    assert profile.excluded_tool_names == ()
    assert profile.max_iterations is None
    assert profile.tool_loop_guardrails == {}
    assert profile_coding_app_kwargs(profile) == {}


def test_read_only_profile_selects_non_mutating_tools() -> None:
    profile = get_profile("read-only")
    kwargs = profile_coding_app_kwargs(profile)

    assert kwargs["allowed_tool_names"] == ["read", "grep", "find", "ls"]
    assert kwargs["excluded_tool_names"] == ["bash", "edit", "write"]
    assert "append_system_prompt" in kwargs
    assert "Do not edit, write, delete, move, or create files." in kwargs["append_system_prompt"]
    assert "bash" not in kwargs["allowed_tool_names"]
    assert "edit" not in kwargs["allowed_tool_names"]
    assert "write" not in kwargs["allowed_tool_names"]


def test_profile_lookup_normalizes_case_and_underscores() -> None:
    assert get_profile("Read_Only").id == "read-only"
    assert get_profile("SECURITY_LAB_LITE").id == "security-lab-lite"


def test_unknown_profile_raises_clear_error() -> None:
    with pytest.raises(ProfileError) as excinfo:
        get_profile("unknown")

    message = str(excinfo.value)
    assert 'Unknown profile "unknown"' in message
    assert "coding" in message
    assert "read-only" in message
