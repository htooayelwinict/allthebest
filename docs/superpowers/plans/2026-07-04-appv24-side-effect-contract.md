# appv24 Side-Effect Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add passive, broker-ready side-effect contract validation for appv24 workflow packages without touching kernel red-zone files.

**Architecture:** Keep all behavior inside the workflow package layer. JSON Schema validates structural fields on connector actions; semantic validation enforces brokered/design-only mutation rules across connectors, workflow steps, permissions, fixtures, and rollback. Staging and repair prompts teach the contract, while the validator remains authoritative.

**Tech Stack:** Python 3.13, PyYAML safe loading, `jsonschema.Draft202012Validator`, pytest, appv24 workflow package modules.

---

## File Map

- Modify `appV2.4/tests/test_workflow_packages.py`: Add schema and semantic regression tests; update minimal valid package fixture with broker-ready metadata.
- Modify `appV2.4/tests/test_workflow_staging.py`: Assert package-stage prompt mentions side-effect contract requirements.
- Modify `appV2.4/appv24/workflows/schema.py`: Add structural schema fields for connector action side-effect contracts.
- Modify `appV2.4/appv24/workflows/semantic.py`: Add cross-document side-effect contract checks.
- Modify `appV2.4/appv24/workflows/staging.py`: Add package-stage guidance for brokered/design-only actions.
- Modify `appV2.4/appv24/workflows/repair.py`: Add repair guidance for missing broker metadata and design-only misuse.
- Modify `docs/appv24/workflow-package-validator.md`: Document the side-effect contract.

Do not modify red-zone files:

```text
appV2.4/appv24/agent/agent.py
appV2.4/appv24/agent/agent_loop.py
appV2.4/appv24/agent/types.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/compressor.py
appV2.4/appv24/compaction/timing.py
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

## Task 1: Write Failing Contract Tests

**Files:**
- Modify: `appV2.4/tests/test_workflow_packages.py`
- Modify: `appV2.4/tests/test_workflow_staging.py`

- [ ] **Step 1: Update the minimal package fixture to represent a valid brokered mutation**

In `write_minimal_package()`, change the `create_issue` connector action to include:

```yaml
side_effect: true
side_effect_class: create
execution_mode: brokered
approval_required: true
dry_run_supported: true
idempotency:
  key_fields:
    - repo
    - alert_id
outputs:
  allowed:
    - issue_number
    - issue_url
required_scopes:
  - issues:write
```

- [ ] **Step 2: Add schema tests**

Add tests that mutate the loaded connector action and call `validate_schema_documents()`:

```python
def test_schema_validation_rejects_invalid_action_execution_mode(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1]["execution_mode"] = "automatic"

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(issues, file="connectors.yaml", path=("connectors", 0, "actions", 1, "execution_mode"))
```

Add equivalent schema tests for:

```text
outputs.allowed is not a list
outputs.allowed contains a non-string
idempotency.key_fields is not a list
idempotency.key_fields contains a non-string
invalid side_effect_class
```

- [ ] **Step 3: Add semantic tests**

Add tests that call `validate_semantics()` and assert issue codes:

```python
def test_semantic_validation_requires_broker_metadata_for_mutating_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    action = connectors["connectors"][0]["actions"][1]
    action.pop("execution_mode")

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(issues, file="connectors.yaml", code="missing_side_effect_contract", message_substring="execution_mode")
```

Add semantic tests for:

```text
brokered action approval_required false fails
brokered action dry_run_supported false fails
brokered action outputs.allowed missing fails
brokered outputs.allowed wildcard fails
create/send/publish/provision idempotency.key_fields missing fails
design_only action referenced by a workflow step fails
design_only action in permissions is allowed when no workflow step calls it
manual mutating action can be declared without broker dry-run enforcement when no workflow step calls it
valid Telegram draft-only connector passes without executable send step
```

- [ ] **Step 4: Add staging prompt tests**

In `test_build_package_stage_prompt_requires_full_package_sections()`, assert prompt text contains:

```python
assert "brokered mutating actions require approval_required: true" in prompt
assert "design-only actions must not be called by workflow steps" in prompt
assert "outputs.allowed" in prompt
```

- [ ] **Step 5: Run tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_staging.py -q
```

Expected: fails because schema and semantic rules are not implemented yet.

## Task 2: Implement Schema Fields

**Files:**
- Modify: `appV2.4/appv24/workflows/schema.py`

- [ ] **Step 1: Add reusable schema fragments near the top of the file**

Add constants:

```python
SIDE_EFFECT_CLASS_VALUES = ["create", "update", "delete", "send", "publish", "provision"]
EXECUTION_MODE_VALUES = ["brokered", "manual", "design_only"]
```

- [ ] **Step 2: Extend connector action properties**

In `CONNECTORS_SCHEMA["properties"]["connectors"]["items"]["properties"]["actions"]["items"]["properties"]`, add:

