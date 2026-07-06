# appv24 Local Approval Run Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce that live-draft and live-production `approval_run_id` values reference a prior local run.

**Architecture:** Add a read-only run manifest helper to `WorkflowRunStore`. Use it in live preflight and live runner approval-index construction before reading approval decisions.

**Tech Stack:** Python, appv24 workflow run store, live preflight/runner, pytest.

---

### Task 1: Add Failing Tests

**Files:**
- Modify: `appV2.4/tests/test_workflow_run_store.py`
- Modify: `appV2.4/tests/test_workflow_live_preflight.py`
- Modify: `appV2.4/tests/test_workflow_live_runner.py`

- [x] Add a run-store test proving `read_run_manifest()` returns `run.json`.
- [x] Add a live-production preflight test that changes the approval run manifest to `mode: preview` and expects `live-production requires approval_run_id from a local run`.
- [x] Add a live-draft runner test that changes the approval run manifest to `mode: preview`, expects `live-draft requires approval_run_id from a local run`, and asserts the broker transport is not invoked.
- [x] Run focused tests and verify they fail before implementation.

### Task 2: Implement The Gate

**Files:**
- Modify: `appV2.4/appv24/workflows/run_store.py`
- Modify: `appV2.4/appv24/workflows/live_preflight.py`
- Modify: `appV2.4/appv24/workflows/live_runner.py`

- [x] Add `WorkflowRunStore.read_run_manifest(run_id)`.
- [x] In live preflight `_approval_index()`, reject manifests whose `mode` is not `local`.
- [x] In live runner `_live_draft_approval_index()`, reject manifests whose `mode` is not `local`.
- [x] Preserve existing unknown-run behavior.

### Task 3: Document And Verify

**Files:**
- Modify: `docs/appv24/workflow-run-live-path.md`

- [x] Clarify that appv24 validates `run.json mode=local` before accepting approval decisions.
- [x] Run focused tests.
- [x] Run full appv24 suite.
- [x] Run `git diff --check`.
- [x] Confirm no red-zone/appv23/appv231 paths changed.

### Additional Coverage

- [x] Added direct live-production runner coverage proving a non-local `approval_run_id` blocks before broker transport invocation.
