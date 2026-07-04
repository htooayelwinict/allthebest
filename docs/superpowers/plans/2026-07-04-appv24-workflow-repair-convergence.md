# AppV24 Workflow Repair Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed two-attempt workflow repair cap with a validator-driven convergence loop that keeps repairing until valid or until progress guards prove the loop is stuck.

**Architecture:** Keep all changes outside red-zone kernel files. The workflow repair module owns convergence state, validation fingerprints, and stop reasons; CLI/TUI only render attempt progress and final validation reports. The fixer prompt is strengthened for loop mode without loosening schemas.

**Tech Stack:** Python dataclasses, existing appv24 workflow validator, existing CLI/TUI repair hooks, pytest.

---

### Task 1: Define Convergence Loop Behavior

**Files:**
- Modify: `appV2.4/tests/test_workflow_repair.py`
- Modify: `appV2.4/appv24/workflows/repair.py`

- [ ] **Step 1: Write failing tests**

Add tests proving:
- default repair mode can run past two attempts and succeed on a later valid draft;
- repeated identical validation failures stop with a progress-guard reason;
- repair prompts in convergence mode say `Repair attempt 1` instead of `Repair attempt 1/2`;
- a repeated validation failure prompt tells the fixer to make a different structural repair.

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair.py -k 'convergence or repeated or bounded_and_schema_specific' -q
```

Expected: new tests fail because the code still uses `max_attempts=2` and `/2` prompt rendering.

- [ ] **Step 3: Implement minimal convergence state**

In `repair.py`:
- make `WorkflowDraftRepairConfig.max_attempts` optional and default it to `None`;
- add progress guards such as `max_repeated_validation_results`;
- add `stop_reason` to `WorkflowDraftRepairResult`;
- fingerprint validation issues by code, location, and message;
- loop while invalid when `max_attempts is None`;
- stop only on valid draft, missing next draft, repeated identical validation failures, explicit configured max attempts, or timeout if configured.

- [ ] **Step 4: Verify focused green**

Run the same focused pytest command. Expected: selected workflow repair tests pass.

### Task 2: Update CLI/TUI Rendering

**Files:**
- Modify: `appV2.4/tests/test_cli.py`
- Modify: `appV2.4/tests/test_tui.py`
- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`

- [ ] **Step 1: Write failing output expectations**

Update workflow repair expectations from `attempt 1/2` to `attempt 1` for convergence mode. Keep exhaustion tests, but assert stop reason text if rendered.

- [ ] **Step 2: Run focused CLI/TUI tests and verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py::test_cli_workflow_profile_repairs_invalid_draft_before_printing appV2.4/tests/test_tui.py -k 'workflow_draft' -q
```

Expected: output tests fail until callers stop passing `max_attempts=2` and render optional max attempts correctly.

- [ ] **Step 3: Implement caller updates**

In CLI/TUI:
- call `WorkflowDraftRepairConfig(max_issues=8)` without `max_attempts=2`;
- render `Workflow draft repair: attempt N` when `max_attempts is None`;
- preserve existing validation report behavior.

- [ ] **Step 4: Verify focused green**

Run the same CLI/TUI focused command. Expected: selected tests pass.

### Task 3: Full Verification And Zone Audit

**Files:**
- No production file changes expected beyond workflow repair, CLI/TUI, and tests.

- [ ] **Step 1: Run workflow-focused tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -k 'workflow' -q
```

- [ ] **Step 2: Run the full appV2.4 suite**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

- [ ] **Step 3: Confirm red-zone diff is empty**

```bash
git diff -- appV2.4/appv24/agent/agent.py appV2.4/appv24/agent/agent_loop.py appV2.4/appv24/agent/types.py appV2.4/appv24/ai/types.py appV2.4/appv24/ai/stream.py appV2.4/appv24/ai/validation.py appV2.4/appv24/compaction/compressor.py appV2.4/appv24/compaction/timing.py appV2.4/appv24/coding_agent/session_store.py appV2.4/appv24/app.py
```

Expected: no output.
