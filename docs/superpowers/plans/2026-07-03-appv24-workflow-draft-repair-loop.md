# appv24 Workflow Draft Repair Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded validator-driven repair loop for `workflow-designer-lite` so invalid workflow drafts are fed back to the model for correction before the user saves them.

**Architecture:** Keep workflow repair outside the appv24 kernel. Add a green-zone repair helper in `appV2.4/appv24/workflows/` that validates the latest assistant draft, builds compact validator feedback, and calls a supplied `run_turn(prompt)` callback for up to two repair attempts. Wire it into CLI and TUI profile wrappers only, so `agent_loop.py`, `AgentSession`, providers, compaction, session storage, and `app.py` remain untouched.

**Tech Stack:** Python dataclasses, existing appv24 workflow draft validator, existing `CodingApp.run_turn`, pytest, existing CLI/TUI test harnesses. No new third-party dependency and no Context7 docs dependency.

---

## Boundary Rules

Do not modify these frozen kernel files:

```text
appV2.4/appv24/agent/agent.py
appV2.4/appv24/agent/agent_loop.py
appV2.4/appv24/agent/types.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/compressor.py
appV2.4/appv24/compaction/timing.py
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Allowed files for this feature:

```text
appV2.4/appv24/workflows/repair.py
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_workflow_repair.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
docs/appv24/workflow-package-validator.md
```

The repair loop must remain passive:

```text
no workflow execution
no external API calls
no secret reads
no shell commands from the repair prompt
no file writes unless the existing /workflow-save command is used after a valid draft
```

## File Structure

Create one focused workflow module:

```text
appV2.4/appv24/workflows/repair.py
```

Responsibilities:

- Validate the latest assistant workflow draft from an existing messages list.
- Format compact validator issues for the model.
- Build a repair prompt that asks for a complete corrected package draft.
- Run a bounded repair loop through a supplied `run_turn(prompt)` callback.
- Return a small result object for CLI/TUI rendering.

Modify two yellow-zone integration points:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
```

Responsibilities:

- Activate repair only when `should_validate_workflow_drafts(profile_id)` is true.
- Run repair after the original assistant turn and before final validation rendering.
- Print or render repair attempt status.
- Keep `/workflow-save` dependent on the final valid draft.

Do not add workflow repair logic to the agent loop or provider layer.

## Task 1: Add Pure Repair Contract Tests

**Files:**
- Create: `appV2.4/tests/test_workflow_repair.py`
- Create later in Task 2: `appV2.4/appv24/workflows/repair.py`

- [ ] **Step 1: Write the failing tests**

Create `appV2.4/tests/test_workflow_repair.py` with this content:

```python
from __future__ import annotations

from types import SimpleNamespace

from appv24.workflows.repair import (
    WorkflowDraftRepairConfig,
    build_workflow_repair_prompt,
    compact_workflow_validation_issues,
    repair_workflow_draft_messages,
    validate_latest_workflow_draft,
)
from appv24.workflows.validator import validate_workflow_draft_text


def _assistant_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(role="assistant", content=[SimpleNamespace(type="text", text=text)])


def _valid_draft() -> str:
    return """
Here is the workflow package draft.

```yaml workflow.yaml
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved issue.
triggers:
  - id: weekly
    type: schedule
    schedule: "0 9 * * 5"
entrypoint: inspect_alerts
steps:
  - id: inspect_alerts
    uses: github.list_dependabot_alerts
    next: draft_issue
  - id: draft_issue
    uses: github.create_issue
    side_effect_class: create
    approval: human-review
    next: done
  - id: done
    type: terminal
```

```yaml connectors.yaml
version: appv24.connectors/v1
connectors:
  - id: github
    adapter: openapi
    description: GitHub REST API.
    actions:
      - id: list_dependabot_alerts
        operationId: dependabot_list_alerts
        side_effect: false
      - id: create_issue
        operationId: issues_create
        side_effect: true
        required_scopes:
          - issues:write
```

```yaml permissions.yaml
version: appv24.permissions/v1
approvals:
  - id: human-review
    mode: explicit
    required_for:
      - draft_issue
