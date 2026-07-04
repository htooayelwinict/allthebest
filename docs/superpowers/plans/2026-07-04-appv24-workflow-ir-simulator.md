# appv24 Workflow IR Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a passive workflow IR compiler, policy checker, dry-run simulator, and ledger for valid appv24 workflow packages without executing connectors.

**Architecture:** Keep the whole feature inside `appV2.4/appv24/workflows/` and workflow tests. Existing loader/schema/semantic validation remains authoritative for package validity; the new compiler lowers validated documents into typed dataclasses, policy evaluates the IR, and simulator traverses fixtures into an inspectable ledger.

**Tech Stack:** Python 3.13+, dataclasses, enums, PyYAML via existing loader, jsonschema via existing validator, pytest.

---

## File Map

Create:

```text
appV2.4/appv24/workflows/ir.py
appV2.4/appv24/workflows/capabilities.py
appV2.4/appv24/workflows/compiler.py
appV2.4/appv24/workflows/policy.py
appV2.4/appv24/workflows/ledger.py
appV2.4/appv24/workflows/simulator.py
appV2.4/tests/test_workflow_ir.py
appV2.4/tests/test_workflow_simulator.py
docs/appv24/workflow-ir-simulator.md
```

Modify:

```text
appV2.4/appv24/workflows/__init__.py
```

Do not modify:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
appV2.4/appv24/cli.py
appV2.4/appv24/tui/
appV2.4/appv24/profiles/
```

## Task 1: Add Compiler Tests And IR Types

**Files:**

- Create: `appV2.4/tests/test_workflow_ir.py`
- Create: `appV2.4/appv24/workflows/ir.py`

- [ ] **Step 1: Write failing compiler/IR tests**

Create `appV2.4/tests/test_workflow_ir.py`:

```python
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents, compile_workflow_package
from appv24.workflows.ir import StepKind
from appv24.workflows.loader import PackageDocuments, load_package_documents


def write_ir_package(root: Path) -> None:
    from test_workflow_packages import write_minimal_package

    write_minimal_package(root)


def replace_documents(
    documents: PackageDocuments,
    *,
    workflow: object | None = None,
    connectors: object | None = None,
    permissions: object | None = None,
) -> PackageDocuments:
    return PackageDocuments(
        root=documents.root,
        workflow=documents.workflow if workflow is None else workflow,
        connectors=documents.connectors if connectors is None else connectors,
        permissions=documents.permissions if permissions is None else permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_compile_workflow_package_builds_ir_for_valid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)

    result = compile_workflow_package(package)

    assert result.ok
    assert result.ir is not None
    assert result.ir.name == "dependency-security-review"
    assert result.ir.entrypoint == "inspect_alerts"
    assert sorted(result.ir.steps) == ["done", "draft_issue", "inspect_alerts"]
    assert result.ir.steps["inspect_alerts"].kind is StepKind.ACTION
    assert result.ir.steps["draft_issue"].uses is not None
    assert result.ir.steps["draft_issue"].uses.connector_id == "github"
    assert result.ir.steps["draft_issue"].uses.action_id == "create_issue"
    assert result.ir.steps["draft_issue"].side_effect_class == "create"
    assert result.ir.steps["draft_issue"].approval == "human-review"
    assert result.ir.connectors["github"].actions["create_issue"].execution_mode == "brokered"


def test_compile_workflow_documents_normalizes_edges(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)

    result = compile_workflow_documents(documents)

    assert result.ok
    assert result.ir is not None
    assert result.ir.steps["inspect_alerts"].outbound == ("draft_issue",)
    assert result.ir.steps["draft_issue"].outbound == ("done",)
    assert result.ir.steps["done"].outbound == ()


def test_compile_reports_unknown_transition(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["next"] = "missing_step"

    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))

    assert not result.ok
    assert "unknown_transition" in issue_codes(result)
    assert result.ir is None


def test_compile_reports_unknown_connector_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["uses"] = "github.missing_action"

    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))

    assert not result.ok
    assert "unknown_action" in issue_codes(result)
    assert result.ir is None


def test_compile_reports_duplicate_step_ids(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][1]["id"] = "inspect_alerts"

    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))

    assert not result.ok
    assert "duplicate_step" in issue_codes(result)
    assert result.ir is None


