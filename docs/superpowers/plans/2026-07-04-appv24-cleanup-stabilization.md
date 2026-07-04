# appv24 Cleanup Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize appv24 workflow-designer-lite before workflow enablement by fixing docs drift, centralizing workflow prompt fragments, documenting configurable repair bounds, and verifying profile/save isolation without touching red-zone kernel files.

**Architecture:** Keep all workflow-specific behavior in the appv24 profile/workflow/TUI/CLI layer. Extract duplicated prompt/schema reminder text into `appV2.4/appv24/workflows/prompt_fragments.py`, then have profile, staging, and repair prompts consume those helpers. Use offline tests to prove the workflow profile stays passive and profile-scoped.

**Tech Stack:** Python 3.13, pytest, appv24 built-in profiles, appv24 CLI/TUI, workflow draft validator, PyYAML/jsonschema-backed workflow package validation.

---

## Boundary Rules

Do not modify these paths:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
appV2.3/
appV2.3.1/
appV2.2/
pi/
hermes-agent/
```

Allowed implementation files:

```text
appV2.4/appv24/workflows/prompt_fragments.py
appV2.4/appv24/workflows/staging.py
appV2.4/appv24/workflows/repair.py
appV2.4/appv24/profiles/builtins.py
appV2.4/appv24/cli.py only if profile/save enforcement needs a code fix
appV2.4/appv24/tui/interactive_mode.py only if profile/save enforcement needs a code fix
appV2.4/tests/test_profiles.py
appV2.4/tests/test_workflow_staging.py
appV2.4/tests/test_workflow_repair.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
docs/appv24/workflow-package-validator.md
appV2.4/README.md
```

---

## Task 1: Baseline Boundary And Drift Audit

**Files:**
- Read: `docs/superpowers/specs/2026-07-04-appv24-cleanup-stabilization-design.md`
- Read: `rules.md`
- Read: `appV2.4/README.md`
- Read: `docs/appv24/workflow-package-validator.md`
- Read: `appV2.4/appv24/workflows/staging.py`
- Read: `appV2.4/appv24/workflows/repair.py`
- Read: `appV2.4/appv24/profiles/builtins.py`

- [ ] **Step 1: Confirm worktree and current branch**

Run:

```bash
git status --short --branch
```

Expected:

```text
## profile/appv24-built-in-profiles...upstream/profile/appv24-built-in-profiles [ahead N]
```

No modified files should appear before implementation begins.

- [ ] **Step 2: Confirm red-zone paths are not already dirty**

Run:

```bash
git diff --name-only | rg '^(appV2\.4/appv24/(agent|compaction)/|appV2\.4/appv24/ai/(stream|validation)\.py|appV2\.4/appv24/coding_agent/session_store\.py|appV2\.4/appv24/app\.py|appV2\.3/|appV2\.3\.1/|appV2\.2/|pi/|hermes-agent/)' || true
```

Expected: no output.

- [ ] **Step 3: Identify current docs drift**

Run:

```bash
rg -n 'next/appv23\.1|appv23\.tu|two attempts|bounded to two attempts|both repair attempts' appV2.4/README.md docs/appv24/workflow-package-validator.md
```

Expected current drift before fixes:

```text
appV2.4/README.md:... next/appv23.1
appV2.4/README.md:... appv23.tu
docs/appv24/workflow-package-validator.md:... bounded to two attempts
docs/appv24/workflow-package-validator.md:... both repair attempts
```

- [ ] **Step 4: Do not commit**

This task is audit-only. Do not edit files yet.

---

## Task 2: Fix README And Validator Documentation Drift

**Files:**
- Modify: `appV2.4/README.md`
- Modify: `docs/appv24/workflow-package-validator.md`

- [ ] **Step 1: Write a focused docs drift check**

Run:

```bash
rg -n 'next/appv23\.1|appv23\.tu|bounded to two attempts|both repair attempts' appV2.4/README.md docs/appv24/workflow-package-validator.md
```

Expected before implementation: at least one match.

- [ ] **Step 2: Update `appV2.4/README.md` appv24 identity drift**

Edit only appv24-specific drift:

```text
appv23.tu -> appv24
next/appv23.1 -> next/appv24
```

Do not rewrite the README broadly. Do not touch root `README.md` in this task.

- [ ] **Step 3: Update workflow repair docs**

In `docs/appv24/workflow-package-validator.md`, replace the fixed two-attempt wording in “Live Draft Repair” with this behavior:

```markdown
If the draft fails validation, appv24 sends a compact validator feedback prompt back through the same `CodingApp.run_turn()` path. Repair attempts are configurable. The default runtime should remain finite and cost-safe, while workflow-specific tests may configure a larger or effectively unbounded loop to prove convergence. The repair prompt includes validator issue codes, issue locations, issue messages, required draft section names, and schema reminders for common mistakes.

