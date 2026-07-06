# appv24 Idempotency inputRef Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workflow idempotency keys account for selected fixture input values when key fields use `inputRef`.

**Architecture:** Keep one shared helper in `workflows/idempotency.py`. Pass the selected fixture from local run, live preflight, and live runner paths so approval creation and approval matching use identical key material.

**Tech Stack:** Python, appv24 workflow runner/preflight modules, pytest.

---

### Task 1: Add Failing Tests

**Files:**
- Modify: `appV2.4/tests/test_workflow_idempotency.py`
- Modify: `appV2.4/tests/test_workflow_live_preflight.py`

- [x] Add a unit test that calls `build_idempotency_key()` twice with the same raw `inputRef` fields and different fixture input values.
- [x] Add a preflight regression test that approves `happy_path.yaml` and then verifies `alternate_path.yaml` is blocked with `approval not granted for notify_admin`.
- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_idempotency.py \
  appV2.4/tests/test_workflow_live_preflight.py::test_live_production_preflight_blocks_approval_for_different_resolved_inputref_fixture -q
```

Expected: both new tests fail before implementation.

### Task 2: Resolve inputRef Key Material

**Files:**
- Modify: `appV2.4/appv24/workflows/idempotency.py`

- [x] Add an optional `fixture` parameter to `build_idempotency_key()`.
- [x] Resolve a key-field value shaped as `{"inputRef": "<name>"}` to `fixture["input"][name]` when available.
- [x] Keep non-reference values and missing fixture values deterministic.

### Task 3: Thread Fixture Through Call Sites

**Files:**
- Modify: `appV2.4/appv24/workflows/runner.py`
- Modify: `appV2.4/appv24/workflows/live_runner.py`
- Modify: `appV2.4/appv24/workflows/live_preflight.py`

- [x] Pass `fixture` into local-run idempotency key creation.
- [x] Pass `fixture` into live preflight gate checks.
- [x] Pass `fixture` into live draft and live production runner gate checks and brokered action previews.

### Task 4: Verify

**Files:**
- No code edits.

- [x] Run the focused tests from Task 1 and verify pass.
- [x] Run full suite:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

- [x] Run:

```bash
git diff --check
```

- [x] Confirm changed files remain outside appv24 red-zone paths.
