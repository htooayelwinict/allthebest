# appv24 Workflow Live-Read Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add no-side-effect `/workflow-live-read-preflight` checks for saved appv24 workflow packages.

**Architecture:** Extend `workflows/live_preflight.py` with a live-read preflight function and formatter. Add narrow CLI/TUI adapters that call the pure preflight function and never invoke the model or create run artifacts.

**Tech Stack:** Python dataclasses, existing workflow validator/compiler/policy/credential modules, pytest, appv24 CLI/TUI command patterns.

---

## Files

Modify:

- `appV2.4/appv24/workflows/live_preflight.py`
- `appV2.4/tests/test_workflow_live_preflight.py`
- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- `appV2.4/tests/test_cli.py`
- `appV2.4/tests/test_tui.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify red-zone/kernel files.

## Task 1: Core Live-Read Preflight

- [x] **Step 1: Write failing core tests**

Add tests that call `preflight_live_read_workflow` and assert:

- a read-only GitHub package passes with credential scope and allowlist
- a mutating Gmail draft package blocks with `live-read cannot execute mutating step create_gmail_draft`
- a missing allowlist blocks with `live-read action is not allowlisted: github.list_dependabot_alerts`
- formatting starts with `Live-read preflight: passed`

- [x] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_preflight.py -q
```

Expected: import failure for `preflight_live_read_workflow`.

- [x] **Step 3: Implement core function**

Add `preflight_live_read_workflow` in `live_preflight.py`. It should mirror live-draft load/validation/compile/policy/fixture/credential flow, then traverse reachable steps and check:

- no mutating actions
- action is request allowlisted
- action exists in connector catalog
- catalog metadata marks `live_read_eligible`

- [x] **Step 4: Implement formatter**

Add `format_live_read_preflight_report(report)` with label `Live-read preflight`.

- [x] **Step 5: Verify core tests pass**

Run the focused test command again.

## Task 2: CLI/TUI Commands

- [x] **Step 1: Write failing CLI/TUI tests**

Add tests for:

```text
/workflow-live-read-preflight <package> <fixture> --allow github.list_dependabot_alerts
```

Both tests must prove:

- the model is not called
- output includes `Live-read preflight: passed`
- output includes `github.list_dependabot_alerts`
- `.workflow-runs` is not created

- [x] **Step 2: Verify command tests fail**

Run the two new tests directly. Expected: command falls through to model/TUI prompt handling.

- [x] **Step 3: Implement CLI/TUI command adapters**

Add parser/handler in `cli.py` and `interactive_mode.py`. Use `credential_resolver_for_package_env`, `WorkflowRunMode.LIVE_READ`, and the pure preflight function.

- [x] **Step 4: Verify command tests pass**

Run the two new tests directly.

## Task 3: Docs and Verification

- [x] **Step 1: Document command**

Update `docs/appv24/workflow-run-live-path.md` under live-read boundary with the preflight command and no-run guarantee.

- [x] **Step 2: Run focused checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_preflight.py \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_live_read_preflight_passes_without_model_or_run \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_live_read_preflight_passes_without_run \
  -q
```

- [x] **Step 3: Run broad checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py appV2.4/tests/test_workflow_live_preflight.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git diff --name-only | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
git diff --name-only | rg -v '^(appV2\.4/|docs/appv24/|docs/superpowers/|examples/appv24/)' || true
```
