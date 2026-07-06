# appv24 Workflow Live-Read Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe CLI/TUI `/workflow-run-live-read` command wiring for the existing appv24 live-read runner.

**Architecture:** Reuse `run_live_read_workflow`, `WorkflowRunRequest`, `credential_resolver_for_package_env`, and `LiveWorkflowBroker` with an empty transport registry. Keep execution behavior in green-zone workflow modules; only add narrow command routing in CLI/TUI after failing tests exist.

**Tech Stack:** Python dataclasses, existing appv24 workflow runner, pytest CLI/TUI test harnesses, JSON workflow run artifacts.

---

## File Structure

Modify:

- `appV2.4/tests/test_cli.py`: plain CLI command test that proves no model call and blocked live-read run creation.
- `appV2.4/tests/test_tui.py`: TUI command test with the same safety assertion.
- `appV2.4/appv24/cli.py`: import `run_live_read_workflow`, add `/workflow-run-live-read` parser and handler.
- `appV2.4/appv24/tui/interactive_mode.py`: import/use the same runner and add TUI handler/help/autocomplete command.
- `docs/appv24/workflow-run-live-path.md`: document the command and disabled transport registry boundary.

Do not modify red-zone/kernel files.

## Task 1: CLI Live-Read Command

- [x] **Step 1: Write failing CLI test**

Add a test named `test_cli_plain_workflow_run_live_read_creates_blocked_run_without_transport` that:

- writes the `github_dependabot_triage` golden package
- sets `APPV24_GITHUB_CREDENTIAL_REF=github-prod`
- sets `APPV24_GITHUB_SCOPES=github:security_events:read`
- runs `/workflow-run-live-read github_dependabot_triage happy_path.yaml --allow github.list_dependabot_alerts`
- uses a fake app whose `run_turn` raises
- asserts output contains `Workflow live-read run: blocked`
- asserts output contains `live transport is not configured for github.list_dependabot_alerts`
- asserts one `.workflow-runs/run-*` directory exists

- [x] **Step 2: Verify CLI test fails**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py::test_cli_plain_workflow_run_live_read_creates_blocked_run_without_transport -q
```

Expected: fails because the command falls through to workflow drafting/model execution.

- [x] **Step 3: Implement CLI command**

In `appV2.4/appv24/cli.py`:

- import `run_live_read_workflow`
- add one-shot and plain-loop routing before model execution
- parse exactly `/workflow-run-live-read <path> <fixture> --allow <action>[,<action>]`
- call `run_live_read_workflow` with `WorkflowRunMode.LIVE_READ`, empty `LiveWorkflowBroker`, and env credential resolver
- render the report with label `Workflow live-read run:`

- [x] **Step 4: Verify CLI test passes**

Run the same focused pytest command. Expected: pass.

## Task 2: TUI Live-Read Command

- [x] **Step 1: Write failing TUI test**

Add a test named `test_interactive_mode_workflow_run_live_read_creates_blocked_run_without_transport` that:

- writes the `github_dependabot_triage` golden package
- sets the same GitHub env credential values
- runs TUI inputs for `/workflow-run-live-read ...` then `/exit`
- asserts rendered history contains `Workflow live-read run: blocked`
- asserts rendered history contains the missing transport issue
- asserts no model prompt was run

- [x] **Step 2: Verify TUI test fails**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py::test_interactive_mode_workflow_run_live_read_creates_blocked_run_without_transport -q
```

Expected: fails because the TUI has no live-read command route.

- [x] **Step 3: Implement TUI command**

In `appV2.4/appv24/tui/interactive_mode.py`:

- add slash command metadata/help text
- add `_run_workflow_run_live_read_command`
- add `_is_workflow_run_live_read_command`
- add parser dataclass/function mirroring live-draft without approval-run-id
- call `run_live_read_workflow` with empty `LiveWorkflowBroker`
- render report with label `Workflow live-read run:`

- [x] **Step 4: Verify TUI test passes**

Run the same focused pytest command. Expected: pass.

## Task 3: Docs and Verification

- [x] **Step 1: Update live path docs**

Document:

- `/workflow-run-live-read <package> <fixture> --allow <action>[,<action>]`
- env credential ref format
- empty transport registry in v0
- blocked run artifact behavior

- [x] **Step 2: Run focused checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_run_live_read_creates_blocked_run_without_transport \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_run_live_read_creates_blocked_run_without_transport \
  -q
```

- [x] **Step 3: Run broad checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py appV2.4/tests/test_workflow_live_runner.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git diff --name-only | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
git diff --name-only | rg -v '^(appV2\.4/|docs/appv24/|docs/superpowers/|examples/appv24/)' || true
```

Expected: tests pass, diff check clean, red-zone/appV2.3 guard empty, unexpected-path guard empty.