The repair loop is passive. It does not execute workflows, call connector APIs, read secrets, run shell commands, provision infrastructure, or write package files. It only asks the model to return a complete corrected draft. The existing `/workflow-save relative/path` command still refuses to save unless the final draft validates.

If repair stops because the configured attempt bound, timeout, abort signal, or provider error is reached, appv24 keeps the failed validation report visible and leaves `/workflow-save` blocked for that draft.
```

- [ ] **Step 4: Verify docs drift is gone**

Run:

```bash
rg -n 'next/appv23\.1|appv23\.tu|bounded to two attempts|both repair attempts' appV2.4/README.md docs/appv24/workflow-package-validator.md
```

Expected: no output.

- [ ] **Step 5: Commit docs drift fix**

Run:

```bash
git add appV2.4/README.md docs/appv24/workflow-package-validator.md
git commit -m "docs(appv24): fix cleanup drift"
```

---

## Task 3: Add Shared Workflow Prompt Fragments

**Files:**
- Create: `appV2.4/appv24/workflows/prompt_fragments.py`
- Modify: `appV2.4/tests/test_workflow_staging.py`
- Modify: `appV2.4/tests/test_workflow_repair.py`
- Modify: `appV2.4/tests/test_profiles.py`

- [ ] **Step 1: Add failing tests for fragment helper content**

Append focused tests.

In `appV2.4/tests/test_workflow_staging.py` add:

```python
from appv24.workflows.prompt_fragments import (
    render_required_package_blocks_fragment,
    render_schema_reminders_fragment,
)


def test_prompt_fragments_render_required_package_blocks() -> None:
    fragment = render_required_package_blocks_fragment()

    assert "```yaml workflow.yaml" in fragment
    assert "```yaml connectors.yaml" in fragment
    assert "```yaml permissions.yaml" in fragment
    assert "```yaml dry_run_fixtures/happy_path.yaml" in fragment
    assert "```yaml dry_run_fixtures/rejection_path.yaml" in fragment
    assert "```markdown rollback.md" in fragment


def test_prompt_fragments_render_schema_reminders() -> None:
    fragment = render_schema_reminders_fragment()

    assert "workflow.yaml version must be appv24.workflow/v1" in fragment
    assert "connectors.yaml connectors must be a list" in fragment
    assert "permissions.yaml permissions must be a list" in fragment
    assert "permissions.yaml approvals[].required_for must be workflow step id strings" in fragment
```

In `appV2.4/tests/test_workflow_repair.py` add:

```python
from appv24.workflows.prompt_fragments import (
    render_passive_safety_fragment,
    render_secret_reference_fragment,
    render_side_effect_contract_fragment,
)


def test_prompt_fragments_render_repair_safety_rules() -> None:
    safety = render_passive_safety_fragment()
    side_effects = render_side_effect_contract_fragment()
    secrets = render_secret_reference_fragment()

    assert "Do not call tools" in safety
    assert "Do not run shell commands" in safety
    assert "Brokered mutating actions require approval_required: true" in side_effects
    assert "Design-only actions must use execution_mode: design_only" in side_effects
    assert "secretRef values must be reference names only" in secrets
```

In `appV2.4/tests/test_profiles.py` add:

```python
from appv24.workflows.prompt_fragments import render_required_package_blocks_fragment


