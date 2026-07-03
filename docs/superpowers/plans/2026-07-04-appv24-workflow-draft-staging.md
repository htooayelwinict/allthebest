# appv24 Workflow Draft Staging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded prompt-chaining to `workflow-designer-lite` so natural-language workflow requests are compiled through explicit intent, skeleton, and package stages before the existing validator/repair/save flow.

**Architecture:** Keep staging profile-local and visible. Add a pure green-zone workflow staging helper that builds stage prompts and runs them through the existing `CodingApp.run_turn()` path, then wire CLI/TUI workflow-profile entrypoints to that helper. Do not change appv24 red-zone kernel files, provider streaming, compaction, session storage, or tool schemas.

**Tech Stack:** Python dataclasses, existing appv24 profile registry, existing `CodingApp.run_turn()`, existing workflow draft validator/repair modules, pytest.

---

## Scope And Boundary Rules

This plan is for appv24 only.

Do not modify these red-zone appv24 files:

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

Yellow-zone files touched by this plan:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
```

Green-zone files touched by this plan:

```text
appV2.4/appv24/workflows/staging.py
appV2.4/appv24/workflows/__init__.py
appV2.4/appv24/profiles/builtins.py
appV2.4/tests/test_workflow_staging.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
appV2.4/tests/test_profiles.py
docs/appv24/workflow-package-validator.md
```

## Design Decision

Use **visible staged compiler turns** for v1:

```text
user workflow request
  -> stage 1: intent brief
  -> stage 2: workflow skeleton
  -> stage 3: full package draft
  -> existing validator
  -> existing repair loop if invalid
  -> existing /workflow-save if valid
```

Do not create hidden/internal turns in v1. `CodingApp.run_turn()` appends prompts and assistant messages to normal session history. Hiding staged prompts would require a new app/session API or renderer/session-event filtering, which would touch red-zone behavior. Visible staged turns are less polished, but they preserve protocol honesty and keep the kernel stable.

The stage prompts must be passive:

```text
no shell
no file writes
no workflow execution
no external APIs
no secrets
no cloud provisioning
```

## File Structure

`appV2.4/appv24/workflows/staging.py`

- Owns pure prompt building and bounded stage execution.
- Depends only on `appv24.workflows.live_validation.should_validate_workflow_drafts`.
- Does not import `CodingApp`, TUI, CLI, providers, compaction, or session internals.

`appV2.4/appv24/cli.py`

- Replaces direct workflow-profile `app.run_turn(prompt)` with staged execution.
- Prints compact stage status lines.
- Keeps existing validation, repair, and save behavior.

`appV2.4/appv24/tui/interactive_mode.py`

- Replaces direct workflow-profile turn execution with staged execution inside the existing turn thread.
- Adds compact status/history lines for stage progress.
- Keeps existing validation, repair, and save behavior.

`appV2.4/appv24/profiles/builtins.py`

- Updates the workflow profile prompt so it does not fight the stage prompts.
- The profile remains read-only and passive.

`docs/appv24/workflow-package-validator.md`

- Documents the stage flow and its safety boundary.

---

### Task 1: Add Pure Workflow Staging Helper

**Files:**
- Create: `appV2.4/appv24/workflows/staging.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: `appV2.4/tests/test_workflow_staging.py`

- [ ] **Step 1: Write the failing staging tests**

Create `appV2.4/tests/test_workflow_staging.py`:

```python
from __future__ import annotations

from appv24.workflows.staging import (
    WorkflowDraftStageConfig,
    build_workflow_stage_prompt,
    run_workflow_draft_stages,
    should_stage_workflow_drafts,
)


def test_should_stage_only_workflow_designer_profile() -> None:
    assert should_stage_workflow_drafts("workflow-designer-lite")
    assert should_stage_workflow_drafts("WORKFLOW_DESIGNER_LITE")
    assert not should_stage_workflow_drafts("coding")
    assert not should_stage_workflow_drafts(None)


def test_build_intent_stage_prompt_is_passive_and_preserves_request() -> None:
    prompt = build_workflow_stage_prompt(
        "Every weekday, check GitHub alerts and draft a Telegram summary.",
        stage_id="intent",
        stage_index=1,
        total_stages=3,
    )

    assert "Workflow draft staging 1/3: intent" in prompt
    assert "Every weekday, check GitHub alerts and draft a Telegram summary." in prompt
    assert "Do not call tools" in prompt
    assert "Do not call external APIs" in prompt
    assert "Do not write files" in prompt
    assert "Return only an intent brief" in prompt
    assert "workflow.yaml" not in prompt


def test_build_package_stage_prompt_requires_full_package_sections() -> None:
    prompt = build_workflow_stage_prompt(
        "Create a human-approved dependency triage workflow.",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert "Workflow draft staging 3/3: package" in prompt
    assert "Return the complete workflow package draft" in prompt
    assert "```yaml workflow.yaml" in prompt
    assert "```yaml connectors.yaml" in prompt
    assert "```yaml permissions.yaml" in prompt
    assert "```yaml dry_run_fixtures/happy_path.yaml" in prompt
    assert "```yaml dry_run_fixtures/rejection_path.yaml" in prompt
    assert "```markdown rollback.md" in prompt