permissions:
  - connector: github
    actions:
      - list_dependabot_alerts
      - create_issue
    scopes:
      - dependabot:read
      - issues:write
```

```yaml dry_run_fixtures/happy_path.yaml
input:
  repo: owner/project
```

```yaml dry_run_fixtures/rejection_path.yaml
approval:
  human-review: rejected
```

```markdown rollback.md
# Rollback

Close the drafted issue if it was created.
```
"""


def _invalid_draft() -> str:
    return _valid_draft().replace("side_effect: true", "side_effect: send")


def test_validate_latest_workflow_draft_returns_none_without_assistant_text() -> None:
    assert validate_latest_workflow_draft([]) is None


def test_compact_workflow_validation_issues_caps_issue_count() -> None:
    result = validate_workflow_draft_text(_invalid_draft())

    rendered = compact_workflow_validation_issues(result, max_issues=1)

    assert rendered.startswith("- ")
    assert "connectors.yaml" in rendered
    assert "side_effect" in rendered
    assert rendered.count("\n- ") == 1


def test_build_workflow_repair_prompt_is_bounded_and_schema_specific() -> None:
    result = validate_workflow_draft_text(_invalid_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Repair attempt 1/2" in prompt
    assert "latest workflow draft failed appv24 workflow validation" in prompt
    assert "Return a complete corrected workflow package draft" in prompt
    assert "workflow.yaml" in prompt
    assert "connectors.yaml" in prompt
    assert "permissions.yaml" in prompt
    assert "dry_run_fixtures/happy_path.yaml" in prompt
    assert "dry_run_fixtures/rejection_path.yaml" in prompt
    assert "rollback.md" in prompt
    assert "Do not call tools" in prompt
    assert "side_effect must be boolean true or false" in prompt


def test_repair_noops_when_latest_draft_is_valid() -> None:
    messages = [_assistant_message(_valid_draft())]

    def fail_run_turn(prompt: str) -> None:
        raise AssertionError(f"repair turn should not run: {prompt}")

    result = repair_workflow_draft_messages(messages, fail_run_turn)

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 0
    assert not result.repaired
    assert not result.exhausted


def test_repair_runs_once_when_second_draft_is_valid() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_valid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=6),
    )

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 1
    assert result.repaired
    assert not result.exhausted
    assert len(prompts) == 1
    assert prompts == list(result.feedback_prompts)


def test_repair_stops_after_max_attempts_when_invalid_persists() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_invalid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=3),
    )

    assert result.validation is not None
    assert not result.validation.ok
    assert result.attempts == 2
    assert not result.repaired
    assert result.exhausted
    assert len(prompts) == 2
```

