# appv24 Built-In Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a built-in-only profile system to appv24 so `appv24 --profile <name>` can select focused runtime behavior without loading user/project profile files or rewriting kernel control flow.

**Architecture:** Create a green-zone `appv24.profiles` package containing immutable built-in profile definitions and a small resolver. Wire the CLI to resolve a profile at startup, then pass profile options into existing `CodingApp` and `AgentSession` knobs. Touch `app.py` only for explicit constructor pass-throughs; do not change agent loop, provider streaming, compaction, session store, or tool schemas.

**Tech Stack:** Python dataclasses, argparse, existing `CodingApp`, existing `AgentSession` tool filters and prompt hooks, pytest.

---

## Source Basis

- `rules.md` says appv24 should grow through profiles/apps/plugins beside the coding agent, not by adding use-case logic to the kernel.
- `rules.md` marks `profiles/`, `docs/`, and tests as green zone.
- `rules.md` marks `app.py` as red zone. This plan allows only a narrow constructor pass-through in `appV2.4/appv24/app.py`.
- `appV2.4/appv24/coding_agent/agent_session.py` already accepts `active_tool_names`, `allowed_tool_names`, `excluded_tool_names`, `custom_prompt`, `append_system_prompt`, `max_iterations`, and `tool_loop_guardrails`.
- `appV2.4/appv24/cli.py` already resolves cwd, dotenv, model, thinking level, scoped models, generation params, session path, and runtime options before constructing `CodingApp`.
- Pi's preset extension proves the useful shape: provider/model/thinking/tools/instructions. appv24 should borrow the passive config shape, not Pi's active extension hooks.

## Scope Boundaries

Allowed files:

- Create: `appV2.4/appv24/profiles/__init__.py`
- Create: `appV2.4/appv24/profiles/types.py`
- Create: `appV2.4/appv24/profiles/builtins.py`
- Create: `appV2.4/appv24/profiles/resolver.py`
- Create: `appV2.4/tests/test_profiles.py`
- Modify: `appV2.4/appv24/app.py`
- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/tests/test_app_integration.py`
- Modify: `appV2.4/tests/test_cli.py`
- Create: `docs/architecture/appv24-built-in-profiles.md`

Explicitly out of scope:

- Do not load profiles from `~/.appv24`, `~/.pi`, `.appv24`, `.pi`, project files, package files, or environment variables.
- Do not add mid-session profile switching.
- Do not add `/profile` TUI commands.
- Do not persist profile state into session JSONL.
- Do not register dynamic tools from profiles.
- Do not mutate provider payloads from profiles.
- Do not change tool schemas.
- Do not modify `appV2.4/appv24/agent/agent.py`.
- Do not modify `appV2.4/appv24/agent/agent_loop.py`.
- Do not modify `appV2.4/appv24/agent/types.py`.
- Do not modify `appV2.4/appv24/ai/types.py`.
- Do not modify `appV2.4/appv24/ai/stream.py`.
- Do not modify `appV2.4/appv24/ai/validation.py`.
- Do not modify `appV2.4/appv24/compaction/`.
- Do not modify `appV2.4/appv24/coding_agent/session_store.py`.
- Do not build GHCR or publish npm.

If execution discovers a required red-zone change beyond constructor pass-through in `app.py`, stop and ask Lewis for explicit `kernel change` approval with the exact reason, rollback path, and regression tests.

## File Responsibilities

- `appV2.4/appv24/profiles/types.py`: profile dataclasses and conversion helpers. No I/O.
- `appV2.4/appv24/profiles/builtins.py`: static built-in profile registry. No I/O and no dynamic imports from runtime internals except tool-name constants if needed.
- `appV2.4/appv24/profiles/resolver.py`: normalize names, validate uniqueness/tool references, list profiles, resolve selected profile, and produce `CodingApp` keyword options.
- `appV2.4/appv24/app.py`: accept profile-derived session options and pass them unchanged to `AgentSession`.
- `appV2.4/appv24/cli.py`: add `--profile` and `--list-profiles`, resolve built-in profile before model startup, apply CLI overrides over profile defaults, and pass profile options into `CodingApp`.
- `appV2.4/tests/test_profiles.py`: contract tests for built-ins and resolver behavior.
- `appV2.4/tests/test_app_integration.py`: pass-through regression tests for `CodingApp`.
- `appV2.4/tests/test_cli.py`: CLI selection, listing, unknown-profile, and override tests.
- `docs/architecture/appv24-built-in-profiles.md`: user/developer documentation for built-in-only v1 behavior.

## Behavioral Contract

Built-in profiles:

```text
coding
read-only
planning
research-lite
sre-lite
security-lab-lite
```

Required behavior:

- `appv24 --profile coding ...` is equivalent to current default behavior.
- Omitting `--profile` selects `coding`.
- `appv24 --profile read-only ...` enables only `read`, `grep`, `find`, and `ls`.
- `read-only`, `planning`, `research-lite`, `sre-lite`, and `security-lab-lite` must not activate `bash`, `edit`, or `write` in v1.
- Profile prompt text is appended through the existing system prompt append path.
- CLI flags override profile defaults:
  - `--model` overrides profile `default_model`.
  - `--provider` overrides profile `default_provider`.
  - `--thinking` overrides profile `thinking_level`.
  - `--max-iterations` overrides profile `max_iterations`.
  - `--tool-loop-hard-stop` overrides or augments profile `tool_loop_guardrails`.
- `--list-profiles` prints built-in profile IDs and descriptions without starting providers, loading dotenv model auth, or constructing `CodingApp`.
- Unknown profiles fail before provider/model/app startup.
- Profiles do not persist into session history.
- Profiles do not read any user/project profile config files.

## Task 1: Profile Types, Built-Ins, and Resolver

**Files:**

- Create: `appV2.4/appv24/profiles/__init__.py`
- Create: `appV2.4/appv24/profiles/types.py`
- Create: `appV2.4/appv24/profiles/builtins.py`
- Create: `appV2.4/appv24/profiles/resolver.py`
- Create: `appV2.4/tests/test_profiles.py`

- [ ] **Step 1: Write failing profile contract tests**

Create `appV2.4/tests/test_profiles.py` with:

```python
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
```

- [ ] **Step 2: Run profile tests and confirm import failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.profiles'
```