def test_workflow_profile_uses_shared_required_block_fragment() -> None:
    prompt = profile_coding_app_kwargs(get_profile("workflow-designer-lite"))["append_system_prompt"]
    fragment = render_required_package_blocks_fragment()

    for label in (
        "```yaml workflow.yaml",
        "```yaml connectors.yaml",
        "```yaml permissions.yaml",
        "```yaml dry_run_fixtures/happy_path.yaml",
        "```yaml dry_run_fixtures/rejection_path.yaml",
        "```markdown rollback.md",
    ):
        assert label in fragment
        assert label in prompt
```

- [ ] **Step 2: Run tests and confirm import failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_profiles.py::test_workflow_profile_uses_shared_required_block_fragment \
  appV2.4/tests/test_workflow_staging.py::test_prompt_fragments_render_required_package_blocks \
  appV2.4/tests/test_workflow_staging.py::test_prompt_fragments_render_schema_reminders \
  appV2.4/tests/test_workflow_repair.py::test_prompt_fragments_render_repair_safety_rules -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'appv24.workflows.prompt_fragments'`.

- [ ] **Step 3: Create `prompt_fragments.py`**

Create `appV2.4/appv24/workflows/prompt_fragments.py`:

```python
"""Shared prompt fragments for appv24 workflow package authoring."""

from __future__ import annotations


REQUIRED_PACKAGE_BLOCK_LABELS = (
    "```yaml workflow.yaml",
    "```yaml connectors.yaml",
    "```yaml permissions.yaml",
    "```yaml dry_run_fixtures/happy_path.yaml",
    "```yaml dry_run_fixtures/rejection_path.yaml",
    "```markdown rollback.md",
)


def render_required_package_blocks_fragment() -> str:
    lines = ["Use these exact labeled fenced sections:"]
    lines.extend(f"- {label}" for label in REQUIRED_PACKAGE_BLOCK_LABELS)
    return "\n".join(lines)


def render_passive_safety_fragment() -> str:
    return "\n".join(
        [
            "- Do not call tools.",
            "- Do not call external APIs.",
            "- Do not run shell commands.",
            "- Do not write files.",
            "- Do not request raw secrets.",
            "- Do not execute, publish, provision, send, or mutate anything.",
        ]
    )


def render_schema_reminders_fragment() -> str:
    return "\n".join(
        [
            "- workflow.yaml version must be appv24.workflow/v1.",
            "- workflow.yaml must include design metadata with intent, trigger, inputs, outputs, assumptions, side_effects, approval_gates, failure_modes, and rollback.",
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
        ]
    )


def render_side_effect_contract_fragment() -> str:
    return "\n".join(
        [
            "- Read, classify, analyze, summarize, draft, format, and local artifact steps must use side_effect: false unless they mutate external systems.",
            "- Only external create, update, delete, send, publish, or provision actions should be mutating.",
            "- Mutating connector actions must include side_effect_class, execution_mode, approval_required, and dry_run_supported.",
            "- Brokered mutating actions require approval_required: true and dry_run_supported: true.",
            "- Brokered create/send/publish/provision actions require idempotency.key_fields.",
            "- Brokered mutating actions require outputs.allowed with explicit field names and no wildcard.",
            "- Design-only actions must use execution_mode: design_only and must not be called by workflow steps.",
            "- Draft-only outputs must be represented as local artifact drafts, not connector send/publish calls.",
            "- Notification, email, chat, message, or report drafts stored locally are local artifact drafts with side_effect: false.",
            "- Every mutating step must have an explicit approval gate.",
            "- The approval id in steps[].approval must exist in permissions.yaml approvals[].id.",
            "- approvals[].required_for must include the exact mutating workflow step id.",
        ]
    )