def test_compile_reports_invalid_package_before_ir(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    (package / "workflow.yaml").write_text("version: appv24.workflow/v1\n", encoding="utf-8")

    result = compile_workflow_package(package)

    assert not result.ok
    assert "schema_required" in issue_codes(result)
    assert result.ir is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.compiler'
```

- [ ] **Step 3: Create `ir.py` with the typed dataclasses**

Create `appV2.4/appv24/workflows/ir.py`:

```python
"""Typed intermediate representation for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.errors import WorkflowIssue


class StepKind(Enum):
    ACTION = "action"
    DECISION = "decision"
    MANUAL_APPROVAL = "manual_approval"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class ConnectorRef:
    connector_id: str
    action_id: str

    @property
    def dotted(self) -> str:
        return f"{self.connector_id}.{self.action_id}"


@dataclass(frozen=True)
class ActionCapability:
    connector_id: str
    action_id: str
    adapter: str
    side_effect: bool
    side_effect_class: str | None
    execution_mode: str | None
    approval_required: bool | None
    dry_run_supported: bool | None
    idempotency_key_fields: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    required_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    raw: Mapping[str, Any]

    @property
    def dotted(self) -> str:
        return f"{self.connector_id}.{self.action_id}"


@dataclass(frozen=True)
class ConnectorCapability:
    connector_id: str
    adapter: str
    actions: Mapping[str, ActionCapability]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ApprovalRequirement:
    approval_id: str
    required_for: tuple[str, ...]
    mode: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowTrigger:
    trigger_id: str
    trigger_type: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowDesignContract:
    intent: str
    trigger: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    assumptions: tuple[str, ...]
    side_effects: tuple[str, ...]
    approval_gates: tuple[str, ...]
    failure_modes: tuple[str, ...]
    rollback: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    kind: StepKind
    uses: ConnectorRef | None
    side_effect_class: str | None
    approval: str | None
    next_step: str | None
    branches: Mapping[str, str]
    outbound: tuple[str, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowIR:
    root: Path
    name: str
    version: str
    entrypoint: str
    triggers: tuple[WorkflowTrigger, ...]
    steps: Mapping[str, WorkflowStep]
    connectors: Mapping[str, ConnectorCapability]
    approvals: Mapping[str, ApprovalRequirement]
    design: WorkflowDesignContract


@dataclass(frozen=True)
class WorkflowCompileResult:
    root: Path
    ir: WorkflowIR | None
    issues: tuple[WorkflowIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues and self.ir is not None
```

- [ ] **Step 4: Run tests and verify import advances**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.compiler'
```

- [ ] **Step 5: Commit IR tests and dataclasses**

Run:

```bash
git add appV2.4/tests/test_workflow_ir.py appV2.4/appv24/workflows/ir.py
git commit -m "test(appv24): specify workflow IR compiler"
```

## Task 2: Add Capabilities And Compiler

**Files:**

- Create: `appV2.4/appv24/workflows/capabilities.py`
- Create: `appV2.4/appv24/workflows/compiler.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`

- [ ] **Step 1: Create capability extraction**

Create `appV2.4/appv24/workflows/capabilities.py`:

```python
"""Capability extraction for appv24 workflow IR."""

from __future__ import annotations

from typing import Any, Mapping

from appv24.workflows.ir import ActionCapability, ConnectorCapability


def build_connector_capabilities(
    connectors_document: Mapping[str, Any],
    permissions_document: Mapping[str, Any],
) -> dict[str, ConnectorCapability]:
    granted_scopes = _permission_scopes(permissions_document)
    capabilities: dict[str, ConnectorCapability] = {}
    for connector in connectors_document.get("connectors", []):
        if not isinstance(connector, dict) or not isinstance(connector.get("id"), str):
            continue
        connector_id = str(connector["id"])
        adapter = str(connector.get("adapter", "manual"))
        actions: dict[str, ActionCapability] = {}
        for action in connector.get("actions", []):
            if not isinstance(action, dict) or not isinstance(action.get("id"), str):
                continue
            action_id = str(action["id"])
            actions[action_id] = ActionCapability(
                connector_id=connector_id,
                action_id=action_id,
                adapter=adapter,
                side_effect=bool(action.get("side_effect")),
                side_effect_class=_optional_str(action.get("side_effect_class")),
                execution_mode=_optional_str(action.get("execution_mode")),
                approval_required=_optional_bool(action.get("approval_required")),
                dry_run_supported=_optional_bool(action.get("dry_run_supported")),
                idempotency_key_fields=tuple(_string_list(_mapping(action.get("idempotency")).get("key_fields"))),
                allowed_outputs=tuple(_string_list(_mapping(action.get("outputs")).get("allowed"))),
                required_scopes=tuple(_string_list(action.get("required_scopes"))),
                granted_scopes=tuple(sorted(granted_scopes.get(connector_id, set()))),
                raw=action,
            )
        capabilities[connector_id] = ConnectorCapability(
            connector_id=connector_id,
            adapter=adapter,
            actions=actions,
            raw=connector,
        )
    return capabilities


def _permission_scopes(permissions_document: Mapping[str, Any]) -> dict[str, set[str]]:
    scopes: dict[str, set[str]] = {}
    for permission in permissions_document.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        scopes[str(permission["connector"])] = {
            str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str)
        }
    return scopes


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
```

- [ ] **Step 2: Create compiler implementation**

Create `appV2.4/appv24/workflows/compiler.py`:

```python
"""Compile appv24 workflow package documents into typed IR."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.capabilities import build_connector_capabilities
from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.ir import (
    ApprovalRequirement,
    ConnectorRef,
    StepKind,
    WorkflowCompileResult,
    WorkflowDesignContract,
    WorkflowIR,
    WorkflowStep,
    WorkflowTrigger,
)
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.validator import validate_workflow_documents, validate_workflow_package


MAX_COMPILE_STEPS = 100


def compile_workflow_package(path: str | Path) -> WorkflowCompileResult:
    validation = validate_workflow_package(path)
    if not validation.ok:
        return WorkflowCompileResult(root=validation.root, ir=None, issues=validation.issues)
    documents = load_package_documents(path)
    return compile_workflow_documents(documents)


def compile_workflow_documents(documents: PackageDocuments) -> WorkflowCompileResult:
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return WorkflowCompileResult(root=documents.root, ir=None, issues=validation.issues)

    issues: list[WorkflowIssue] = []
    workflow = documents.workflow
    raw_steps = [step for step in workflow.get("steps", []) if isinstance(step, dict)]
    if len(raw_steps) > MAX_COMPILE_STEPS:
        issues.append(WorkflowIssue("step_limit_exceeded", "workflow.yaml", ("steps",), "workflow has more than 100 steps"))

    capabilities = build_connector_capabilities(documents.connectors, documents.permissions)
    approvals = _build_approvals(documents.permissions)
    steps: dict[str, WorkflowStep] = {}
    seen: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        step_id = str(raw_step.get("id", ""))
        if step_id in seen:
            issues.append(WorkflowIssue("duplicate_step", "workflow.yaml", ("steps", index, "id"), f"duplicate step id: {step_id}"))
            continue
        seen.add(step_id)
        step = _compile_step(index, raw_step, capabilities, approvals)
        issues.extend(step[1])
        if step[0] is not None:
            steps[step_id] = step[0]

    known_steps = set(steps)
    entrypoint = str(workflow.get("entrypoint", ""))
    if entrypoint not in known_steps:
        issues.append(WorkflowIssue("unknown_entrypoint", "workflow.yaml", ("entrypoint",), f"entrypoint does not exist: {entrypoint}"))
    for step in steps.values():
        for target in step.outbound:
            if target not in known_steps:
                issues.append(WorkflowIssue("unknown_transition", "workflow.yaml", ("steps", step.step_id), f"step {step.step_id} points to {target}"))

    if issues:
        return WorkflowCompileResult(root=documents.root, ir=None, issues=sorted_issues(issues))

    ir = WorkflowIR(
        root=documents.root,
        name=str(workflow["name"]),
        version=str(workflow["version"]),
        entrypoint=entrypoint,
        triggers=_build_triggers(workflow),
        steps=steps,
        connectors=capabilities,
        approvals=approvals,
        design=_build_design(workflow),
    )
    return WorkflowCompileResult(root=documents.root, ir=ir, issues=())


def _compile_step(
    index: int,
    raw_step: Mapping[str, Any],
    capabilities: Mapping[str, object],
    approvals: Mapping[str, ApprovalRequirement],
) -> tuple[WorkflowStep | None, list[WorkflowIssue]]:
    issues: list[WorkflowIssue] = []
    step_id = str(raw_step.get("id", ""))
    kind = _step_kind(raw_step)
    uses = _connector_ref(raw_step.get("uses"))
    if uses is not None:
        connector = capabilities.get(uses.connector_id)
        actions = getattr(connector, "actions", {}) if connector is not None else {}
        if connector is None:
            issues.append(WorkflowIssue("unknown_connector", "workflow.yaml", ("steps", index, "uses"), f"unknown connector: {uses.connector_id}"))
        elif uses.action_id not in actions:
            issues.append(WorkflowIssue("unknown_action", "workflow.yaml", ("steps", index, "uses"), f"unknown action: {uses.dotted}"))
    approval = raw_step.get("approval")
    approval_id = approval if isinstance(approval, str) else None
    if approval_id is not None and approval_id not in approvals:
        issues.append(WorkflowIssue("unknown_approval", "workflow.yaml", ("steps", index, "approval"), f"unknown approval: {approval_id}"))
    next_step = raw_step.get("next") if isinstance(raw_step.get("next"), str) else None
    branches = {
        str(key): str(value)
        for key, value in raw_step.get("branches", {}).items()
        if isinstance(raw_step.get("branches"), dict) and isinstance(value, str)
    }
    outbound = tuple(target for target in (next_step, *branches.values()) if isinstance(target, str))
    return (
        WorkflowStep(
            step_id=step_id,
            kind=kind,
            uses=uses,
            side_effect_class=raw_step.get("side_effect_class") if isinstance(raw_step.get("side_effect_class"), str) else None,
            approval=approval_id,
            next_step=next_step,
            branches=branches,
            outbound=outbound,
            raw=raw_step,
        ),
        issues,
    )


def _step_kind(raw_step: Mapping[str, Any]) -> StepKind:
    value = raw_step.get("type")
    if value == "decision":
        return StepKind.DECISION
    if value == "manual_approval":
        return StepKind.MANUAL_APPROVAL
    if value == "terminal":
        return StepKind.TERMINAL
    return StepKind.ACTION


def _connector_ref(value: object) -> ConnectorRef | None:
    if not isinstance(value, str) or "." not in value:
        return None
    connector_id, action_id = value.split(".", 1)
    return ConnectorRef(connector_id=connector_id, action_id=action_id)


def _build_approvals(permissions: Mapping[str, Any]) -> dict[str, ApprovalRequirement]:
    approvals: dict[str, ApprovalRequirement] = {}
    for approval in permissions.get("approvals", []):
        if not isinstance(approval, dict) or not isinstance(approval.get("id"), str):
            continue
        approval_id = str(approval["id"])
        approvals[approval_id] = ApprovalRequirement(
            approval_id=approval_id,
            mode=str(approval.get("mode", "")),
            required_for=tuple(str(item) for item in approval.get("required_for", []) if isinstance(item, str)),
            raw=approval,
        )
    return approvals


def _build_triggers(workflow: Mapping[str, Any]) -> tuple[WorkflowTrigger, ...]:
    return tuple(
        WorkflowTrigger(trigger_id=str(trigger["id"]), trigger_type=str(trigger["type"]), raw=trigger)
        for trigger in workflow.get("triggers", [])
        if isinstance(trigger, dict) and isinstance(trigger.get("id"), str) and isinstance(trigger.get("type"), str)
    )


def _build_design(workflow: Mapping[str, Any]) -> WorkflowDesignContract:
    design = workflow.get("design", {})
    design_map = design if isinstance(design, dict) else {}
    return WorkflowDesignContract(
        intent=str(design_map.get("intent", "")),
        trigger=str(design_map.get("trigger", "")),
        inputs=tuple(_strings(design_map.get("inputs"))),
        outputs=tuple(_strings(design_map.get("outputs"))),
        assumptions=tuple(_strings(design_map.get("assumptions"))),
        side_effects=tuple(_strings(design_map.get("side_effects"))),
        approval_gates=tuple(_strings(design_map.get("approval_gates"))),
        failure_modes=tuple(_strings(design_map.get("failure_modes"))),
        rollback=tuple(_strings(design_map.get("rollback"))),
    )


def _strings(value: object) -> list[str]:
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []
```

- [ ] **Step 3: Export the compiler API**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.compiler import compile_workflow_documents, compile_workflow_package
from appv24.workflows.ir import WorkflowCompileResult, WorkflowIR, WorkflowStep
```

Add to `__all__`:

```python
"WorkflowCompileResult",
"WorkflowIR",
"WorkflowStep",
"compile_workflow_documents",
"compile_workflow_package",
```

- [ ] **Step 4: Run compiler tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py -q
```

Expected:

```text
7 passed
```

- [ ] **Step 5: Commit compiler**

Run:

```bash
git add appV2.4/appv24/workflows/ir.py appV2.4/appv24/workflows/capabilities.py appV2.4/appv24/workflows/compiler.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_ir.py
git commit -m "feat(appv24): compile workflow packages to IR"
```

## Task 3: Add Policy Report

**Files:**

- Modify: `appV2.4/tests/test_workflow_ir.py`
- Create: `appV2.4/appv24/workflows/policy.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`

- [ ] **Step 1: Add failing policy tests**

Append to `appV2.4/tests/test_workflow_ir.py`:

```python
from dataclasses import replace

from appv24.workflows.policy import PolicySeverity, evaluate_policy


def policy_codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def compile_valid_ir(tmp_path: Path):
    package = tmp_path / "package"
    write_ir_package(package)
    result = compile_workflow_package(package)
    assert result.ok
    assert result.ir is not None
    return result.ir


def test_policy_accepts_valid_brokered_mutation(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)

    report = evaluate_policy(ir)

    assert report.ok
    assert report.issues == ()


def test_policy_blocks_mutating_step_without_approval(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)
    steps = dict(ir.steps)
    steps["draft_issue"] = replace(steps["draft_issue"], approval=None)
    ir = replace(ir, steps=steps)

    report = evaluate_policy(ir)

    assert not report.ok
    assert "policy_missing_approval" in policy_codes(report)


def test_policy_blocks_missing_required_scope(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)
    connector = ir.connectors["github"]
    actions = dict(connector.actions)
    actions["create_issue"] = replace(actions["create_issue"], granted_scopes=("dependabot:read",))
    connectors = dict(ir.connectors)
    connectors["github"] = replace(connector, actions=actions)
    ir = replace(ir, connectors=connectors)

    report = evaluate_policy(ir)

    assert not report.ok
    assert "policy_missing_scope" in policy_codes(report)


def test_policy_blocks_output_wildcard(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)
    connector = ir.connectors["github"]
    actions = dict(connector.actions)
    actions["create_issue"] = replace(actions["create_issue"], allowed_outputs=("*",))
    connectors = dict(ir.connectors)
    connectors["github"] = replace(connector, actions=actions)
    ir = replace(ir, connectors=connectors)

    report = evaluate_policy(ir)

    assert not report.ok
    assert "policy_wildcard_outputs" in policy_codes(report)
```

- [ ] **Step 2: Run tests to verify policy module is missing**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.policy'
```

- [ ] **Step 3: Implement policy module**

Create `appV2.4/appv24/workflows/policy.py`:

```python
"""Static policy evaluation for compiled appv24 workflow IR."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from appv24.workflows.ir import WorkflowIR


class PolicySeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class PolicyIssue:
    code: str
    message: str
    severity: PolicySeverity = PolicySeverity.ERROR
    step_id: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class PolicyReport:
    issues: tuple[PolicyIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue.severity is PolicySeverity.ERROR for issue in self.issues)


def evaluate_policy(ir: WorkflowIR) -> PolicyReport:
    issues: list[PolicyIssue] = []
    for step in ir.steps.values():
        if step.uses is None:
            continue
        connector = ir.connectors.get(step.uses.connector_id)
        action = connector.actions.get(step.uses.action_id) if connector is not None else None
        if action is None:
            continue
        if not action.side_effect:
            continue
        if not step.approval:
            issues.append(PolicyIssue("policy_missing_approval", f"{step.step_id} requires approval", step_id=step.step_id))
        elif step.approval not in ir.approvals:
            issues.append(PolicyIssue("policy_unknown_approval", f"{step.step_id} references unknown approval {step.approval}", step_id=step.step_id))
        elif step.step_id not in ir.approvals[step.approval].required_for:
            issues.append(PolicyIssue("policy_approval_required_for", f"{step.approval} must include {step.step_id}", step_id=step.step_id))
        for scope in action.required_scopes:
            if scope not in action.granted_scopes and "*" not in action.granted_scopes:
                issues.append(PolicyIssue("policy_missing_scope", f"{action.dotted} requires scope {scope}", step_id=step.step_id, source=action.dotted))
        if action.execution_mode == "brokered":
            if action.dry_run_supported is not True:
                issues.append(PolicyIssue("policy_missing_dry_run", f"{action.dotted} requires dry_run_supported", step_id=step.step_id, source=action.dotted))
            if "*" in action.allowed_outputs:
                issues.append(PolicyIssue("policy_wildcard_outputs", f"{action.dotted} must not allow wildcard outputs", step_id=step.step_id, source=action.dotted))
            if not action.allowed_outputs:
                issues.append(PolicyIssue("policy_missing_outputs", f"{action.dotted} requires outputs.allowed", step_id=step.step_id, source=action.dotted))
            if action.side_effect_class in {"create", "send", "publish", "provision"} and not action.idempotency_key_fields:
                issues.append(PolicyIssue("policy_missing_idempotency", f"{action.dotted} requires idempotency key fields", step_id=step.step_id, source=action.dotted))
        if action.execution_mode == "design_only":
            issues.append(PolicyIssue("policy_design_only_used", f"{step.step_id} must not call design-only action {action.dotted}", step_id=step.step_id, source=action.dotted))
    return PolicyReport(issues=tuple(sorted(issues, key=lambda issue: (issue.code, issue.step_id or "", issue.message))))
```

- [ ] **Step 4: Export policy API**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.policy import PolicyIssue, PolicyReport, PolicySeverity, evaluate_policy
```

Add to `__all__`:

```python
"PolicyIssue",
"PolicyReport",
"PolicySeverity",
"evaluate_policy",
```

- [ ] **Step 5: Run policy tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py -q
```

Expected:

```text
11 passed
```

- [ ] **Step 6: Commit policy**

Run:

```bash
git add appV2.4/appv24/workflows/policy.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_ir.py
git commit -m "feat(appv24): add workflow IR policy report"
```

## Task 4: Add Simulator And Ledger

**Files:**

- Create: `appV2.4/tests/test_workflow_simulator.py`
- Create: `appV2.4/appv24/workflows/ledger.py`
- Create: `appV2.4/appv24/workflows/simulator.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`

- [ ] **Step 1: Write failing simulator tests**

Create `appV2.4/tests/test_workflow_simulator.py`:

```python
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.simulator import simulate_workflow_fixture


def write_branching_package(root: Path) -> None:
    from test_workflow_packages import write_minimal_package

    write_minimal_package(root)
    workflow = (root / "workflow.yaml").read_text(encoding="utf-8")
    workflow = workflow.replace(
        "entrypoint: inspect_alerts\nsteps:\n  - id: inspect_alerts\n    uses: github.list_dependabot_alerts\n    next: draft_issue",
        "entrypoint: inspect_alerts\nsteps:\n  - id: inspect_alerts\n    uses: github.list_dependabot_alerts\n    next: decide_path\n  - id: decide_path\n    type: decision\n    branches:\n      approved: draft_issue\n      rejected: done",
    )
    (root / "workflow.yaml").write_text(workflow, encoding="utf-8")
    fixtures = root / "dry_run_fixtures"
    (fixtures / "happy_path.yaml").write_text(
        "decision:\n  decide_path: approved\nmock_responses:\n  github.list_dependabot_alerts:\n    alerts: []\n  github.create_issue:\n    issue_number: 12\n    issue_url: https://example.invalid/12\n",
        encoding="utf-8",
    )
    (fixtures / "rejection_path.yaml").write_text(
        "decision:\n  decide_path: rejected\nmock_responses:\n  github.list_dependabot_alerts:\n    alerts: []\n",
        encoding="utf-8",
    )


def replace_documents(
    documents: PackageDocuments,
    *,
    workflow: object | None = None,
) -> PackageDocuments:
    return PackageDocuments(
        root=documents.root,
        workflow=documents.workflow if workflow is None else workflow,
        connectors=documents.connectors,
        permissions=documents.permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )


def compile_branching_ir(tmp_path: Path):
    package = tmp_path / "package"
    write_branching_package(package)
    documents = load_package_documents(package)
    result = compile_workflow_documents(documents)
    assert result.ok
    assert result.ir is not None
    return result.ir, documents


def test_simulator_runs_happy_path_fixture(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)

    ledger = simulate_workflow_fixture(ir, "happy_path.yaml", documents.dry_run_fixtures["happy_path.yaml"])

    assert ledger.status == "passed"
    assert [trace.step_id for trace in ledger.trace] == ["inspect_alerts", "decide_path", "draft_issue", "done"]
    assert ledger.terminal_step == "done"
    assert ledger.simulated_side_effects == ("draft_issue",)


def test_simulator_runs_rejection_path_fixture_without_side_effect(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)

    ledger = simulate_workflow_fixture(ir, "rejection_path.yaml", documents.dry_run_fixtures["rejection_path.yaml"])

    assert ledger.status == "passed"
    assert [trace.step_id for trace in ledger.trace] == ["inspect_alerts", "decide_path", "done"]
    assert ledger.simulated_side_effects == ()


def test_simulator_blocks_unknown_decision_value(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)
    fixture = deepcopy(documents.dry_run_fixtures["happy_path.yaml"])
    fixture["decision"]["decide_path"] = "unknown"

    ledger = simulate_workflow_fixture(ir, "broken.yaml", fixture)

    assert ledger.status == "blocked"
    assert ledger.terminal_step is None
    assert any("no branch matched" in issue for issue in ledger.issues)


def test_simulator_detects_cycle(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][-1]["next"] = "inspect_alerts"
    workflow["steps"][-1]["type"] = "action"
    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))
    assert result.ok
    assert result.ir is not None

    ledger = simulate_workflow_fixture(result.ir, "happy_path.yaml", documents.dry_run_fixtures["happy_path.yaml"], max_transitions=5)

    assert ledger.status == "blocked"
    assert any("transition budget exceeded" in issue for issue in ledger.issues)
```

- [ ] **Step 2: Run tests to verify simulator module is missing**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_simulator.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.simulator'
```

- [ ] **Step 3: Implement ledger dataclasses**

Create `appV2.4/appv24/workflows/ledger.py`:

```python
"""Simulation ledger types for appv24 workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StepTrace:
    step_id: str
    kind: str
    status: str
    selected_branch: str | None = None
    connector_action: str | None = None
    side_effect_class: str | None = None
    approval_id: str | None = None
    simulated_outputs: Mapping[str, Any] | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class SimulationLedger:
    workflow_name: str
    fixture_name: str
    status: str
    trace: tuple[StepTrace, ...]
    terminal_step: str | None
    simulated_side_effects: tuple[str, ...]
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 4: Implement simulator**

Create `appV2.4/appv24/workflows/simulator.py`:

```python
"""Deterministic dry-run simulator for compiled appv24 workflow IR."""

from __future__ import annotations

from typing import Any, Mapping

from appv24.workflows.ir import StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.ledger import SimulationLedger, StepTrace
from appv24.workflows.policy import evaluate_policy


def simulate_workflow_fixture(
    ir: WorkflowIR,
    fixture_name: str,
    fixture: Mapping[str, Any],
    *,
    max_transitions: int = 200,
) -> SimulationLedger:
    policy = evaluate_policy(ir)
    if not policy.ok:
        return SimulationLedger(
            workflow_name=ir.name,
            fixture_name=fixture_name,
            status="blocked",
            trace=(),
            terminal_step=None,
            simulated_side_effects=(),
            issues=tuple(issue.message for issue in policy.issues),
        )

    trace: list[StepTrace] = []
    side_effects: list[str] = []
    current = ir.entrypoint
    issues: list[str] = []
    transitions = 0

    while current:
        transitions += 1
        if transitions > max_transitions:
            issues.append("transition budget exceeded")
            return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
        outputs = _mock_outputs(step, fixture)
        selected_branch = None
        next_step = step.next_step
        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                issues.append(f"no branch matched for {step.step_id}")
                trace.append(_trace(step, "blocked", selected_branch, outputs, ("no branch matched",)))
                return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
        if _is_side_effect_step(ir, step):
            side_effects.append(step.step_id)
        status = "terminal" if step.kind is StepKind.TERMINAL else "completed"
        trace.append(_trace(step, status, selected_branch, outputs, ()))
        if step.kind is StepKind.TERMINAL:
            return _ledger(ir, fixture_name, "passed", trace, step.step_id, side_effects, issues)
        current = next_step

    issues.append("workflow stopped without terminal step")
    return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)


def _select_branch(step: WorkflowStep, fixture: Mapping[str, Any]) -> tuple[str | None, str | None]:
    decisions = fixture.get("decision", {})
    if isinstance(decisions, dict):
        selected = decisions.get(step.step_id)
        if isinstance(selected, str) and selected in step.branches:
            return selected, step.branches[selected]
    if len(step.branches) == 1:
        selected, target = next(iter(step.branches.items()))
        return selected, target
    return None, None


def _mock_outputs(step: WorkflowStep, fixture: Mapping[str, Any]) -> Mapping[str, Any]:
    mocks = fixture.get("mock_responses", {})
    if not isinstance(mocks, dict) or step.uses is None:
        return {}
    value = mocks.get(step.uses.dotted, {})
    return value if isinstance(value, dict) else {}


def _is_side_effect_step(ir: WorkflowIR, step: WorkflowStep) -> bool:
    if step.uses is None:
        return False
    connector = ir.connectors.get(step.uses.connector_id)
    action = connector.actions.get(step.uses.action_id) if connector is not None else None
    return bool(action and action.side_effect)


def _trace(
    step: WorkflowStep,
    status: str,
    selected_branch: str | None,
    outputs: Mapping[str, Any],
    issues: tuple[str, ...],
) -> StepTrace:
    return StepTrace(
        step_id=step.step_id,
        kind=step.kind.value,
        status=status,
        selected_branch=selected_branch,
        connector_action=step.uses.dotted if step.uses is not None else None,
        side_effect_class=step.side_effect_class,
        approval_id=step.approval,
        simulated_outputs=outputs,
        issues=issues,
    )


def _ledger(
    ir: WorkflowIR,
    fixture_name: str,
    status: str,
    trace: list[StepTrace],
    terminal_step: str | None,
    side_effects: list[str],
    issues: list[str],
) -> SimulationLedger:
    return SimulationLedger(
        workflow_name=ir.name,
        fixture_name=fixture_name,
        status=status,
        trace=tuple(trace),
        terminal_step=terminal_step,
        simulated_side_effects=tuple(side_effects),
        issues=tuple(issues),
    )
```

- [ ] **Step 5: Export simulator API**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.ledger import SimulationLedger, StepTrace
from appv24.workflows.simulator import simulate_workflow_fixture
```

Add to `__all__`:

```python
"SimulationLedger",
"StepTrace",
"simulate_workflow_fixture",
```

- [ ] **Step 6: Run simulator tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_simulator.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 7: Run workflow IR and simulator tests together**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py appV2.4/tests/test_workflow_simulator.py -q
```

Expected:

```text
15 passed
```

- [ ] **Step 8: Commit simulator**

Run:

```bash
git add appV2.4/appv24/workflows/ledger.py appV2.4/appv24/workflows/simulator.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_simulator.py
git commit -m "feat(appv24): simulate workflow fixtures"
```

## Task 5: Add Documentation And Full Verification

**Files:**

- Create: `docs/appv24/workflow-ir-simulator.md`

- [ ] **Step 1: Write workflow IR simulator docs**

Create `docs/appv24/workflow-ir-simulator.md`:

```markdown
# appv24 Workflow IR And Simulator

appv24 workflow packages can be compiled into a typed internal IR and simulated against dry-run fixtures.

This layer is passive. It does not execute connectors, call APIs, send messages, provision cloud resources, run shell commands, or read secrets.

## Pipeline

```text
workflow package
  -> validate_workflow_package
  -> compile_workflow_package
  -> evaluate_policy
  -> simulate_workflow_fixture
  -> SimulationLedger
```

## Public API

```python
from appv24.workflows import (
    compile_workflow_package,
    evaluate_policy,
    simulate_workflow_fixture,
)
from appv24.workflows.loader import load_package_documents

documents = load_package_documents("path/to/package")
compiled = compile_workflow_package("path/to/package")
if compiled.ok and compiled.ir is not None:
    report = evaluate_policy(compiled.ir)
    fixture = documents.dry_run_fixtures["happy_path.yaml"]
    ledger = simulate_workflow_fixture(compiled.ir, "happy_path.yaml", fixture)
```

## Boundaries

The simulator uses fixture data only. Connector actions are recorded as simulated side effects in the ledger, never executed.

## Ledger

The ledger reports:

- workflow name
- fixture name
- status
- ordered step trace
- terminal step
- simulated side-effect step ids
- blocking issues
```

- [ ] **Step 2: Run focused verification**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py appV2.4/tests/test_workflow_simulator.py appV2.4/tests/test_workflow_packages.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 3: Run broader workflow verification**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_ir.py appV2.4/tests/test_workflow_simulator.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 4: Run full appV2.4 tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Check red/yellow-zone impact**

Run:

```bash
git diff --name-only HEAD~4..HEAD
```

Expected changed runtime paths:

```text
appV2.4/appv24/workflows/__init__.py
appV2.4/appv24/workflows/capabilities.py
appV2.4/appv24/workflows/compiler.py
appV2.4/appv24/workflows/ir.py
appV2.4/appv24/workflows/ledger.py
appV2.4/appv24/workflows/policy.py
appV2.4/appv24/workflows/simulator.py
```

No changed paths should match:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
appV2.4/appv24/cli.py
appV2.4/appv24/tui/
appV2.4/appv24/profiles/
```

- [ ] **Step 6: Commit docs and verification-ready state**

Run:

```bash
git add docs/appv24/workflow-ir-simulator.md
git commit -m "docs(appv24): document workflow IR simulator"
```

## Execution Notes

- Do not add a CLI command in this plan.
- Do not add TUI output in this plan.
- Do not execute connectors.
- Do not read secrets.
- Do not weaken existing validator tests.
- If an existing semantic validator blocks a test fixture, fix the fixture unless the validator is clearly inconsistent with the spec.
- If any implementation step appears to require a red-zone file, stop and redesign the step inside `appV2.4/appv24/workflows/`.

## Final Verification Checklist

Before reporting completion:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git status --short
git diff --name-only HEAD~5..HEAD
```

Report:

```text
test result
commit ids
red-zone touched: yes/no
yellow-zone touched: yes/no
new public APIs
```