- [ ] **Step 3: Add profile dataclasses**

Create `appV2.4/appv24/profiles/types.py` with:

```python
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
```

- [ ] **Step 4: Add built-in profiles**

Create `appV2.4/appv24/profiles/builtins.py` with:

```python
"""Built-in appv24 profiles.

These profiles are intentionally static. v1 does not load user, project, package,
or environment-defined profiles.
"""

from __future__ import annotations

from appv24.profiles.types import AppProfile

DEFAULT_PROFILE_ID = "coding"
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")
MUTATING_TOOLS = ("bash", "edit", "write")

_READ_ONLY_APPEND = (
    "Profile: read-only.\n"
    "Inspect and explain the workspace using only read-only tools. "
    "Do not edit, write, delete, move, or create files. "
    "Do not run shell commands."
)

_PLANNING_APPEND = (
    "Profile: planning.\n"
    "Create careful implementation plans from repository evidence. "
    "Do not edit, write, delete, move, or create files. "
    "When the work needs implementation, stop with a clear plan and wait for approval."
)

_RESEARCH_LITE_APPEND = (
    "Profile: research-lite.\n"
    "Use local repository files as the evidence base. Summarize findings, cite paths, "
    "and separate facts from inference. Do not modify files or run shell commands."
)

_SRE_LITE_APPEND = (
    "Profile: sre-lite.\n"
    "Inspect infrastructure, deployment, configuration, and operational files using "
    "read-only tools. Produce incident notes, risk summaries, or runbook drafts in the "
    "final response only. Do not modify files or run shell commands."
)

_SECURITY_LAB_LITE_APPEND = (
    "Profile: security-lab-lite.\n"
    "Review local code and configuration defensively. Focus on attack surface, likely "
    "risks, mitigations, and verification steps. Do not exploit systems, modify files, "
    "or run shell commands."
)

BUILTIN_PROFILES: tuple[AppProfile, ...] = (
    AppProfile(
        id="coding",
        display_name="Coding",
        description="Default appv24 coding agent behavior.",
    ),
    AppProfile(
        id="read-only",
        display_name="Read Only",
        description="Inspect and explain files without mutation.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_READ_ONLY_APPEND,
        max_iterations=40,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="planning",
        display_name="Planning",
        description="Read-only planning mode for implementation design.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_PLANNING_APPEND,
        max_iterations=60,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="research-lite",
        display_name="Research Lite",
        description="Local evidence research without workspace mutation.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_RESEARCH_LITE_APPEND,
        max_iterations=50,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="sre-lite",
        display_name="SRE Lite",
        description="Read-only operational and infrastructure analysis.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_SRE_LITE_APPEND,
        max_iterations=50,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
    AppProfile(
        id="security-lab-lite",
        display_name="Security Lab Lite",
        description="Defensive local code and configuration review.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_SECURITY_LAB_LITE_APPEND,
        max_iterations=50,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
)
```