def test_run_workflow_draft_stages_runs_default_stages_in_order() -> None:
    prompts: list[str] = []
    events: list[tuple[int, int, str]] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)

    result = run_workflow_draft_stages(
        "Build a weekly dependency alert workflow.",
        run_turn,
        on_stage=lambda index, total, stage_id: events.append((index, total, stage_id)),
    )

    assert result.staged
    assert result.stage_ids == ("intent", "skeleton", "package")
    assert result.prompts == tuple(prompts)
    assert events == [(1, 3, "intent"), (2, 3, "skeleton"), (3, 3, "package")]
    assert "intent brief" in prompts[0]
    assert "workflow skeleton" in prompts[1]
    assert "complete workflow package draft" in prompts[2]


def test_run_workflow_draft_stages_noops_when_disabled() -> None:
    prompts: list[str] = []

    result = run_workflow_draft_stages(
        "Build a workflow.",
        lambda prompt: prompts.append(prompt),
        config=WorkflowDraftStageConfig(enabled=False),
    )

    assert not result.staged
    assert result.stage_ids == ()
    assert result.prompts == ()
    assert prompts == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_staging.py -q
```

Expected: FAIL because `appv24.workflows.staging` does not exist.

- [ ] **Step 3: Add the staging helper**

Create `appV2.4/appv24/workflows/staging.py`:

```python
"""Bounded workflow draft staging helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from appv24.workflows.live_validation import should_validate_workflow_drafts


DEFAULT_WORKFLOW_STAGE_IDS = ("intent", "skeleton", "package")


@dataclass(frozen=True)
class WorkflowDraftStageConfig:
    enabled: bool = True
    stage_ids: tuple[str, ...] = DEFAULT_WORKFLOW_STAGE_IDS


@dataclass(frozen=True)
class WorkflowDraftStageResult:
    staged: bool
    stage_ids: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()


def should_stage_workflow_drafts(profile_id: str | None) -> bool:
    return should_validate_workflow_drafts(profile_id)


def build_workflow_stage_prompt(
    user_request: str,
    *,
    stage_id: str,
    stage_index: int,
    total_stages: int,
) -> str:
    normalized_stage = stage_id.strip().lower().replace("_", "-")
    header = [
        f"Workflow draft staging {stage_index}/{total_stages}: {normalized_stage}.",
        "",
        "Original user request:",
        user_request.strip(),
        "",
        "Passive safety rules:",
        "- Do not call tools.",
        "- Do not call external APIs.",
        "- Do not run shell commands.",
        "- Do not write files.",
        "- Do not request raw secrets.",
        "- Do not execute, publish, provision, send, or mutate anything.",
        "- Use placeholders such as ${secrets.NAME} for secret references.",
        "",
    ]

    if normalized_stage == "intent":
        body = [
            "Return only an intent brief with these headings:",
            "- Goal",
            "- Trigger",
            "- Systems involved",
            "- Data needed",
            "- Side effects",
            "- Approval gates",
            "- Safety assumptions",
            "",
            "Do not return workflow package files in this stage.",
        ]
    elif normalized_stage == "skeleton":
        body = [
            "Using the original request and the previous intent brief, return only a workflow skeleton.",
            "Include these headings:",
            "- Trigger",
            "- Step list",
            "- Decision points",
            "- Connector candidates",
            "- Permission candidates",
            "- Required dry-run fixtures",
            "- Rollback notes",
            "",
            "Do not return workflow package files in this stage.",
        ]
    elif normalized_stage == "package":
        body = [
            "Using the original request, previous intent brief, and previous workflow skeleton,",
            "return the complete workflow package draft.",
            "",
            "Use these exact labeled fenced sections:",
            "- ```yaml workflow.yaml",
            "- ```yaml connectors.yaml",
            "- ```yaml permissions.yaml",
            "- ```yaml dry_run_fixtures/happy_path.yaml",
            "- ```yaml dry_run_fixtures/rejection_path.yaml",
            "- ```markdown rollback.md",
            "",
            "Schema reminders:",
            "- workflow.yaml version must be appv24.workflow/v1.",
            "- workflow.yaml triggers must be a list.",
            "- Cron-like triggers must use type: schedule and schedule: <cron>.",
            "- workflow.yaml steps must use approval: <approval-id> for mutating side effects.",
            "- connectors.yaml version must be appv24.connectors/v1.",
            "- connectors.yaml connectors must be a list.",
            "- connectors.yaml actions must be objects with id and side_effect boolean.",
            "- permissions.yaml version must be appv24.permissions/v1.",
            "- permissions.yaml permissions must be a list.",
            "- permissions.yaml permissions[].actions must be connector action id strings.",
            "- permissions.yaml approvals[].required_for must be workflow step id strings.",
            "- Every mutating step must have an explicit approval gate.",
        ]
    else:
        body = [
            "Return a concise workflow design note for this custom stage.",
            "Do not return workflow package files unless the stage name is package.",
        ]

    return "\n".join((*header, *body))


def run_workflow_draft_stages(
    user_request: str,
    run_turn: Callable[[str], None],
    *,
    config: WorkflowDraftStageConfig = WorkflowDraftStageConfig(),
    on_stage: Callable[[int, int, str], None] | None = None,
) -> WorkflowDraftStageResult:
    if not config.enabled:
        return WorkflowDraftStageResult(staged=False)

    stage_ids = tuple(stage_id.strip().lower().replace("_", "-") for stage_id in config.stage_ids if stage_id.strip())
    if not stage_ids:
        return WorkflowDraftStageResult(staged=False)

    prompts: list[str] = []
    total = len(stage_ids)
    for index, stage_id in enumerate(stage_ids, start=1):
        prompt = build_workflow_stage_prompt(
            user_request,
            stage_id=stage_id,
            stage_index=index,
            total_stages=total,
        )
        prompts.append(prompt)
        if on_stage is not None:
            on_stage(index, total, stage_id)
        run_turn(prompt)

    return WorkflowDraftStageResult(
        staged=True,
        stage_ids=stage_ids,
        prompts=tuple(prompts),
    )
```

- [ ] **Step 4: Export staging helpers**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.staging import (
    WorkflowDraftStageConfig,
    WorkflowDraftStageResult,
    build_workflow_stage_prompt,
    run_workflow_draft_stages,
    should_stage_workflow_drafts,
)
```

Add these names to `__all__`:

```python
    "WorkflowDraftStageConfig",
    "WorkflowDraftStageResult",
    "build_workflow_stage_prompt",
    "run_workflow_draft_stages",
    "should_stage_workflow_drafts",
```

- [ ] **Step 5: Run staging tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_staging.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appV2.4/appv24/workflows/staging.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_staging.py
git commit -m "feat(appv24): add workflow draft staging helper"
```

---

### Task 2: Wire Staging Into CLI Workflow Profile

**Files:**
- Modify: `appV2.4/appv24/cli.py`
- Test: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Add failing CLI tests**

Append to `appV2.4/tests/test_cli.py` near the existing workflow profile tests:

```python
def test_cli_workflow_profile_stages_prompt_before_validation(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            text = _valid_workflow_draft_text() if "staging 3/3: package" in prompt else "stage note"
            self.messages.append(_assistant_message(text))

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

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "design workflow"])

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert len(app.run_prompts) == 3
    assert "Workflow draft staging 1/3: intent" in app.run_prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in app.run_prompts[1]
    assert "Workflow draft staging 3/3: package" in app.run_prompts[2]
    assert "design workflow" in app.run_prompts[0]
    assert "Workflow draft stage: intent 1/3" in captured.out
    assert "Workflow draft stage: skeleton 2/3" in captured.out
    assert "Workflow draft stage: package 3/3" in captured.out
    assert "Workflow draft validation: passed" in captured.out


def test_cli_coding_profile_does_not_stage_prompt(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            self.messages.append(_assistant_message(_valid_workflow_draft_text()))

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

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "coding", "design workflow"])

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert app.run_prompts == ["design workflow"]
    assert "Workflow draft stage:" not in captured.out
    assert "Workflow draft validation:" not in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_cli.py::test_cli_workflow_profile_stages_prompt_before_validation appV2.4/tests/test_cli.py::test_cli_coding_profile_does_not_stage_prompt -q
```

Expected: FAIL because CLI still sends the raw prompt once for the workflow profile.

- [ ] **Step 3: Import staging helper in CLI**

Modify the workflow imports near the top of `appV2.4/appv24/cli.py`:

```python
from appv24.workflows.staging import run_workflow_draft_stages, should_stage_workflow_drafts
```

- [ ] **Step 4: Add CLI staging helper**

Add this helper near `_repair_workflow_draft_if_needed`:

```python
def _run_prompt_for_profile(app: CodingApp, profile_id: str, prompt: str) -> None:
    if not should_stage_workflow_drafts(profile_id):
        app.run_turn(prompt)
        return

    def on_stage(index: int, total: int, stage_id: str) -> None:
        print(f"Workflow draft stage: {stage_id} {index}/{total}")

    run_workflow_draft_stages(prompt, app.run_turn, on_stage=on_stage)
```

- [ ] **Step 5: Use the CLI staging helper**

In prompt mode, replace:

```python
        app.run_turn(prompt)
```

with:

```python
        _run_prompt_for_profile(app, selected_profile.id, prompt)
```

In plain input loop, replace:

```python
        app.run_turn(prompt)
```

with:

```python
        _run_prompt_for_profile(app, selected_profile.id, prompt)
```

- [ ] **Step 6: Run CLI staging tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_cli.py::test_cli_workflow_profile_stages_prompt_before_validation appV2.4/tests/test_cli.py::test_cli_coding_profile_does_not_stage_prompt appV2.4/tests/test_cli.py::test_cli_workflow_profile_repairs_invalid_draft_before_printing appV2.4/tests/test_cli.py::test_cli_plain_workflow_save_writes_last_valid_draft -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add appV2.4/appv24/cli.py appV2.4/tests/test_cli.py
git commit -m "feat(appv24): stage workflow drafts in cli"
```

---

### Task 3: Wire Staging Into TUI Workflow Profile

**Files:**
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Add failing TUI test**

Append to `appV2.4/tests/test_tui.py` near the existing workflow validation tests:

```python
def test_interactive_mode_workflow_profile_stages_prompt_before_validation(tmp_path) -> None:
    calls = {"n": 0}
    prompts: list[str] = []

    def script(model, context):
        calls["n"] += 1
        user_messages = [message for message in context.messages if getattr(message, "role", None) == "user"]
        prompts.append(user_messages[-1].content[0].text)
        text = _valid_workflow_draft_text() if calls["n"] == 3 else "stage note"
        return text_response_events(model, text)

    register_api_provider(create_faux_provider(script))
    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    inputs = iter(["design workflow", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))
    assert calls["n"] == 3
    assert "Workflow draft staging 1/3: intent" in prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in prompts[1]
    assert "Workflow draft staging 3/3: package" in prompts[2]
    assert "design workflow" in prompts[0]
    assert "Workflow draft stage: intent 1/3" in rendered
    assert "Workflow draft stage: skeleton 2/3" in rendered
    assert "Workflow draft stage: package 3/3" in rendered
    assert "Workflow draft validation: passed" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_tui.py::test_interactive_mode_workflow_profile_stages_prompt_before_validation -q
```

Expected: FAIL because TUI still sends the raw prompt once.

- [ ] **Step 3: Import staging helper in TUI**

Modify imports in `appV2.4/appv24/tui/interactive_mode.py`:

```python
from appv24.workflows.staging import run_workflow_draft_stages, should_stage_workflow_drafts
```

- [ ] **Step 4: Add TUI staging helper**

Add this method near `_run_turn_thread`:

```python
    def _run_prompt_for_profile(self, prompt: str) -> None:
        if not should_stage_workflow_drafts(self._profile_id()):
            self.app.run_turn(
                prompt,
                on_post_response_compaction_start=self._show_post_response_compaction_status,
            )
            return

        def on_stage(index: int, total: int, stage_id: str) -> None:
            self.status.set_message("Staging workflow draft")
            self.history.add(StatusLine(f"Workflow draft stage: {stage_id} {index}/{total}", kind="compact"))
            self.tui.request_render()

        run_workflow_draft_stages(
            prompt,
            lambda stage_prompt: self.app.run_turn(
                stage_prompt,
                on_post_response_compaction_start=self._show_post_response_compaction_status,
            ),
            on_stage=on_stage,
        )
```

- [ ] **Step 5: Use the TUI staging helper**

In `_run_turn_thread`, replace:

```python
            self.app.run_turn(
                prompt,
                on_post_response_compaction_start=self._show_post_response_compaction_status,
            )
```

with:

```python
            self._run_prompt_for_profile(prompt)
```

- [ ] **Step 6: Run TUI staging and regression tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_tui.py::test_interactive_mode_workflow_profile_stages_prompt_before_validation appV2.4/tests/test_tui.py::test_interactive_mode_repairs_invalid_workflow_draft_before_validation_report appV2.4/tests/test_tui.py::test_interactive_mode_workflow_save_uses_repaired_valid_draft appV2.4/tests/test_tui.py::test_interactive_mode_coding_profile_does_not_render_workflow_validation -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add appV2.4/appv24/tui/interactive_mode.py appV2.4/tests/test_tui.py
git commit -m "feat(appv24): stage workflow drafts in tui"
```

---

### Task 4: Align Workflow Profile Prompt With Staging

**Files:**
- Modify: `appV2.4/appv24/profiles/builtins.py`
- Test: `appV2.4/tests/test_profiles.py`

- [ ] **Step 1: Add failing profile prompt assertions**

Modify `test_workflow_designer_lite_profile_is_passive_and_read_only` in `appV2.4/tests/test_profiles.py`:

```python
    assert "appv24 may ask for staged workflow compiler passes" in prompt
    assert "Only the package stage should emit labeled fenced code blocks" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_profiles.py::test_workflow_designer_lite_profile_is_passive_and_read_only -q
```

Expected: FAIL because the profile prompt does not mention staged compiler passes yet.

- [ ] **Step 3: Update workflow profile prompt**

In `_WORKFLOW_DESIGNER_LITE_APPEND` in `appV2.4/appv24/profiles/builtins.py`, insert this text after `Produce the workflow design in the final response only.`:

```python
    "appv24 may ask for staged workflow compiler passes. "
    "When a stage prompt asks for intent or skeleton, answer only that stage. "
    "Only the package stage should emit labeled fenced code blocks. "
```

Do not remove the passive safety lines:

```python
    "Do not edit, write, delete, move, or create files. "
    "Do not run shell commands. "
    "Do not run workflows. "
    "Do not call external APIs. "
```

- [ ] **Step 4: Run profile tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_profiles.py::test_workflow_designer_lite_profile_is_passive_and_read_only appV2.4/tests/test_profiles.py::test_workflow_designer_lite_prompt_pins_schema_shapes -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appV2.4/appv24/profiles/builtins.py appV2.4/tests/test_profiles.py
git commit -m "fix(appv24): align workflow profile prompt with staging"
```

---

### Task 5: Document Workflow Draft Staging

**Files:**
- Modify: `docs/appv24/workflow-package-validator.md`

- [ ] **Step 1: Add documentation section**

Append this section after `## Live Draft Validation` and before `## Live Draft Repair`:

````markdown
## Live Draft Staging

When appv24 runs with `--profile workflow-designer-lite`, user workflow requests are compiled through visible staged turns before validation:

```text
intent -> skeleton -> package -> validation -> repair if needed
```

The staging loop is passive. It does not execute workflows, call connector APIs, read secrets, run shell commands, provision infrastructure, send messages, or write package files.

The first two stages produce review notes only. The package stage must return the labeled workflow package blocks that the draft validator already understands.

Staging uses normal `CodingApp.run_turn()` calls. This means stage prompts and assistant responses are visible in the session transcript. appv24 does not create hidden internal workflow turns in this version.
````

- [ ] **Step 2: Commit**

```bash
git add docs/appv24/workflow-package-validator.md
git commit -m "docs(appv24): document workflow draft staging"
```

---

### Task 6: Focused Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run focused staging/profile/CLI/TUI tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_profiles.py appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full appv24 test suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected: PASS.

- [ ] **Step 3: Confirm branch boundaries**

Run:

```bash
git diff --name-only upstream/profile/appv24-built-in-profiles...HEAD | rg '^(appV2\.3|appV2\.3\.1|packages/appv23|packages/appv231|appV2\.4/appv24/agent/|appV2\.4/appv24/ai/(types|stream|validation)\.py|appV2\.4/appv24/compaction/|appV2\.4/appv24/coding_agent/session_store\.py|appV2\.4/appv24/app\.py)' || true
```

Expected: no output.

- [ ] **Step 4: Commit verification notes only if a doc file was updated**

Do not create a verification-only commit unless the implementation updates release notes or docs. Keep command results for the final report.

---

### Task 7: Live TUI Verification

**Files:**
- No code changes unless a verified bug is found.

- [ ] **Step 1: Prepare a temp cwd**

Run:

```bash
mkdir -p tmp/appv24_workflow_staging_live
```

- [ ] **Step 2: Run live TUI with a budget model**

Run:

```bash
APPV24_WORKER_LLM_ENABLED=true APPV24_MODEL_CATALOG_STARTUP_FETCH=true PYTHONPATH=appV2.4 .venv/bin/python -m appv24.cli --cwd tmp/appv24_workflow_staging_live --dotenv .env --profile workflow-designer-lite --provider openrouter --model qwen/qwen3-coder-next --thinking off --temperature 0.2 --max-tokens 4500 --timeout-seconds 180
```

Use this scenario:

```text
Design a workflow that runs every weekday at 09:00, checks GitHub Dependabot alerts for high and critical dependency issues, creates a human-approved GitHub issue for new critical alerts, drafts a Telegram notification as a design target only, stores no raw secrets, includes dry-run fixtures for approval and rejection, and includes rollback notes. Do not execute anything.
```

Expected TUI behavior:

```text
Workflow draft stage: intent 1/3
Workflow draft stage: skeleton 2/3
Workflow draft stage: package 3/3
Workflow draft validation: passed
```

If validation fails once and repair succeeds, acceptable behavior:

```text
Workflow draft repair: attempt 1/2
Workflow draft validation: passed
```

- [ ] **Step 3: Save the validated draft**

In the TUI, run:

```text
/workflow-save staged-qwen-workflow
```

Expected:

```text
Workflow draft saved: <cwd>/staged-qwen-workflow
Workflow package validation: passed
```

- [ ] **Step 4: Validate saved package from disk**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python - <<'PY'
from appv24.workflows import validate_workflow_package
r = validate_workflow_package("tmp/appv24_workflow_staging_live/staged-qwen-workflow")
print("ok=", r.ok)
print("issues=", len(r.issues))
for issue in r.issues:
    print(issue)
PY
```

Expected:

```text
ok= True
issues= 0
```

---

## Self-Review

Spec coverage:

- Prompt chaining is implemented as bounded workflow staging: Task 1.
- Existing validator/repair/save flow remains active after the package stage: Tasks 2 and 3.
- Red-zone files are not touched: Scope and Task 6 boundary check.
- Yellow-zone CLI/TUI changes have focused tests: Tasks 2 and 3.
- Profile prompt is aligned with staged behavior: Task 4.
- Docs describe passive visible staging: Task 5.
- Live TUI verification is included: Task 7.

Placeholder scan:

- No `TBD`, `TODO`, or open-ended "add validation" steps remain.
- Every code-changing step names exact files and includes exact snippets.

Type consistency:

- `WorkflowDraftStageConfig`, `WorkflowDraftStageResult`, `build_workflow_stage_prompt`, `run_workflow_draft_stages`, and `should_stage_workflow_drafts` are defined in Task 1 before CLI/TUI use them.
- CLI and TUI helpers both call the same pure staging helper.
- Existing `WorkflowDraftRepairResult` and validation helpers remain unchanged.

Risk notes:

- Intermediate stage prompts/responses are visible because they use normal `CodingApp.run_turn()` calls. This is intentional for v1.
- This increases model calls from one to three before repair. Use budget models and low temperature for live workflow-profile testing.
- If user experience suffers from visible stage prompts, do not hide them with renderer hacks. Design a separate kernel-reviewed internal-turn API later.
