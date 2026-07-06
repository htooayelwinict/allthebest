# appv24 Workflow Approval List Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/workflow-approvals <package> <run-id>` so operators can list approval request ids needed for `/workflow-approve` and `/workflow-deny`.

**Architecture:** Keep approval reading/formatting in `workflows/approval_commands.py`, beside the existing approval decision command. CLI/TUI add narrow read-only command routing. No live transports, provider calls, or ledger mutations happen in this command.

**Tech Stack:** Existing `WorkflowRunStore`, `ApprovalRequest`, pytest CLI/TUI fakes.

---

## Files

Modify:

- `appV2.4/appv24/workflows/approval_commands.py`
- `appV2.4/appv24/workflows/__init__.py`
- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- `appV2.4/tests/test_workflow_approval_gate.py`
- `appV2.4/tests/test_cli.py`
- `appV2.4/tests/test_tui.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify red-zone/kernel files.

## Task 1: Approval List Helper

- [x] **Step 1: Write failing helper tests**

Add tests proving pending and decided approval requests are listed with request id, status, step, action, approval id, and idempotency key.

- [x] **Step 2: Verify helper tests fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_approval_gate.py -q
```

Expected: import failure or missing helper failure.

- [x] **Step 3: Implement helper and formatter**

Add `WorkflowApprovalListItem`, `WorkflowApprovalListResult`, `list_workflow_approval_requests`, and `format_workflow_approval_list`.

- [x] **Step 4: Export helper API**

Export the new names from `appV2.4/appv24/workflows/__init__.py`.

- [x] **Step 5: Verify helper tests pass**

Run the helper test command again.

## Task 2: CLI/TUI Commands

- [x] **Step 1: Write failing CLI/TUI tests**

Add tests for:

```text
/workflow-approvals <package> <run-id>
```

The tests should assert the output contains the approval request id and does not invoke the model.

- [x] **Step 2: Verify command tests fail**

Run the new command tests directly. Expected: command is not recognized and falls through to model or missing handler.

- [x] **Step 3: Wire CLI/TUI command handlers**

Add parsing/routing beside `/workflow-approve` and `/workflow-deny`.

- [x] **Step 4: Verify command tests pass**

Run the new command tests again.

## Task 3: Docs and Verification

- [x] **Step 1: Update live path docs**

Document `/workflow-approvals` in the approval-ledger section.

- [x] **Step 2: Run focused checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_approval_gate.py \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_approvals_lists_request_ids_without_model \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_approvals_lists_request_ids \
  -q
```

- [x] **Step 3: Run broad checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py appV2.4/tests/test_workflow_approval_gate.py appV2.4/tests/test_workflow_run_store.py appV2.4/tests/test_workflow_live_runner.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git status --short | sed 's/^...//' | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
git status --short | sed 's/^...//' | rg -v '^(appV2\.4/|docs/appv24/|docs/superpowers/|examples/appv24/)' || true
```