- [ ] **Step 5: Add resolver helpers**

Create `appV2.4/appv24/profiles/resolver.py` with:

```python
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
```

- [ ] **Step 6: Export profile API**

Create `appV2.4/appv24/profiles/__init__.py` with:

```python
"""Built-in profile API for appv24."""

from appv24.profiles.builtins import BUILTIN_PROFILES, DEFAULT_PROFILE_ID
from appv24.profiles.resolver import (
    get_profile,
    list_profiles,
    normalize_profile_id,
    profile_coding_app_kwargs,
    validate_builtin_profiles,
)
from appv24.profiles.types import AppProfile, ProfileError

__all__ = [
    "AppProfile",
    "BUILTIN_PROFILES",
    "DEFAULT_PROFILE_ID",
    "ProfileError",
    "get_profile",
    "list_profiles",
    "normalize_profile_id",
    "profile_coding_app_kwargs",
    "validate_builtin_profiles",
]
```

- [ ] **Step 7: Run profile tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py -q
```

Expected:

```text
6 passed
```

- [ ] **Step 8: Commit Task 1**

Run:

```bash
git add appV2.4/appv24/profiles appV2.4/tests/test_profiles.py
git commit -m "feat(appv24): add built-in profile registry"
```

## Task 2: CodingApp Profile Pass-Through

**Files:**

- Modify: `appV2.4/appv24/app.py`
- Modify: `appV2.4/tests/test_app_integration.py`

- [ ] **Step 1: Write failing CodingApp pass-through test**

Append to `appV2.4/tests/test_app_integration.py` near the existing `CodingApp` constructor tests:

```python
def test_coding_app_forwards_profile_session_options_to_agent_session(tmp_path: Path) -> None:
    model = faux_model()
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(),
        enable_tui=False,
        allowed_tool_names=["read", "grep", "find", "ls"],
        excluded_tool_names=["bash", "edit", "write"],
        append_system_prompt="Profile appendix: no file mutation.",
        max_iterations=40,
        tool_loop_guardrails={"hard_stop_enabled": True},
    )

    assert app.session.get_active_tool_names() == ["read", "grep", "find", "ls"]
    assert "Profile appendix: no file mutation." in app.session.system_prompt
    assert "write:" not in app.session.system_prompt
    assert app.session.agent.max_iterations == 40
```

- [ ] **Step 2: Run the focused app test and confirm failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_app_integration.py::test_coding_app_forwards_profile_session_options_to_agent_session -q
```

Expected:

```text
TypeError: CodingApp.__init__() got an unexpected keyword argument 'allowed_tool_names'
```

- [ ] **Step 3: Add narrow constructor pass-throughs**

Modify `appV2.4/appv24/app.py`.

Add these keyword-only arguments to `CodingApp.__init__` after `tool_loop_guardrails`:

```python
        active_tool_names: list[str] | None = None,
        allowed_tool_names: list[str] | None = None,
        excluded_tool_names: list[str] | None = None,
        custom_prompt: str | None = None,
        append_system_prompt: str | None = None,
```

Then pass them into `AgentSession(...)`:

```python
            active_tool_names=active_tool_names,
            allowed_tool_names=allowed_tool_names,
            excluded_tool_names=excluded_tool_names,
            custom_prompt=custom_prompt,
            append_system_prompt=append_system_prompt,
```

Do not change `CodingApp._transform_context`, compaction setup, event subscription, renderer construction, or session lifecycle.

- [ ] **Step 4: Run focused app test and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_app_integration.py::test_coding_app_forwards_profile_session_options_to_agent_session -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Run nearby app integration tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_app_integration.py -q
```

Expected:

```text
all tests in test_app_integration.py pass
```

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add appV2.4/appv24/app.py appV2.4/tests/test_app_integration.py
git commit -m "feat(appv24): pass profile options into coding app"
```

## Task 3: CLI Profile Selection

**Files:**

- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Write failing CLI profile tests**

Append these tests to `appV2.4/tests/test_cli.py` near other startup CLI tests:

