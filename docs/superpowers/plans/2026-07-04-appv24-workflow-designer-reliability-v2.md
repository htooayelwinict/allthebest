# appv24 Workflow Designer Reliability v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `workflow-designer-lite` drafts easier to validate, repair, and save safely without adding workflow execution.

**Architecture:** Keep all behavior in the appV2.4 workflow package layer. Add typed issue metadata, draft-quality semantic checks, stronger repair/staging prompt guidance, and docs/tests around the passive execution boundary. Do not touch red-zone runtime/kernel files.

**Tech Stack:** Python 3, pytest, JSON Schema via `jsonschema`, YAML package fixtures.

---

## File Structure

- Modify `appV2.4/appv24/workflows/errors.py` to extend `WorkflowIssue` with optional `expected` and `repair_hint` fields while preserving existing constructor compatibility.
- Modify `appV2.4/appv24/workflows/schema.py` to emit taxonomy-style schema issue codes plus expected/hint metadata.
- Modify `appV2.4/appv24/workflows/semantic.py` to add draft quality contract checks and richer issue metadata for existing semantic checks.
- Modify `appV2.4/appv24/workflows/repair.py` to include expected/hint metadata in compact validation feedback.
- Modify `appV2.4/appv24/workflows/staging.py` to ask for the draft quality contract in intent, skeleton, and package stages.
- Modify `docs/appv24/workflow-package-validator.md` with the reliability v2 contract and passive boundary.
- Add/modify focused tests in `appV2.4/tests/test_workflow_packages.py`, `appV2.4/tests/test_workflow_repair.py`, and `appV2.4/tests/test_workflow_staging.py`.

## Tasks

### Task 1: Issue Metadata Contract

- [x] Write tests proving schema issues expose specific taxonomy codes, expected metadata, and repair hints.
- [x] Run the focused tests and confirm they fail against current generic issue data.
- [x] Extend `WorkflowIssue` and schema issue conversion minimally.
- [x] Re-run the focused tests and confirm they pass.

### Task 2: Draft Quality Contract

- [x] Write tests proving workflow drafts require `design.intent`, `design.inputs`, `design.outputs`, `design.assumptions`, and `design.failure_modes`.
- [x] Run the focused tests and confirm they fail.
- [x] Add semantic validation for package-level draft quality metadata.
- [x] Re-run focused workflow package tests.

### Task 3: Repair Prompt Hardening

- [x] Write tests proving repair prompts include expected shape and repair hints from issues.
- [x] Run tests and confirm failure.
- [x] Update compact repair issue rendering.
- [x] Re-run workflow repair tests.

### Task 4: Staging Prompt Contract

- [x] Write tests proving staging prompts ask for the design contract and passive execution boundary.
- [x] Run tests and confirm failure.
- [x] Update staging prompt strings.
- [x] Re-run staging tests.

### Task 5: Docs and Full Verification

- [x] Update workflow validator docs with the reliability v2 contract.
- [x] Run focused workflow tests.
- [x] Run full appV2.4 tests.
- [x] Check git diff for red-zone avoidance.