```python
"side_effect_class": {"type": "string", "enum": SIDE_EFFECT_CLASS_VALUES},
"execution_mode": {"type": "string", "enum": EXECUTION_MODE_VALUES},
"approval_required": {"type": "boolean"},
"dry_run_supported": {"type": "boolean"},
"idempotency": {
    "type": "object",
    "properties": {
        "key_fields": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": True,
},
"outputs": {
    "type": "object",
    "properties": {
        "allowed": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": True,
},
```

Do not add conditional required logic in schema. Keep cross-field rules in `semantic.py` for clearer issue codes.

- [ ] **Step 3: Run schema tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py -q
```

Expected: schema tests pass; semantic tests still fail until Task 3.

## Task 3: Implement Semantic Contract Checks

**Files:**
- Modify: `appV2.4/appv24/workflows/semantic.py`

- [ ] **Step 1: Add constants**

Add near existing constants:

```python
BROKERED_IDEMPOTENCY_CLASSES = {"create", "send", "publish", "provision"}
SIDE_EFFECT_CONTRACT_FIELDS = ("side_effect_class", "execution_mode", "approval_required", "dry_run_supported")
```

- [ ] **Step 2: Track connector metadata**

Add helpers:

```python
def _connector_action_entries(connectors_document: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    entries: dict[tuple[str, str], Mapping[str, Any]] = {}
    for connector in connectors_document.get("connectors", []):
        if not isinstance(connector, dict) or not isinstance(connector.get("id"), str):
            continue
        connector_id = str(connector["id"])
        for action in connector.get("actions", []):
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                entries[(connector_id, str(action["id"]))] = action
    return entries
```

Add a helper to map action uses to workflow steps:

```python
def _steps_by_action_use(steps: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    used: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for step in steps:
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        connector_id, _, action_id = uses.partition(".")
        used.setdefault((connector_id, action_id), []).append(step)
    return used
```

- [ ] **Step 3: Add `_side_effect_contract_issues()`**

Implement checks:

```text
side_effect true actions require all contract fields
brokered actions require approval_required true
brokered actions require dry_run_supported true
brokered actions require outputs.allowed with no "*"
create/send/publish/provision brokered actions require idempotency.key_fields
design_only actions cannot be used by workflow steps
```

Use issue codes:

```text
missing_side_effect_contract
invalid_side_effect_contract
design_only_action_used
```

- [ ] **Step 4: Wire into `validate_semantics()`**

After connector reference checks and before approval checks:

```python
action_entries = _connector_action_entries(documents.connectors)
steps_by_action = _steps_by_action_use(steps)
issues.extend(_side_effect_contract_issues(action_entries, steps_by_action))
```

- [ ] **Step 5: Run semantic tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py -q
```

Expected: all workflow package tests pass.

## Task 4: Update Staging And Repair Prompts

**Files:**
- Modify: `appV2.4/appv24/workflows/staging.py`
- Modify: `appV2.4/appv24/workflows/repair.py`
- Test: `appV2.4/tests/test_workflow_staging.py`

- [ ] **Step 1: Add package-stage prompt guidance**

In the package stage schema reminders, add lines:

```python
"- Mutating connector actions must include side_effect_class, execution_mode, approval_required, and dry_run_supported.",
"- Brokered mutating actions require approval_required: true and dry_run_supported: true.",
"- Brokered create/send/publish/provision actions require idempotency.key_fields.",
"- Brokered mutating actions require outputs.allowed with explicit field names and no wildcard.",
"- Design-only actions must use execution_mode: design_only and must not be called by workflow steps.",
"- Telegram draft-only means no workflow step calls telegram.send_message.",
```

- [ ] **Step 2: Add repair prompt guidance**

In `build_workflow_repair_prompt()`, add rules:

```python
"- Mutating connector actions must include side_effect_class, execution_mode, approval_required, and dry_run_supported.",
"- Brokered mutating actions require approval_required: true, dry_run_supported: true, idempotency.key_fields, and outputs.allowed.",
"- Design-only connector actions must not be referenced by workflow steps.",
"- For draft-only Telegram, keep the message as a local artifact and do not call telegram.send_message.",
```

- [ ] **Step 3: Run staging and repair tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_repair.py -q
```

Expected: pass.

## Task 5: Update Docs And Verify

**Files:**
- Modify: `docs/appv24/workflow-package-validator.md`

- [ ] **Step 1: Document side-effect contract**

Add a validation-layer bullet after current permission/approval rules:

```text
Brokered mutating connector actions require explicit side-effect contract metadata: side_effect_class, execution_mode, approval_required, dry_run_supported, idempotency for create/send/publish/provision, and outputs.allowed.
```

Add a short section showing a valid brokered mutating action and a design-only Telegram action.

- [ ] **Step 2: Run targeted workflow tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_staging.py -q
```

Expected: pass.

- [ ] **Step 3: Run full appv24 test suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected: pass.

- [ ] **Step 4: Prove red-zone untouched**

Run:

```bash
git diff --name-only
```

Expected: no files under:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