```python
def test_cli_list_profiles_prints_builtins_without_starting_app(monkeypatch, capsys) -> None:
    def fail_startup(*args, **kwargs):
        raise AssertionError("profile listing should not start providers, models, or app")

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--list-profiles"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "coding" in captured.out
    assert "read-only" in captured.out
    assert "security-lab-lite" in captured.out
    assert captured.err == ""


def test_cli_read_only_profile_passes_profile_options_to_coding_app(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "read-only", "--plain", "inspect"])

    kwargs = created["kwargs"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert kwargs["allowed_tool_names"] == ["read", "grep", "find", "ls"]
    assert kwargs["excluded_tool_names"] == ["bash", "edit", "write"]
    assert kwargs["max_iterations"] == 40
    assert kwargs["tool_loop_guardrails"] == {"hard_stop_enabled": True}
    assert "Do not edit, write, delete, move, or create files." in kwargs["append_system_prompt"]


def test_cli_default_profile_is_coding_noop(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--plain", "inspect"])

    kwargs = created["kwargs"]
    assert exit_code == 0
    assert "allowed_tool_names" not in kwargs
    assert "excluded_tool_names" not in kwargs
    assert "append_system_prompt" not in kwargs


def test_cli_unknown_profile_stops_before_provider_startup(monkeypatch, tmp_path, capsys) -> None:
    def fail_startup(*args, **kwargs):
        raise AssertionError("unknown profile should stop before provider/model/app startup")

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--cwd", str(tmp_path), "--profile", "missing", "--plain", "inspect"])

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert 'Unknown profile "missing"' in captured.err


def test_cli_flags_override_profile_defaults(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            observed["app_kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    def fake_startup_model_from_env(*args, **kwargs):
        observed["startup_kwargs"] = kwargs
        return cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_startup_model_from_env", fake_startup_model_from_env)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--profile",
            "planning",
            "--provider",
            "openrouter",
            "--model",
            "qwen/qwen3.7-max",
            "--thinking",
            "high",
            "--max-iterations",
            "12",
            "--tool-loop-hard-stop",
            "--plain",
            "inspect",
        ]
    )

    startup_kwargs = observed["startup_kwargs"]
    app_kwargs = observed["app_kwargs"]
    assert exit_code == 0
    assert startup_kwargs["cli_provider"] == "openrouter"
    assert startup_kwargs["cli_model"] == "qwen/qwen3.7-max"
    assert startup_kwargs["cli_thinking"] == "high"
    assert app_kwargs["thinking_level"] == "high"
    assert app_kwargs["max_iterations"] == 12
    assert app_kwargs["tool_loop_guardrails"] == {"hard_stop_enabled": True}
```

- [ ] **Step 2: Run focused CLI tests and confirm failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_cli.py::test_cli_list_profiles_prints_builtins_without_starting_app \
  appV2.4/tests/test_cli.py::test_cli_read_only_profile_passes_profile_options_to_coding_app \
  appV2.4/tests/test_cli.py::test_cli_default_profile_is_coding_noop \
  appV2.4/tests/test_cli.py::test_cli_unknown_profile_stops_before_provider_startup \
  appV2.4/tests/test_cli.py::test_cli_flags_override_profile_defaults \
  -q
```

Expected first failure:

```text
SystemExit: 2
error: unrecognized arguments: --list-profiles
```

- [ ] **Step 3: Import profile resolver in CLI**

Modify imports in `appV2.4/appv24/cli.py`:

```python
from appv24.profiles import (
    DEFAULT_PROFILE_ID,
    ProfileError,
    get_profile,
    list_profiles,
    profile_coding_app_kwargs,
    validate_builtin_profiles,
)
```

- [ ] **Step 4: Add profile parser flags**

In `main()`, after model-related flags, add:

```python
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_ID,
        help="Built-in app profile to use: coding, read-only, planning, research-lite, sre-lite, security-lab-lite",
    )
    parser.add_argument("--list-profiles", action="store_true", help="List built-in profiles and exit")
```

- [ ] **Step 5: Add profile listing helper**

Add this helper near `_print_model_list()`:

```python
def _print_profile_list() -> None:
    for profile in list_profiles():
        print(f"{profile.id}\t{profile.description}")