def render_secret_reference_fragment() -> str:
    return "\n".join(
        [
            "- Secret examples must use reference names such as secretRef: CONNECTOR_CREDENTIAL_REF, checksum fields, redacted: true, or non-secret references.",
            '- Prefer normal input keys with scalar secret reference values, for example github_token: "secretRef: GITHUB_TOKEN_REF".',
            "- Do not invent keys like github_token_secretRef: GITHUB_TOKEN.",
            "- secretRef values must be reference names only, never raw tokens, passwords, keys, or credentials.",
        ]
    )


def render_rollback_safety_fragment() -> str:
    return "\n".join(
        [
            "- rollback.md must be manual, non-executable rollback guidance.",
            "- Do not include API, CLI, shell, curl, Terraform, kubectl, cloud CLI, or token examples in rollback.md.",
            "- rollback.md must not include ${secrets.*}, Authorization headers, HTTP method examples, or copy-paste commands.",
        ]
    )


def render_workflow_passive_boundary_fragment() -> str:
    return "- appv24 only designs workflow packages; it does not execute workflows."
```

- [ ] **Step 4: Run fragment tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_profiles.py::test_workflow_profile_uses_shared_required_block_fragment \
  appV2.4/tests/test_workflow_staging.py::test_prompt_fragments_render_required_package_blocks \
  appV2.4/tests/test_workflow_staging.py::test_prompt_fragments_render_schema_reminders \
  appV2.4/tests/test_workflow_repair.py::test_prompt_fragments_render_repair_safety_rules -q
```

Expected: PASS.

- [ ] **Step 5: Commit fragment module and tests**

Run:

```bash
git add appV2.4/appv24/workflows/prompt_fragments.py appV2.4/tests/test_profiles.py appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_repair.py
git commit -m "test(appv24): cover workflow prompt fragments"
```

---

## Task 4: Refactor Staging, Repair, And Profile Prompts To Use Fragments

**Files:**
- Modify: `appV2.4/appv24/workflows/staging.py`
- Modify: `appV2.4/appv24/workflows/repair.py`
- Modify: `appV2.4/appv24/profiles/builtins.py`
- Modify: `appV2.4/tests/test_workflow_staging.py`
- Modify: `appV2.4/tests/test_workflow_repair.py`
- Modify: `appV2.4/tests/test_profiles.py`

- [ ] **Step 1: Add tests that generated prompts include shared fragment text**

In `appV2.4/tests/test_workflow_staging.py`, add:

```python
def test_package_stage_prompt_includes_shared_fragments() -> None:
    prompt = build_workflow_stage_prompt(
        "design a workflow",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert render_required_package_blocks_fragment() in prompt
    assert render_schema_reminders_fragment() in prompt
```

In `appV2.4/tests/test_workflow_repair.py`, add:

```python
def test_repair_prompt_includes_shared_contract_fragments() -> None:
    result = validate_workflow_draft_text(_invalid_draft())
    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None)

    assert render_side_effect_contract_fragment() in prompt
    assert render_secret_reference_fragment() in prompt
```

Import `render_required_package_blocks_fragment`, `render_schema_reminders_fragment`, `render_side_effect_contract_fragment`, and `render_secret_reference_fragment` explicitly at the top of the relevant test files.

- [ ] **Step 2: Run tests and confirm current duplication still passes or fails by exact containment**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_staging.py::test_package_stage_prompt_includes_shared_fragments \
  appV2.4/tests/test_workflow_repair.py::test_repair_prompt_includes_shared_contract_fragments -q
