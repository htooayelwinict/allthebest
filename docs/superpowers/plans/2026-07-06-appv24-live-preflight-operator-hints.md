# appv24 Live Preflight Operator Hints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe environment-variable hints to missing live transport preflight failures.

**Architecture:** Keep the change inside workflow preflight diagnostics. A helper maps exact supported live connector actions to safe env names, and `_transport_gate_issue()` appends those hints when the action is missing from `available_transports`.

**Tech Stack:** Python, pytest, existing appv24 workflow preflight modules.

---

## Files

Modify:

- `appV2.4/appv24/workflows/live_preflight.py`
- `appV2.4/tests/test_workflow_live_preflight.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify appv24 red-zone files. Do not modify appv23 or appv231.

## Task 1: Add Preflight Hint Tests

- [x] **Step 1: Update missing-transport assertions**

In `appV2.4/tests/test_workflow_live_preflight.py`, update the missing transport tests to expect env-name hints for:

```python
"live-read transport is not configured for github.list_dependabot_alerts; required env: APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN"
"live-draft transport is not configured for gmail.create_draft; required env: APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN or APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET"
"live-production transport is not configured for telegram.send_message; required env: APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, APPV24_TELEGRAM_BOT_TOKEN"
```

- [x] **Step 2: Run focused tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_preflight.py -q
```

Expected: the updated tests fail because `_transport_gate_issue()` still returns the old generic issue.

## Task 2: Add Hint Helper

- [x] **Step 1: Implement the helper**

In `appV2.4/appv24/workflows/live_preflight.py`, add a private helper:

```python
def _transport_env_hint(action_ref: str) -> str | None:
    return {
        "github.list_dependabot_alerts": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN",
        "gmail.create_draft": (
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN "
            "or APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET"
        ),
        "telegram.send_message": "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, APPV24_TELEGRAM_BOT_TOKEN",
    }.get(action_ref)
```

Update `_transport_gate_issue()` so it appends `; required env: <hint>` only when the action has a known hint.

- [x] **Step 2: Run focused tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_preflight.py -q
```

## Task 3: Document Operator Hints

- [x] **Step 1: Update live path docs**

In `docs/appv24/workflow-run-live-path.md`, document that transport readiness failures include safe env variable names but never values.

- [x] **Step 2: Run full verification**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git diff --name-only | rg '^(appV2\.4/appv24/(agent/|ai/(stream|types|validation)\.py|compaction/|coding_agent/session_store\.py|app\.py)|appV2\.3|packages/appv23-cli|packages/appv231-cli|appV2\.3\.1)' || true
```
