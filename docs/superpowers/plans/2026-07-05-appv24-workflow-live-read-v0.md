# appv24 Workflow Live-Read V0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe appv24 live-read workflow runner that can execute allowlisted read-only connector actions through injected live transports while preserving credential, policy, output, and audit gates.

**Architecture:** Keep the implementation in the green-zone workflow package. Reuse existing package validation, IR compile, policy, credential preflight, `LiveWorkflowBroker`, `WorkflowRunStore`, and report dataclasses; do not touch kernel/red-zone files and do not add TUI wiring in this phase.

**Tech Stack:** Python dataclasses, existing appv24 workflow modules, JSON/JSONL run store, pytest with fake live transports.

---

## File Structure

- Modify: `appV2.4/appv24/workflows/live_broker.py`
  - Reuse broker output filtering so live transports cannot leak undeclared outputs or silently produce incompatible output names.
- Create: `appV2.4/appv24/workflows/live_runner.py`
  - Add `run_live_read_workflow()` for `WorkflowRunMode.LIVE_READ` only.
  - Enforce validation, compile, policy, credential preflight, action allowlist, read-only action gates, live broker invocation, events, trace, and report persistence.
- Modify: `appV2.4/appv24/workflows/__init__.py`
  - Export the stable live-read runner API after tests pass.
- Test: `appV2.4/tests/test_workflow_live_broker.py`
  - Add live output mismatch contract tests.
- Test: `appV2.4/tests/test_workflow_live_runner.py`
  - Add full headless live-read runner contract tests.
- Docs: `docs/appv24/workflow-run-live-path.md`
  - Record the implemented v0 boundary.

## Tasks

### Task 1: Live Broker Output Contract

- [x] Write a failing test showing `LiveWorkflowBroker` blocks when a transport returns outputs that do not match `allowed_outputs`.
- [x] Run `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_broker.py -q` and verify the new test fails.
- [x] Update `LiveWorkflowBroker.invoke()` to call `filter_broker_outputs()`.
- [x] Rerun the focused test and verify it passes.

### Task 2: Live-Read Runner Contract

- [x] Write failing tests for `run_live_read_workflow()`:
  - passes a read-only package through an allowlisted fake transport
  - blocks missing credential refs before transport invocation
  - blocks mutating workflow steps in `live-read`
  - blocks connector actions absent from `WorkflowRunRequest.connector_allowlist`
- [x] Run `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_runner.py -q` and verify import/behavior failures.
- [x] Implement `appV2.4/appv24/workflows/live_runner.py` with minimal traversal and report persistence.
- [x] Export `run_live_read_workflow` from `appV2.4/appv24/workflows/__init__.py`.
- [x] Rerun the focused live-runner tests and verify they pass.

### Task 3: Documentation and Verification

- [x] Update `docs/appv24/workflow-run-live-path.md` with the live-read v0 guarantees and exclusions.
- [x] Run workflow-focused tests:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_*.py -q
```

- [x] Run full appv24 tests:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests -q
```

- [x] Run:

```bash
git diff --check
```
