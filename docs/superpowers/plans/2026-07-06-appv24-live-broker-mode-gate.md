# appv24 Live Broker Mode Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every live workflow runner rejects a broker constructed for the wrong live mode before any transport invocation.

**Architecture:** Add a read-only broker mode contract and runner-side preflight checks after credential preflight.

**Tech Stack:** Python, appv24 live broker/runner, pytest.

---

### Task 1: Add Failing Tests

**Files:**
- Modify: `appV2.4/tests/test_workflow_live_runner.py`
- Modify: `appV2.4/tests/test_workflow_live_broker.py`

- [x] Add a live broker test proving unknown broker modes are rejected at construction.
- [x] Add a live broker test proving `production_enabled=True` is rejected outside `live-production`.
- [x] Add a live broker test proving non-blocked transport statuses must match broker mode.
- [x] Add a live broker test proving blocked transport outputs are dropped.
- [x] Add a live-read runner test proving a `live-draft` broker blocks before transport.
- [x] Add a live-draft runner test proving a `live-read` broker blocks before transport.
- [x] Add a live-production runner test proving a `live-draft` broker blocks before transport.
- [x] Run focused tests and verify they fail before implementation.

### Task 2: Implement The Gate

**Files:**
- Modify: `appV2.4/appv24/workflows/live_broker.py`
- Modify: `appV2.4/appv24/workflows/live_runner.py`

- [x] Validate `LiveWorkflowBroker.mode` at construction.
- [x] Validate that `production_enabled=True` is only valid for `live-production`.
- [x] Block non-blocked transport results whose status does not match broker mode.
- [x] Drop outputs from blocked transport results.
- [x] Add a read-only `LiveWorkflowBroker.mode` property.
- [x] Add runner-side broker mode checks for `live-read`, `live-draft`, and `live-production`.
- [x] Persist blocked reports with a `broker_preflight_blocked` event.
- [x] Preserve existing transport/output filtering behavior when the mode matches.

### Task 3: Document And Verify

**Files:**
- Modify: `docs/appv24/workflow-run-live-path.md`
- Modify: `docs/appv24/plan-implementation-audit.md`

- [x] Document the broker mode gate.
- [x] Run focused red tests after implementation.
- [x] Run live broker and live runner suites.
- [x] Run full appv24 suite.
- [x] Run `git diff --check`.
- [x] Confirm no red-zone/appv23/appv231 paths changed.