- [ ] **Step 2: Run the tests and verify they fail because the module does not exist**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_repair.py -q
```

Expected output contains:

```text
ModuleNotFoundError: No module named 'appv24.workflows.repair'
```

## Task 2: Implement the Green-Zone Repair Helper

**Files:**
- Create: `appV2.4/appv24/workflows/repair.py`
- Test: `appV2.4/tests/test_workflow_repair.py`

- [ ] **Step 1: Create the repair module**

Create `appV2.4/appv24/workflows/repair.py` with this content:

```python
"""Bounded workflow draft repair helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from appv24.workflows.live_validation import assistant_text_from_messages
from appv24.workflows.validator import WorkflowDraftValidationResult, validate_workflow_draft_text


@dataclass(frozen=True)
class WorkflowDraftRepairConfig:
    max_attempts: int = 2
    max_issues: int = 8


@dataclass(frozen=True)
class WorkflowDraftRepairResult:
    validation: WorkflowDraftValidationResult | None
    attempts: int
    repaired: bool
    exhausted: bool
    feedback_prompts: tuple[str, ...] = ()


def validate_latest_workflow_draft(messages: list[object]) -> WorkflowDraftValidationResult | None:
    text = assistant_text_from_messages(messages)
    if not text.strip():
        return None
    return validate_workflow_draft_text(text)


def compact_workflow_validation_issues(result: WorkflowDraftValidationResult, *, max_issues: int = 8) -> str:
    if result.ok:
        return "No validation issues."

    visible_count = max(0, max_issues)
    lines = [
        f"- {issue.code} {issue.location}: {issue.message}"
        for issue in result.validation.issues[:visible_count]
    ]
    remaining = len(result.validation.issues) - visible_count
    if remaining > 0:
        lines.append(f"- {remaining} more issue(s) were omitted from this repair prompt")
    return "\n".join(lines)


def build_workflow_repair_prompt(
    result: WorkflowDraftValidationResult,
    *,
    attempt: int,
    max_attempts: int,
    max_issues: int = 8,
) -> str:
    issues = compact_workflow_validation_issues(result, max_issues=max_issues)
    return "\n".join(
        [
            "The latest workflow draft failed appv24 workflow validation.",
            "",
            f"Repair attempt {attempt}/{max_attempts}.",
            "",
            "Validation issues:",
            issues,
            "",
            "Return a complete corrected workflow package draft with these labeled fenced sections:",
            "- workflow.yaml",
            "- connectors.yaml",
            "- permissions.yaml",
            "- dry_run_fixtures/happy_path.yaml",
            "- dry_run_fixtures/rejection_path.yaml",
            "- rollback.md",
            "",
            "Rules:",
            "- Preserve the user's intended workflow.",
            "- Fix schema and semantic validation issues only.",
            "- Output the full package again, not a patch.",
            "- Do not call tools, APIs, shell commands, or write files.",
            "- For cron-like workflows, use workflow.yaml trigger type schedule.",
            "- workflow.yaml trigger type must be schedule, webhook, manual, or event.",
            "- connectors.yaml connectors[].actions must be a list of action objects.",
            "- connectors.yaml actions[].side_effect must be boolean true or false.",
            "- permissions.yaml approvals[].required_for must be workflow step id strings.",
        ]
    )


def repair_workflow_draft_messages(
    messages: list[object],
    run_turn: Callable[[str], None],
    *,
    config: WorkflowDraftRepairConfig = WorkflowDraftRepairConfig(),
    on_attempt: Callable[[int, int, WorkflowDraftValidationResult], None] | None = None,
) -> WorkflowDraftRepairResult:
    validation = validate_latest_workflow_draft(messages)
    max_attempts = max(0, config.max_attempts)
    max_issues = max(0, config.max_issues)

    if validation is None:
        return WorkflowDraftRepairResult(
            validation=None,
            attempts=0,
            repaired=False,
            exhausted=False,
        )

    if validation.ok:
        return WorkflowDraftRepairResult(
            validation=validation,
            attempts=0,
            repaired=False,
            exhausted=False,
        )

    feedback_prompts: list[str] = []
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        prompt = build_workflow_repair_prompt(
            validation,
            attempt=attempt,
            max_attempts=max_attempts,
            max_issues=max_issues,
        )
        feedback_prompts.append(prompt)
        if on_attempt is not None:
            on_attempt(attempt, max_attempts, validation)
        run_turn(prompt)
        attempts = attempt
        next_validation = validate_latest_workflow_draft(messages)
        if next_validation is None:
            break
        validation = next_validation
        if validation.ok:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=True,
                exhausted=False,
                feedback_prompts=tuple(feedback_prompts),
            )

    return WorkflowDraftRepairResult(
        validation=validation,
        attempts=attempts,
        repaired=False,
        exhausted=not validation.ok,
        feedback_prompts=tuple(feedback_prompts),
    )
```

- [ ] **Step 2: Run the repair tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_repair.py -q
```

Expected output:

```text
6 passed
```

- [ ] **Step 3: Commit the pure helper**

Run:

```bash
git add appV2.4/appv24/workflows/repair.py appV2.4/tests/test_workflow_repair.py
git commit -m "feat(appv24): add workflow draft repair helper"
```

## Task 3: Add CLI Repair Tests

**Files:**
- Modify: `appV2.4/tests/test_cli.py`
- Modify later in Task 4: `appV2.4/appv24/cli.py`

- [ ] **Step 1: Add invalid draft fixture helper**

In `appV2.4/tests/test_cli.py`, add this helper next to `_valid_workflow_draft_text()`:

```python
def _invalid_workflow_draft_text() -> str:
    return _valid_workflow_draft_text().replace("side_effect: true", "side_effect: send")
```

- [ ] **Step 2: Add CLI one-shot repair test**

Append this test to `appV2.4/tests/test_cli.py`:

```python
def test_cli_workflow_profile_repairs_invalid_draft_before_printing(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = Path(cwd)
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            self.run_prompts: list[str] = []
            created["app"] = self

        def run_turn(self, prompt: str):
            self.run_prompts.append(prompt)
            text = _invalid_workflow_draft_text() if len(self.run_prompts) == 1 else _valid_workflow_draft_text()
            self.messages.append(_assistant_message(text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_model_from_env",
        lambda dotenv_path, **kwargs: Model(id="m", name="m", api="faux", provider="faux", base_url=""),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--profile",
            "workflow-designer-lite",
            "design workflow",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert app.run_prompts[0] == "design workflow"
    assert "Repair attempt 1/2" in app.run_prompts[1]
    assert "Workflow draft repair: attempt 1/2" in captured.out
    assert "Workflow draft validation: passed" in captured.out
    assert "side_effect: send" not in captured.out
```

- [ ] **Step 3: Add coding profile no-repair test**

Append this test to `appV2.4/tests/test_cli.py`:

```python
def test_cli_coding_profile_does_not_repair_workflow_draft(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = Path(cwd)
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            self.run_prompts: list[str] = []
            created["app"] = self

        def run_turn(self, prompt: str):
            self.run_prompts.append(prompt)
            self.messages.append(_assistant_message(_invalid_workflow_draft_text()))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_model_from_env",
        lambda dotenv_path, **kwargs: Model(id="m", name="m", api="faux", provider="faux", base_url=""),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--profile",
            "coding",
            "answer normally",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert app.run_prompts == ["answer normally"]
    assert "Workflow draft repair:" not in captured.out
    assert "Workflow draft validation:" not in captured.out
```

- [ ] **Step 4: Run CLI repair tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_cli.py::test_cli_workflow_profile_repairs_invalid_draft_before_printing appV2.4/tests/test_cli.py::test_cli_coding_profile_does_not_repair_workflow_draft -q
```

Expected output includes a failure because CLI prints the invalid first draft before repair or never calls the repair turn.

## Task 4: Integrate Repair in CLI

**Files:**
- Modify: `appV2.4/appv24/cli.py`
- Test: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Add repair imports**

In `appV2.4/appv24/cli.py`, add imports beside existing workflow imports:

```python
from appv24.workflows.repair import (
    WorkflowDraftRepairConfig,
    WorkflowDraftRepairResult,
    repair_workflow_draft_messages,
)
```

- [ ] **Step 2: Replace prompt execution order for one-shot prompts**

Replace this block:

```python
    if prompt:
        app.run_turn(prompt)
        _print_last_assistant(app)
        _print_workflow_validation_if_needed(app, selected_profile.id)
        return 0
```

with:

```python
    if prompt:
        app.run_turn(prompt)
        repair_result = _repair_workflow_draft_if_needed(app, selected_profile.id)
        _print_last_assistant(app)
        _print_workflow_validation_if_needed(app, selected_profile.id, repair_result)
        return 0
```

- [ ] **Step 3: Replace prompt execution order in plain mode**

Replace this block:

```python
        app.run_turn(prompt)
        _print_last_assistant(app)
        _print_workflow_validation_if_needed(app, selected_profile.id)
```

with:

```python
        app.run_turn(prompt)
        repair_result = _repair_workflow_draft_if_needed(app, selected_profile.id)
        _print_last_assistant(app)
        _print_workflow_validation_if_needed(app, selected_profile.id, repair_result)
```

- [ ] **Step 4: Replace workflow validation helper with repair-aware helpers**

Replace `_print_workflow_validation_if_needed` in `appV2.4/appv24/cli.py` with these two helpers:

```python
def _repair_workflow_draft_if_needed(app: CodingApp, profile_id: str) -> WorkflowDraftRepairResult | None:
    if not should_validate_workflow_drafts(profile_id):
        return None

    def on_attempt(attempt: int, max_attempts: int, _validation) -> None:
        print(f"Workflow draft repair: attempt {attempt}/{max_attempts}")

    return repair_workflow_draft_messages(
        app.messages,
        app.run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=8),
        on_attempt=on_attempt,
    )


def _print_workflow_validation_if_needed(
    app: CodingApp,
    profile_id: str,
    repair_result: WorkflowDraftRepairResult | None = None,
) -> None:
    if not should_validate_workflow_drafts(profile_id):
        return

    if repair_result is not None:
        result = repair_result.validation
    else:
        text = assistant_text_from_messages(app.messages)
        result = validate_workflow_draft_text(text) if text.strip() else None

    if result is None:
        return

    print(format_workflow_validation_report(result))
```

- [ ] **Step 5: Run CLI repair tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_cli.py::test_cli_workflow_profile_repairs_invalid_draft_before_printing appV2.4/tests/test_cli.py::test_cli_coding_profile_does_not_repair_workflow_draft -q
```

Expected output:

```text
2 passed
```

- [ ] **Step 6: Run existing CLI workflow tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_cli.py -q
```

Expected output has no failures.

- [ ] **Step 7: Commit CLI integration**

Run:

```bash
git add appV2.4/appv24/cli.py appV2.4/tests/test_cli.py
git commit -m "feat(appv24): repair workflow drafts in cli"
```

## Task 5: Add TUI Repair Tests

**Files:**
- Modify: `appV2.4/tests/test_tui.py`
- Modify later in Task 6: `appV2.4/appv24/tui/interactive_mode.py`

- [ ] **Step 1: Add invalid draft fixture helper**

In `appV2.4/tests/test_tui.py`, add this helper near `_valid_workflow_draft_text()`:

```python
def _invalid_workflow_draft_text() -> str:
    return _valid_workflow_draft_text().replace("side_effect: true", "side_effect: send")
```

- [ ] **Step 2: Add TUI repair rendering test**

Append this test to `appV2.4/tests/test_tui.py`:

```python
def test_interactive_mode_repairs_invalid_workflow_draft_before_validation_report(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        text = _invalid_workflow_draft_text() if calls["n"] == 1 else _valid_workflow_draft_text()
        return text_response_events(model, text)

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    inputs = iter(["design workflow", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))
    assert calls["n"] == 2
    assert "Workflow draft repair: attempt 1/2" in rendered
    assert "Workflow draft validation: passed" in rendered
```

- [ ] **Step 3: Add TUI save-after-repair test**

Append this test to `appV2.4/tests/test_tui.py`:

```python
def test_interactive_mode_workflow_save_uses_repaired_valid_draft(tmp_path) -> None:
    calls = {"n": 0}

    def script(model, context):
        calls["n"] += 1
        text = _invalid_workflow_draft_text() if calls["n"] == 1 else _valid_workflow_draft_text()
        return text_response_events(model, text)

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    inputs = iter(["design workflow", "/workflow-save repaired-workflow", "/exit"])
    holder: dict[str, InteractiveMode] = {}

    def input_fn(prompt: str) -> str:
        command = next(inputs)
        if command.startswith("/workflow-save"):
            assert _wait_until(lambda: holder["mode"]._last_workflow_validation is not None)
        return command

    mode = InteractiveMode(app, input_fn=input_fn)
    holder["mode"] = mode

    assert mode.run() == 0

    saved_path = tmp_path / "repaired-workflow"
    rendered = strip_ansi("\n".join(app.tui.render(160)))
    assert calls["n"] == 2
    assert saved_path.joinpath("workflow.yaml").is_file()
    assert "Workflow draft repair: attempt 1/2" in rendered
    assert "Workflow draft saved:" in rendered
```

- [ ] **Step 4: Add TUI repair exhaustion test**

Append this test to `appV2.4/tests/test_tui.py`:

```python
def test_interactive_mode_reports_failed_validation_after_repair_exhaustion(tmp_path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, _invalid_workflow_draft_text())))
    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    inputs = iter(["design workflow", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))
    assert "Workflow draft repair: attempt 1/2" in rendered
    assert "Workflow draft repair: attempt 2/2" in rendered
    assert "Workflow draft validation: failed" in rendered
    assert mode._last_workflow_validation is None
```

- [ ] **Step 5: Run TUI repair tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_tui.py::test_interactive_mode_repairs_invalid_workflow_draft_before_validation_report appV2.4/tests/test_tui.py::test_interactive_mode_workflow_save_uses_repaired_valid_draft appV2.4/tests/test_tui.py::test_interactive_mode_reports_failed_validation_after_repair_exhaustion -q
```

Expected output includes failures because the TUI currently validates once and does not run repair turns.

## Task 6: Integrate Repair in TUI

**Files:**
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Add repair imports**

In `appV2.4/appv24/tui/interactive_mode.py`, add imports beside existing workflow imports:

```python
from appv24.workflows.repair import (
    WorkflowDraftRepairConfig,
    WorkflowDraftRepairResult,
    repair_workflow_draft_messages,
)
```

- [ ] **Step 2: Make turn finishing repair-aware**

Replace this line in `_finish_turn_thread`:

```python
        self._render_workflow_validation_if_needed()
```

with:

```python
        repair_result = self._repair_workflow_draft_if_needed()
        self._render_workflow_validation_if_needed(repair_result)
```

- [ ] **Step 3: Add repair helper methods**

Add these methods near `_render_workflow_validation_if_needed`:

```python
    def _repair_workflow_draft_if_needed(self) -> WorkflowDraftRepairResult | None:
        if not should_validate_workflow_drafts(self._profile_id()):
            return None

        def on_attempt(attempt: int, max_attempts: int, _validation) -> None:
            self.status.set_message("Repairing workflow draft")
            self.history.add(StatusLine(f"Workflow draft repair: attempt {attempt}/{max_attempts}", kind="compact"))
            self._refresh_footer()
            self.tui.request_render()

        try:
            return repair_workflow_draft_messages(
                self.app.messages,
                self._run_workflow_repair_turn,
                config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=8),
                on_attempt=on_attempt,
            )
        except Exception as error:  # noqa: BLE001 - repair should not crash interactive mode
            self.history.add(StatusLine(f"Workflow draft repair failed: {error}", kind="error"))
            return None

    def _run_workflow_repair_turn(self, prompt: str) -> None:
        self.app.run_turn(
            prompt,
            on_post_response_compaction_start=self._show_post_response_compaction_status,
        )
```

- [ ] **Step 4: Make validation rendering accept repair result**

Replace `_render_workflow_validation_if_needed` with:

```python
    def _render_workflow_validation_if_needed(self, repair_result: WorkflowDraftRepairResult | None = None) -> None:
        if not should_validate_workflow_drafts(self._profile_id()):
            return

        if repair_result is not None:
            result = repair_result.validation
        else:
            text = assistant_text_from_messages(self.app.messages)
            result = validate_workflow_draft_text(text) if text.strip() else None

        if result is None:
            return

        self._last_workflow_validation = result if result.validation.ok else None
        kind = "info" if result.validation.ok else "error"
        self.history.add(StatusLine(format_workflow_validation_report(result), kind=kind))
```

- [ ] **Step 5: Run focused TUI repair tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_tui.py::test_interactive_mode_repairs_invalid_workflow_draft_before_validation_report appV2.4/tests/test_tui.py::test_interactive_mode_workflow_save_uses_repaired_valid_draft appV2.4/tests/test_tui.py::test_interactive_mode_reports_failed_validation_after_repair_exhaustion -q
```

Expected output:

```text
3 passed
```

- [ ] **Step 6: Run existing TUI workflow tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_tui.py -q
```

Expected output has no failures.

- [ ] **Step 7: Commit TUI integration**

Run:

```bash
git add appV2.4/appv24/tui/interactive_mode.py appV2.4/tests/test_tui.py
git commit -m "feat(appv24): repair workflow drafts in tui"
```

## Task 7: Document the Repair Loop

**Files:**
- Modify: `docs/appv24/workflow-package-validator.md`

- [ ] **Step 1: Add live repair loop documentation**

Append this section to `docs/appv24/workflow-package-validator.md`:

```markdown
## Live Draft Repair

When `appv24` runs with `--profile workflow-designer-lite`, the CLI and TUI validate the latest assistant workflow draft after each assistant turn.

If the draft fails validation, appv24 sends a compact validator feedback prompt back through the same `CodingApp.run_turn()` path. The repair loop is bounded to two attempts. The repair prompt includes validator issue codes, issue locations, issue messages, required draft section names, and schema reminders for common mistakes.

The repair loop is passive. It does not execute workflows, call connector APIs, read secrets, run shell commands, provision infrastructure, or write package files. It only asks the model to return a complete corrected draft. The existing `/workflow-save relative/path` command still refuses to save unless the final draft validates.

If both repair attempts fail, appv24 keeps the failed validation report visible and leaves `/workflow-save` blocked for that draft.
```

- [ ] **Step 2: Commit documentation**

Run:

```bash
git add docs/appv24/workflow-package-validator.md
git commit -m "docs(appv24): document workflow draft repair loop"
```

## Task 8: Verification and Boundary Audit

**Files:**
- No code changes in this task.

- [ ] **Step 1: Run focused workflow verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -q
```

Expected output has no failures.

- [ ] **Step 2: Run full appv24 pytest suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected output has no failures.

- [ ] **Step 3: Audit that no appv23/appv231 files changed**

Run:

```bash
git diff --name-only upstream/profile/appv24-built-in-profiles..HEAD | rg '^(appV2\.3|appV2\.3\.1|packages/appv23-cli|packages/appv231-cli)/'
```

Expected output is empty. `rg` exit code `1` is acceptable for no matches.

- [ ] **Step 4: Audit that no frozen appv24 kernel file changed**

Run:

```bash
git diff --name-only upstream/profile/appv24-built-in-profiles..HEAD | rg '^(appV2\.4/appv24/(agent/agent\.py|agent/agent_loop\.py|agent/types\.py|ai/types\.py|ai/stream\.py|ai/validation\.py|compaction/compressor\.py|compaction/timing\.py|coding_agent/session_store\.py|app\.py))$'
```

Expected output is empty. `rg` exit code `1` is acceptable for no matches.

- [ ] **Step 5: Run an offline fake-provider behavior check**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_cli.py::test_cli_workflow_profile_repairs_invalid_draft_before_printing appV2.4/tests/test_tui.py::test_interactive_mode_repairs_invalid_workflow_draft_before_validation_report -q
```

Expected output:

```text
2 passed
```

- [ ] **Step 6: Optional live TUI check after offline tests pass**

Use a temporary cwd so generated workflow drafts do not pollute the repo:

```bash
mkdir -p tmp/appv24_workflow_repair_live
APPV24_MODEL_CATALOG_STARTUP_FETCH=true PYTHONPATH=appV2.4 .venv/bin/python -m appv24.cli --cwd tmp/appv24_workflow_repair_live --dotenv .env --profile workflow-designer-lite --provider openrouter --model xiaomi/mimo-v2.5-pro --thinking off --temperature 0.2 --max-tokens 3500 --timeout-seconds 180
```

Prompt:

```text
Design a passive appv24 workflow package for weekday GitHub dependency alert triage. It should inspect dependency alerts, draft a GitHub issue with human approval, and send a Telegram notification only as a design target. Include rollback notes and dry-run fixtures.
```

Expected behavior:

```text
The TUI renders a workflow draft.
If validation fails, the TUI renders Workflow draft repair: attempt 1/2.
The final validation report is visible.
No files are written unless /workflow-save is used.
```

## Self-Review Checklist

- Spec coverage: The plan adds validator feedback, bounded repair attempts, CLI integration, TUI integration, save protection, docs, and tests.
- Boundary coverage: The plan creates green-zone workflow code and touches yellow-zone CLI/TUI only.
- Frozen-zone coverage: The plan explicitly avoids kernel, provider, compaction, session storage, and app core files.
- Type consistency: `WorkflowDraftRepairConfig`, `WorkflowDraftRepairResult`, `validate_latest_workflow_draft`, `compact_workflow_validation_issues`, `build_workflow_repair_prompt`, and `repair_workflow_draft_messages` are defined before use.
- Validation strategy: Unit tests prove pure repair behavior, CLI tests prove profile-scoped repair, TUI tests prove repair rendering and `/workflow-save` behavior, and full appv24 tests prove no broad regression.
