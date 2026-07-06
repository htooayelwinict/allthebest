# appv24 Credential Preflight Operator Hints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe environment-variable hints to credential preflight failures.

**Architecture:** Keep credential resolution unchanged. Add private formatting helpers in `credential_refs.py` so `preflight_credential_refs()` appends connector-specific env names and required scope lists to missing-ref, wildcard-scope, and missing-scope issues.

**Tech Stack:** Python, pytest, existing appv24 workflow credential preflight module.

---

## Files

Modify:

- `appV2.4/appv24/workflows/credential_refs.py`
- `appV2.4/tests/test_workflow_credential_refs.py`
- `appV2.4/tests/test_workflow_live_runner.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify appv24 red-zone files. Do not modify appv23 or appv231.

## Task 1: Credential Hint Tests

- [x] **Step 1: Update and add failing tests**

In `appV2.4/tests/test_workflow_credential_refs.py`, expect:

```python
"missing credential ref for telegram; required env: APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES=telegram:send"
"credential for telegram does not grant telegram:send; required env: APPV24_TELEGRAM_SCOPES=telegram:send"
"wildcard credential scope is not allowed for ticket; configure APPV24_TICKET_SCOPES without *"
```

In `appV2.4/tests/test_workflow_live_runner.py`, update the exact missing GitHub credential issue to include:

```python
"missing credential ref for github; required env: APPV24_GITHUB_CREDENTIAL_REF, APPV24_GITHUB_SCOPES=dependabot:read"
```

- [x] **Step 2: Run focused tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_credential_refs.py appV2.4/tests/test_workflow_live_runner.py::test_live_read_runner_blocks_missing_credential_before_transport_invocation -q
```

Expected: tests fail because credential issues still use the old terse messages.

## Task 2: Implement Safe Hints

- [x] **Step 1: Implement helper functions**

In `credential_refs.py`, add helpers for:

- required scope extraction from each permission row.
- env hint formatting using `credential_env_name(connector_id)`.
- missing credential, missing scope, and wildcard scope messages.

- [x] **Step 2: Run focused tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_credential_refs.py appV2.4/tests/test_workflow_live_runner.py::test_live_read_runner_blocks_missing_credential_before_transport_invocation -q
```

## Task 3: Docs and Verification

- [x] **Step 1: Update live path docs**

Document that credential preflight failures include safe env variable names and required scopes, never raw values.

- [x] **Step 2: Run verification**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_credential_refs.py appV2.4/tests/test_workflow_live_preflight.py appV2.4/tests/test_workflow_live_runner.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git diff --name-only | rg '^(appV2\.4/appv24/(agent/|ai/(stream|types|validation)\.py|compaction/|coding_agent/session_store\.py|app\.py)|appV2\.3|packages/appv23-cli|packages/appv231-cli|appV2\.3\.1)' || true
```
