# appv24 Live Run Explain Approval Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show `approval_run_id` in live run explanations so production runs can be audited back to the local approval run.

**Architecture:** Keep all behavior inside read-only explanation helpers. The live runner already persists `approval_run_id` in `run.json`; `_provenance()` only needs to render it.

**Tech Stack:** Python, pytest, existing appv24 workflow run store and explanation helpers.

---

## Files

Modify:

- `appV2.4/appv24/workflows/explain.py`
- `appV2.4/tests/test_workflow_explain.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify appv24 red-zone files. Do not modify appv23 or appv231.

## Task 1: Red Test

- [x] **Step 1: Add live-production explanation test**

Add `test_run_explanation_reports_live_production_provenance_without_filtered_outputs` to `appV2.4/tests/test_workflow_explain.py`.

- [x] **Step 2: Run focused test and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_explain.py::test_run_explanation_reports_live_production_provenance_without_filtered_outputs -q
```

Expected: failure because `_provenance()` omits `approval_run_id`.

## Task 2: Minimal Implementation

- [x] **Step 1: Render approval run provenance**

In `appV2.4/appv24/workflows/explain.py`, update `_provenance()` to append:

```python
approval_run_id = run_manifest.get("approval_run_id")
if isinstance(approval_run_id, str) and approval_run_id:
    items.append(f"approval run: {approval_run_id}")
```

- [x] **Step 2: Run focused explanation test**

Run the same focused test and expect pass.

## Task 3: Docs and Verification

- [x] **Step 1: Update live path docs**

Document that `workflow-run-explain` style output includes the prior approval run id for live-draft/live-production runs.

- [x] **Step 2: Run verification**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_explain.py appV2.4/tests/test_workflow_live_runner.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git diff --name-only | rg '^(appV2\.4/appv24/(agent/|ai/(stream|types|validation)\.py|compaction/|coding_agent/session_store\.py|app\.py)|appV2\.3|packages/appv23-cli|packages/appv231-cli|appV2\.3\.1)' || true
```
