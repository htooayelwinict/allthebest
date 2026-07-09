# appv24 Workflow Extension MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workflow catalog and quality gates consume connector/middleware extension specs instead of hardcoded workflow-domain rules.

**Architecture:** Add a green-zone `workflow_extensions.py` module that turns built-in contracts and package-declared connector/middleware hints into immutable extension specs. Capability catalog and quality gate read those specs. A separate build-time planner emits a PRD/coder prompt for missing connectors or middleware without invoking the coding profile at runtime.

**Tech Stack:** Python dataclasses, existing appv24 workflow package loader, validator, quality gate, pytest.

---

### Task 1: Extension Specs And Catalog

**Files:**
- Create: `appV2.4/appv24/workflows/workflow_extensions.py`
- Modify: `appV2.4/appv24/workflows/capability_catalog.py`
- Test: `appV2.4/tests/test_workflow_capability_catalog.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `build_workflow_capability_catalog(documents=...)` includes a custom `crm.lookup_contact` connector from `connectors.yaml`, uses `workflow_hints.catalog_group` and `workflow_hints.catalog_label`, and falls back to a safe label when hints are absent.

- [ ] **Step 2: Verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_capability_catalog.py -q
```

Expected: fail because the catalog currently only uses built-in connector contracts.

- [ ] **Step 3: Implement specs**

Create frozen dataclasses:

```python
WorkflowActionExtensionSpec
WorkflowMiddlewareExtensionSpec
WorkflowTransitConstraint
WorkflowExtensionProfile
```

Add builders:

```python
build_workflow_extension_profile(documents=None, contract_catalog=None)
extension_specs_from_connectors(connectors_document)
extension_specs_from_middlewares(middlewares_document)
```

- [ ] **Step 4: Wire catalog**

Allow `build_workflow_capability_catalog()` to accept either `documents` or an
extension profile and render built-ins plus strict custom connector specs.

- [ ] **Step 5: Verify green**

Run the catalog tests and ensure they pass.

### Task 2: Declarative Quality Constraints

**Files:**
- Modify: `appV2.4/appv24/workflows/quality_gate.py`
- Modify: `appV2.4/appv24/workflows/workflow_extensions.py`
- Test: `appV2.4/tests/test_workflow_quality_gate.py`

- [ ] **Step 1: Write failing tests**

Add one test proving the Telegram registered-user detour rejection still fires,
and another proving a custom connector-declared `workflow_hints.transit_constraints`
rule rejects a disallowed intermediate action.

- [ ] **Step 2: Verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: fail because quality gate has only the hardcoded Telegram rule.

- [ ] **Step 3: Implement generic constraint evaluation**

Replace the hardcoded registered-user allowed-intermediate constant with
extension profile transit constraints. The generic issue code is
`workflow_transit_constraint_violation`; preserve the existing
`registered_user_broadcast_detour` code for built-in Telegram compatibility.

- [ ] **Step 4: Verify green**

Run the quality gate tests and ensure existing examples still pass.

### Task 3: Build-Time Extension PRD Planner

**Files:**
- Create: `appV2.4/appv24/workflows/extension_planner.py`
- Test: `appV2.4/tests/test_workflow_extension_planner.py`

- [ ] **Step 1: Write failing tests**

Add tests proving connector and middleware build requests produce redacted PRD
prompts with goal, ids, schemas, scopes/env names, QA checklist, and an explicit
statement that runtime must not call the coding profile.

- [ ] **Step 2: Verify red**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_planner.py -q
```

Expected: fail because the planner module does not exist.

- [ ] **Step 3: Implement planner**

Add frozen request dataclasses and `render_extension_prd_prompt(request)`.
Keep it string-only and side-effect-free.

- [ ] **Step 4: Verify green**

Run the planner tests.

### Task 4: Export Surface And Verification

**Files:**
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: focused workflow tests

- [ ] **Step 1: Export safe APIs**

Export extension profile builders and PRD planner types from
`appv24.workflows`.

- [ ] **Step 2: Run targeted tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_capability_catalog.py \
  appV2.4/tests/test_workflow_quality_gate.py \
  appV2.4/tests/test_workflow_extension_planner.py \
  appV2.4/tests/test_workflow_connector_contracts.py \
  appV2.4/tests/test_workflow_middlewares.py
```

- [ ] **Step 3: Run broad verification**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
npm --prefix packages/appv24-cli test
git diff --check
git status --short --branch
```