```

- [ ] **Step 6: Resolve and validate profile before provider startup**

In `main()`, immediately after `args = parser.parse_args(argv)`, add:

```python
    try:
        validate_builtin_profiles()
        selected_profile = get_profile(args.profile)
    except ProfileError as error:
        parser.error(str(error))

    if args.list_profiles:
        _print_profile_list()
        return 0
```

This must appear before export, cwd validation, dotenv loading, provider registration, model resolution, or `CodingApp` construction.

- [ ] **Step 7: Apply profile model/thinking defaults with CLI override precedence**

Replace the `_startup_model_from_env(...)` argument values in `main()` with effective values:

```python
    effective_provider = args.provider or selected_profile.default_provider
    effective_model = args.model or (None if args.models else selected_profile.default_model)
    effective_thinking = args.thinking or selected_profile.thinking_level
```

Then call:

```python
        startup = _startup_model_from_env(
            dotenv_path,
            config=config,
            cli_provider=effective_provider,
            cli_model=effective_model,
            cli_thinking=effective_thinking,
            cli_models=_split_models_arg(args.models),
        )
```

Keep existing invalid `--thinking` validation before this block.

- [ ] **Step 8: Merge profile runtime options with CLI runtime overrides**

Replace the current `runtime_options` block with:

```python
    runtime_options: dict[str, object] = profile_coding_app_kwargs(selected_profile)
    if args.max_iterations is not None:
        runtime_options["max_iterations"] = args.max_iterations
    if args.tool_loop_hard_stop:
        guardrails = dict(runtime_options.get("tool_loop_guardrails") or {})
        guardrails["hard_stop_enabled"] = True
        runtime_options["tool_loop_guardrails"] = guardrails
```

- [ ] **Step 9: Preserve current CodingApp construction**

Keep the existing `CodingApp(...)` construction shape and continue passing:

```python
        thinking_level=startup.thinking_level or "off",
        scoped_models=startup.scoped_models,
        **runtime_options,
```

Do not pass the profile object itself into `CodingApp`. The app should receive only concrete existing runtime/session options.

- [ ] **Step 10: Run focused CLI tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_cli.py::test_cli_list_profiles_prints_builtins_without_starting_app \
  appV2.4/tests/test_cli.py::test_cli_read_only_profile_passes_profile_options_to_coding_app \
  appV2.4/tests/test_cli.py::test_cli_default_profile_is_coding_noop \
  appV2.4/tests/test_cli.py::test_cli_unknown_profile_stops_before_provider_startup \
  appV2.4/tests/test_cli.py::test_cli_flags_override_profile_defaults \
  -q
```

Expected:

```text
5 passed
```

- [ ] **Step 11: Run full CLI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py -q
```

Expected:

```text
all tests in test_cli.py pass
```

- [ ] **Step 12: Commit Task 3**

Run:

```bash
git add appV2.4/appv24/cli.py appV2.4/tests/test_cli.py
git commit -m "feat(appv24): wire built-in profiles into cli startup"
```

## Task 4: Profile Documentation

**Files:**

- Create: `docs/architecture/appv24-built-in-profiles.md`

- [ ] **Step 1: Add architecture documentation**

Create `docs/architecture/appv24-built-in-profiles.md` with:

```markdown
# appv24 Built-In Profiles

appv24 profiles are passive startup configuration. A profile may select tools,
append system prompt text, set iteration limits, and set guardrail defaults.
Profiles do not execute code and do not change agent loop, provider streaming,
compaction, session store, or tool schemas.

## Built-In Only

The first profile implementation only supports profiles compiled into appv24.
It does not load profile files from the user home directory, project directory,
packages, or environment variables.

## Profiles

| ID | Purpose | Tools |
| --- | --- | --- |
| `coding` | Default appv24 coding agent behavior | Current default |
| `read-only` | Inspect and explain without mutation | `read`, `grep`, `find`, `ls` |
| `planning` | Read-only implementation planning | `read`, `grep`, `find`, `ls` |
| `research-lite` | Local evidence research | `read`, `grep`, `find`, `ls` |
| `sre-lite` | Read-only operational analysis | `read`, `grep`, `find`, `ls` |
| `security-lab-lite` | Defensive local code/config review | `read`, `grep`, `find`, `ls` |

## CLI

```bash
appv24 --list-profiles
appv24 --profile coding --cwd .
appv24 --profile read-only --cwd .
appv24 --profile planning --cwd .
appv24 --profile research-lite --cwd .
appv24 --profile sre-lite --cwd .
appv24 --profile security-lab-lite --cwd .
```