```

Expected: FAIL if the exact shared fragment text is not yet present as one contiguous string; PASS is acceptable if current duplicated text already matches exactly.

- [ ] **Step 3: Refactor `staging.py`**

Import fragments:

```python
from appv24.workflows.prompt_fragments import (
    render_passive_safety_fragment,
    render_required_package_blocks_fragment,
    render_rollback_safety_fragment,
    render_schema_reminders_fragment,
    render_secret_reference_fragment,
    render_side_effect_contract_fragment,
    render_workflow_passive_boundary_fragment,
)
```

Replace the literal passive safety list in `build_workflow_stage_prompt()` with:

```python
"Passive safety rules:",
render_passive_safety_fragment(),
"- Use secret references, redacted markers, or checksums for secrets; never raw secret values.",
```

Replace the package-stage labeled-section and reminder literals with:

```python
render_required_package_blocks_fragment(),
"",
"Schema reminders:",
render_schema_reminders_fragment(),
render_side_effect_contract_fragment(),
render_secret_reference_fragment(),
render_rollback_safety_fragment(),
render_workflow_passive_boundary_fragment(),
"",
PACKAGE_STAGE_MICRO_EXAMPLES,
```

- [ ] **Step 4: Refactor `repair.py`**

Import fragments:

```python
from appv24.workflows.prompt_fragments import (
    render_rollback_safety_fragment,
    render_schema_reminders_fragment,
    render_secret_reference_fragment,
    render_side_effect_contract_fragment,
)
```

Inside `build_workflow_repair_prompt()`, replace the duplicated rule reminders after `"Rules:"` with:

```python
"- Preserve the user's intended workflow.",
"- Fix schema and semantic validation issues only.",
"- Return the full package because the validator consumes complete drafts; treat it as a minimal patched copy.",
"- Do not call tools, APIs, shell commands, or write files.",
render_schema_reminders_fragment(),
render_side_effect_contract_fragment(),
render_secret_reference_fragment(),
render_rollback_safety_fragment(),
```

Keep the scoped repair contract and failed edit scope unchanged.

- [ ] **Step 5: Refactor `profiles/builtins.py`**

Import:

```python
from appv24.workflows.prompt_fragments import (
    render_required_package_blocks_fragment,
    render_schema_reminders_fragment,
    render_side_effect_contract_fragment,
)
```

Build `_WORKFLOW_DESIGNER_LITE_APPEND` with joined fragments instead of repeating every schema rule inline:

```python
_WORKFLOW_DESIGNER_LITE_APPEND = " ".join(
    (
        "Profile: workflow-designer-lite.",
        "Design safe, reviewable workflow-as-code designs from natural language.",
        "Treat every workflow as a passive design artifact, not executable work.",
        "Use local repository files only as evidence when needed.",
        "Produce the workflow design in the final response only.",
        "appv24 may ask for staged workflow compiler passes.",
        "When a stage prompt asks for intent or skeleton, answer only that stage.",
        "Only the package stage should emit labeled fenced code blocks.",
        "Do not edit, write, delete, move, or create files.",
        "Do not run shell commands.",
        "Do not run workflows.",
        "Do not call external APIs.",
        "Do not request raw secrets, send messages, publish, provision cloud resources, or mutate external systems.",
        "Structure the response with workflow summary, assumptions, approval gates, test plan, risk review, future runner target, and artifact drafts.",
        render_required_package_blocks_fragment(),
        "Use these appv24 schema shapes exactly.",
        render_schema_reminders_fragment(),
        render_side_effect_contract_fragment(),
        "Connector targets may include MCP, direct API, OpenAPI, LangGraph, n8n or Pipedream export, and IaC, but only as design targets.",
    )
)
```

Do not change profile tool permissions.

- [ ] **Step 6: Run prompt tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_profiles.py \
  appV2.4/tests/test_workflow_staging.py \
  appV2.4/tests/test_workflow_repair.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit prompt refactor**

Run:

```bash
git add appV2.4/appv24/workflows/staging.py appV2.4/appv24/workflows/repair.py appV2.4/appv24/profiles/builtins.py appV2.4/tests/test_profiles.py appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_repair.py
git commit -m "refactor(appv24): share workflow prompt fragments"
```

---

## Task 5: Verify And Fill Profile/Save Enforcement Gaps

**Files:**
- Modify: `appV2.4/tests/test_cli.py`
- Modify: `appV2.4/tests/test_tui.py`
- Modify if tests expose bugs: `appV2.4/appv24/cli.py`
- Modify if tests expose bugs: `appV2.4/appv24/tui/interactive_mode.py`

- [ ] **Step 1: Add CLI tests for missing save cases**

Add these tests to `appV2.4/tests/test_cli.py` near existing `/workflow-save` tests.

```python
def test_cli_plain_workflow_save_rejects_absolute_path(monkeypatch, tmp_path, capsys) -> None:
    assistant_text = _valid_workflow_draft_text()
    absolute_target = tmp_path.parent / "appv24-absolute-save"
    prompts = iter(["design workflow", f"/workflow-save {absolute_target}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            self.messages.append(_assistant_message(assistant_text))

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
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft save failed:" in captured.out
    assert "relative" in captured.out.lower() or "inside" in captured.out.lower()
    assert not absolute_target.exists()


def test_cli_plain_workflow_save_refuses_overwrite(monkeypatch, tmp_path, capsys) -> None:
    assistant_text = _valid_workflow_draft_text()
    existing = tmp_path / "saved-workflow"
    existing.mkdir()
    prompts = iter(["design workflow", "/workflow-save saved-workflow", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            self.messages.append(_assistant_message(assistant_text))

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
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft save failed:" in captured.out
    assert "already exists" in captured.out
    assert not existing.joinpath("workflow.yaml").exists()
```

- [ ] **Step 2: Add CLI test for `/workflow-save` outside workflow profile**

Add:

```python
def test_cli_plain_workflow_save_fails_outside_workflow_profile(monkeypatch, tmp_path, capsys) -> None:
    prompts = iter(["/workflow-save saved-workflow", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("save command should not call the model")

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
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "coding", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft save failed: active profile is not workflow-designer-lite" in captured.out
    assert not (tmp_path / "saved-workflow").exists()
```

- [ ] **Step 3: Run CLI save enforcement tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py -k 'workflow_save' -q
```

Expected: PASS. If a test fails, patch only `appV2.4/appv24/cli.py` or save helper behavior required by the failing assertion.

- [ ] **Step 4: Add TUI tests for overwrite and non-workflow profile**

Add these tests to `appV2.4/tests/test_tui.py` near existing workflow-save tests:

```python
def test_interactive_mode_workflow_save_refuses_overwrite(tmp_path) -> None:
    existing = tmp_path / "saved-workflow"
    existing.mkdir()
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, _valid_workflow_draft_text()))
    )
    terminal = FakeTerminal(columns=160, rows=80)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "workflow-designer-lite"
    inputs = iter(["design workflow", "/workflow-save saved-workflow", "/exit"])
    holder: dict[str, InteractiveMode] = {}

    def input_fn(prompt: str) -> str:
        command = next(inputs)
        if command.startswith("/workflow-save"):
            assert _wait_until(lambda: holder["mode"]._last_workflow_validation is not None, timeout=5.0)
        return command

    mode = InteractiveMode(app, input_fn=input_fn)
    holder["mode"] = mode

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(160)))
    assert "Workflow draft save failed:" in rendered
    assert "already exists" in rendered
    assert not existing.joinpath("workflow.yaml").exists()


def test_interactive_mode_workflow_save_fails_outside_workflow_profile(tmp_path) -> None:
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, "model should not run"))
    )
    terminal = FakeTerminal(columns=140, rows=40)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=terminal, enable_tui=True)
    app.profile_id = "coding"
    inputs = iter(["/workflow-save saved-workflow", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    assert mode.run() == 0

    rendered = strip_ansi("\n".join(app.tui.render(140)))
    assert "Workflow draft save failed: active profile is not workflow-designer-lite" in rendered
    assert "model should not run" not in rendered
    assert not (tmp_path / "saved-workflow").exists()
```

- [ ] **Step 5: Run TUI workflow-save tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py -k 'workflow_save' -q
```

Expected: PASS.

- [ ] **Step 6: Commit enforcement coverage**

Run:

```bash
git add appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py appV2.4/appv24/cli.py appV2.4/appv24/tui/interactive_mode.py
git commit -m "test(appv24): harden workflow save profile enforcement"
```

If `cli.py` or `interactive_mode.py` were not modified, `git add` will ignore them.

---

## Task 6: Verify Repair Bounds And Provider Error Boundary

**Files:**
- Modify: `appV2.4/tests/test_workflow_repair.py`
- Modify if tests expose bugs: `appV2.4/appv24/workflows/repair.py`
- Modify if tests expose bugs: `appV2.4/appv24/cli.py`
- Modify if tests expose bugs: `appV2.4/appv24/tui/interactive_mode.py`

- [ ] **Step 1: Add repair config tests**

In `appV2.4/tests/test_workflow_repair.py`, add these focused tests:

```python
def test_repair_config_stops_at_finite_attempt_bound() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    result = repair_workflow_draft_messages(
        messages,
        lambda prompt: prompts.append(prompt),
        config=WorkflowDraftRepairConfig(max_attempts=1, max_issues=8),
    )

    assert result.attempts == 1
    assert result.exhausted is True
    assert result.stop_reason == "max_attempts"
    assert len(prompts) == 1


def test_repair_config_unbounded_attempts_can_converge() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_valid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=None, max_issues=8),
    )

    assert result.attempts == 1
    assert result.repaired is True
    assert result.validation is not None
    assert result.validation.ok is True
```

Use the existing local helper names in `test_workflow_repair.py`; do not duplicate large fixtures if the file already exposes them under different names.

- [ ] **Step 2: Add provider-error boundary test for CLI or TUI**

Prefer CLI because it is deterministic. Add to `appV2.4/tests/test_cli.py` near repair tests:

```python
def test_cli_workflow_repair_provider_error_reports_failure(monkeypatch, tmp_path, capsys) -> None:
    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            if "Repair attempt" in prompt:
                raise RuntimeError("provider exploded")
            text = _invalid_workflow_draft_text() if "staging 3/3: package" in prompt else "stage note"
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
    assert exit_code == 0
    assert "Workflow draft repair failed: provider exploded" in captured.out
    assert "Traceback" not in captured.err
```

If current CLI crashes, patch `_repair_workflow_draft_if_needed()` in `appV2.4/appv24/cli.py` to catch `Exception` around `repair_workflow_draft_messages()` and print `Workflow draft repair failed: {error}`, matching TUI behavior.

- [ ] **Step 3: Run repair tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_repair.py \
  appV2.4/tests/test_cli.py::test_cli_workflow_repair_provider_error_reports_failure -q
```

Expected: PASS.

- [ ] **Step 4: Commit repair-bound coverage**

Run:

```bash
git add appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_cli.py appV2.4/appv24/workflows/repair.py appV2.4/appv24/cli.py appV2.4/appv24/tui/interactive_mode.py
git commit -m "test(appv24): document workflow repair bounds"
```

---

## Task 7: Focused And Full Verification

**Files:**
- Read: `git diff --name-only`
- Test: appv24 focused and full test suites

- [ ] **Step 1: Run focused verification**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_workflow_packages.py
```

Expected: all commands PASS.

- [ ] **Step 2: Run full appv24 test suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected: PASS. Current baseline is around `1185 passed`; the exact count may increase if tests were added.

- [ ] **Step 3: Run frozen-zone review**

Run:

```bash
git diff --name-only upstream/profile/appv24-built-in-profiles...HEAD
```

Review the output manually. It must not contain:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
appV2.3/
appV2.3.1/
appV2.2/
pi/
hermes-agent/
```

- [ ] **Step 4: Confirm working tree clean**

Run:

```bash
git status --short --branch
```

Expected: clean except branch ahead count.

---

## Task 8: Completion Summary

**Files:**
- Read: `git log --oneline -8`
- Read: `git status --short --branch`

- [ ] **Step 1: Collect final evidence**

Run:

```bash
git log --oneline -8
git status --short --branch
```

- [ ] **Step 2: Report to Lewis**

Report:

```text
Implemented appv24 cleanup stabilization.
Changed docs drift, shared workflow prompt fragments, profile/save enforcement tests, and repair-bound/provider-error coverage.
Verification: PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests -> <result>
Frozen-zone review: no red-zone paths touched.
Branch is clean and ahead by <N> commits.
```

Do not publish npm. Do not build or push GHCR. Do not merge or rename branches unless Lewis explicitly instructs it.