## Override Rules

CLI flags override profile defaults for model, provider, thinking level, maximum
iterations, and tool-loop hard-stop behavior.

## Boundary

Profiles are not extensions. Extensions may eventually contain active hooks,
commands, UI integration, provider payload hooks, or dynamic tools. Profiles
must remain declarative startup configuration.
```

- [ ] **Step 2: Commit Task 4**

Run:

```bash
git add docs/architecture/appv24-built-in-profiles.md
git commit -m "docs(appv24): document built-in profiles"
```

## Task 5: Final Verification

**Files:**

- No new files.

- [ ] **Step 1: Run focused profile suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_profiles.py \
  appV2.4/tests/test_app_integration.py::test_coding_app_forwards_profile_session_options_to_agent_session \
  appV2.4/tests/test_cli.py::test_cli_list_profiles_prints_builtins_without_starting_app \
  appV2.4/tests/test_cli.py::test_cli_read_only_profile_passes_profile_options_to_coding_app \
  appV2.4/tests/test_cli.py::test_cli_default_profile_is_coding_noop \
  appV2.4/tests/test_cli.py::test_cli_unknown_profile_stops_before_provider_startup \
  appV2.4/tests/test_cli.py::test_cli_flags_override_profile_defaults \
  -q
```

Expected:

```text
all focused tests pass
```

- [ ] **Step 2: Run appv24 Python test suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected:

```text
all appV2.4 tests pass
```

- [ ] **Step 3: Run package CLI QA**

Run:

```bash
npm run qa:appv24-cli
```

Expected:

```text
node tests pass
npm pack --dry-run succeeds
```

- [ ] **Step 4: Manual CLI smoke checks**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m appv24.cli --list-profiles
```

Expected output includes:

```text
coding
read-only
planning
research-lite
sre-lite
security-lab-lite
```

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m appv24.cli --profile read-only --cwd . --plain "list the active constraints"
```

Expected:

```text
The command starts normally, uses the configured model from dotenv/auth, and does not expose bash/edit/write tools in the session prompt.
```

- [ ] **Step 5: Review red/yellow-zone impact**

Confirm the final diff:

```bash
git diff --stat HEAD~4..HEAD
git diff --name-only HEAD~4..HEAD
```

Expected changed areas:

```text
appV2.4/appv24/profiles/
appV2.4/appv24/app.py
appV2.4/appv24/cli.py
appV2.4/tests/test_profiles.py
appV2.4/tests/test_app_integration.py
appV2.4/tests/test_cli.py
docs/architecture/appv24-built-in-profiles.md
```

Red-zone check:

```text
Only appV2.4/appv24/app.py is red-zone touched, and only for constructor pass-through.
```

Yellow-zone check:

```text
appV2.4/appv24/cli.py is changed for profile startup selection and covered by CLI tests.
```

- [ ] **Step 6: Final commit if any verification-only fixes were needed**

If verification required additional fixes, commit them:

```bash
git add appV2.4 docs
git commit -m "test(appv24): verify built-in profiles"
```

If no fixes were needed, do not create an empty commit.

## Rollback Plan

Rollback is safe because profiles are additive startup configuration:

```bash
git revert <task-4-docs-commit> <task-3-cli-commit> <task-2-app-pass-through-commit> <task-1-profile-registry-commit>
```

If reverting manually:

- Remove `appV2.4/appv24/profiles/`.
- Remove profile imports and flags from `appV2.4/appv24/cli.py`.
- Remove profile pass-through arguments from `appV2.4/appv24/app.py`.
- Remove profile tests and docs.

No session migrations are required because v1 does not persist profile state.

## Self-Review

- Spec coverage: The plan implements built-in profiles only, default `coding`, profile CLI selection, profile listing, tool restriction, prompt appendix, CLI override precedence, tests, and docs.
- Red-zone control: The only planned red-zone touch is `app.py`, limited to constructor pass-through into existing `AgentSession` fields.
- Placeholder scan: The plan contains no unspecified implementation sections.
- Type consistency: The same names are used across tasks: `AppProfile`, `ProfileError`, `DEFAULT_PROFILE_ID`, `get_profile`, `list_profiles`, `profile_coding_app_kwargs`, and `validate_builtin_profiles`.
- Scope check: User/project profile loading, mid-session switching, extension hooks, workflow runtime, provider payload mutation, and release work are explicitly out of scope.
