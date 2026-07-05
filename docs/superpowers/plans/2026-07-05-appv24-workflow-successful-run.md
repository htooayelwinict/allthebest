# appv24 Workflow Successful Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the next appv24 workflow layer so a saved workflow package can complete a real local run with approval handling, brokered local artifacts, append-only events, trace output, and a final report.

**Architecture:** Add a green-zone workflow runner that reuses package loading, validation, compilation, policy, fixtures, local broker providers, and run storage. Local mode executes the graph and writes only local artifacts. Live modes remain blocked or preflight-only until a later plan.

**Tech Stack:** Python dataclasses, pathlib, json/jsonl, existing appv24 workflow modules, pytest, no new runtime dependencies.

---

## File Structure

Create:

- `appV2.4/appv24/workflows/runner.py`
  Owns `run_workflow()`, mode dispatch for `LOCAL`, and conversion from ledger/trace to `WorkflowRunReport`.

- `appV2.4/tests/test_workflow_runner.py`
  End-to-end local run tests.

Modify:

- `appV2.4/appv24/workflows/execution.py`
  Add `approve_all: bool = False` to `WorkflowRunRequest` for local approval policy.

- `appV2.4/appv24/workflows/run_store.py`
  Add helpers for `artifact_root()`, `write_report()`, `write_trace()`, and `write_approvals()`.

- `appV2.4/appv24/workflows/local_artifacts.py`
  Add `telegram_send_message()` and `provider_map()`.

- `appV2.4/appv24/workflows/__init__.py`
  Export `run_workflow` only after tests pass.

- `appV2.4/tests/test_workflow_execution.py`
  Cover any new `WorkflowRunRequest` fields.

- `appV2.4/tests/test_workflow_run_store.py`
  Cover report/trace/approval persistence.

- `appV2.4/tests/test_workflow_local_artifacts.py`
  Cover Telegram local artifact provider and provider map.

Optional yellow-zone later:

- `appV2.4/appv24/tui/interactive_mode.py`
  Add `/workflow-run` command only after green API is proven and Lewis explicitly approves the TUI phase.

Do not modify:

- `appV2.4/appv24/agent/`
- `appV2.4/appv24/ai/stream.py`
- `appV2.4/appv24/ai/validation.py`
- `appV2.4/appv24/compaction/`
- `appV2.4/appv24/coding_agent/session_store.py`
- `appV2.4/appv24/app.py`

---

### Task 1: Add Runner Contract Tests

**Files:**

- Create: `appV2.4/tests/test_workflow_runner.py`
- Use existing: `appV2.4/appv24/workflows/examples.py`
- Use existing: `appV2.4/appv24/workflows/execution.py`

- [ ] **Step 1: Write failing local happy-path test**

Create `appV2.4/tests/test_workflow_runner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.runner import run_workflow


def _write_package(root: Path, example_name: str) -> Path:
    example = golden_workflow_examples()[example_name]
    package = root / example_name
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(yaml.safe_dump(dict(example.documents.workflow), sort_keys=False), encoding="utf-8")
    (package / "connectors.yaml").write_text(yaml.safe_dump(dict(example.documents.connectors), sort_keys=False), encoding="utf-8")
    (package / "permissions.yaml").write_text(yaml.safe_dump(dict(example.documents.permissions), sort_keys=False), encoding="utf-8")
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    for name, fixture in example.documents.dry_run_fixtures.items():
        (fixtures / name).write_text(yaml.safe_dump(dict(fixture), sort_keys=False), encoding="utf-8")
    if example.documents.rollback_text is not None:
        (package / "rollback.md").write_text(example.documents.rollback_text, encoding="utf-8")
    return package


def test_local_run_happy_path_writes_report_trace_events_and_artifacts(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert [step.step_id for step in report.step_trace] == [
        "check_authorized_customer",
        "create_ticket_draft",
        "notify_admin",
        "done",
    ]
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "trace.json").is_file()
    assert (run_dir / "approvals.json").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert sorted(path.name for path in (run_dir / "artifacts").glob("*.json")) == [
        "local-telegram-notify_admin.json",
        "local-ticket-create_ticket_draft.json",
    ]

    saved_report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert saved_report["status"] == "passed"
    assert saved_report["terminal_step"] == "done"
```

- [ ] **Step 2: Run the new test and verify it fails because runner is missing**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_runner.py::test_local_run_happy_path_writes_report_trace_events_and_artifacts -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.runner'
```

- [ ] **Step 3: Write failing rejection-path test**

Append to `appV2.4/tests/test_workflow_runner.py`:

```python
def test_local_run_rejection_path_reaches_terminal_without_artifacts(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="rejection_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.terminal_step == "rejected"
    assert [step.step_id for step in report.step_trace] == ["check_authorized_customer", "rejected"]
    assert list((run_dir / "artifacts").glob("*.json")) == []
```

- [ ] **Step 4: Write failing missing-approval test**

Append:

```python
def test_local_run_without_approval_blocks_before_broker_action(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=False,
        )
    )

    run_dir = Path(report.events[0].details["run_dir"])
    assert report.status == "blocked"
    assert report.terminal_step is None
    assert "approval required for create_ticket_draft" in report.issues
    assert list((run_dir / "artifacts").glob("*.json")) == []
    assert any(event.event_type == "approval_requested" for event in report.events)
    assert not any(event.event_type == "broker_action_started" for event in report.events)
```

---

### Task 2: Add Run Store Persistence Helpers

**Files:**

- Modify: `appV2.4/appv24/workflows/run_store.py`
- Modify: `appV2.4/tests/test_workflow_run_store.py`

- [ ] **Step 1: Write failing run-store persistence test**

Append to `appV2.4/tests/test_workflow_run_store.py`:

```python
from appv24.workflows.ledger import StepTrace


def test_run_store_writes_report_trace_approvals_and_artifact_root(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")

    artifact_root = store.artifact_root(run_id)
    store.write_trace(run_id, (StepTrace(step_id="done", kind="terminal", status="terminal"),))
    store.write_approvals(run_id, [{"step_id": "notify_admin", "status": "approved"}])
    store.write_report(
        run_id,
        {
            "run_id": run_id,
            "workflow_name": "telegram_support_ticket",
            "status": "passed",
            "terminal_step": "done",
        },
    )

    assert artifact_root == tmp_path / run_id / "artifacts"
    assert artifact_root.is_dir()
    assert (tmp_path / run_id / "trace.json").is_file()
    assert (tmp_path / run_id / "approvals.json").is_file()
    assert (tmp_path / run_id / "report.json").is_file()
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_run_store.py::test_run_store_writes_report_trace_approvals_and_artifact_root -q
```

Expected:

```text
AttributeError: 'WorkflowRunStore' object has no attribute 'artifact_root'
```

- [ ] **Step 3: Implement store helpers**

Modify `appV2.4/appv24/workflows/run_store.py`:

```python
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping


def _json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    return value
```

Add methods inside `WorkflowRunStore`:

```python
    def run_dir(self, run_id: str) -> Path:
        run_dir = self.root / run_id
        if not run_dir.is_dir():
            raise ValueError(f"unknown workflow run: {run_id}")
        return run_dir

    def artifact_root(self, run_id: str) -> Path:
        artifact_root = self.run_dir(run_id) / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        return artifact_root

    def write_trace(self, run_id: str, trace: object) -> None:
        payload = {"trace": _json_ready(trace)}
        self._write_json(run_id, "trace.json", payload)

    def write_approvals(self, run_id: str, approvals: object) -> None:
        self._write_json(run_id, "approvals.json", {"approvals": _json_ready(approvals)})

    def write_report(self, run_id: str, report: Mapping[str, object]) -> None:
        self._write_json(run_id, "report.json", report)

    def _write_json(self, run_id: str, filename: str, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(_json_ready(payload), sort_keys=True, indent=2)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow run event contains a raw secret-looking value")
        (self.run_dir(run_id) / filename).write_text(encoded + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run run-store tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_run_store.py -q
```

Expected:

```text
4 passed
```

---

### Task 3: Add Local Telegram Artifact Provider

**Files:**

- Modify: `appV2.4/appv24/workflows/local_artifacts.py`
- Modify: `appV2.4/tests/test_workflow_local_artifacts.py`

- [ ] **Step 1: Write failing Telegram provider test**

Append to `appV2.4/tests/test_workflow_local_artifacts.py`:

```python
def test_telegram_send_message_provider_writes_local_message_artifact(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.telegram_send_message(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={"with": {"text": "Ticket created"}},
            fixture={},
            allowed_outputs=("message_id", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["message_id"] == "local-message-notify_admin"
    artifact_path = Path(str(result.outputs["artifact_path"]))
    assert artifact_path.is_file()
    assert artifact_path.is_relative_to(tmp_path)
```

- [ ] **Step 2: Write failing provider-map test**

Append:

```python
def test_local_artifact_provider_map_exposes_supported_actions(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)

    assert sorted(providers.provider_map()) == [
        "gmail.create_draft",
        "telegram.send_message",
        "ticket.create_draft",
    ]
```

- [ ] **Step 3: Run local artifact tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_local_artifacts.py -q
```

Expected:

```text
AttributeError: 'LocalArtifactProviders' object has no attribute 'telegram_send_message'
```

- [ ] **Step 4: Implement Telegram provider and provider map**

Modify `appV2.4/appv24/workflows/local_artifacts.py`:

```python
    def telegram_send_message(self, request: BrokerRequest) -> BrokerResult:
        message_id = f"local-message-{request.step_id}"
        path = self.artifact_root / f"{message_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return _filtered(
            request,
            {
                "message_id": message_id,
                "artifact_path": str(path),
            },
        )

    def provider_map(self) -> dict[str, object]:
        return {
            "ticket.create_draft": self.ticket_create_draft,
            "gmail.create_draft": self.gmail_create_draft,
            "telegram.send_message": self.telegram_send_message,
        }
```

- [ ] **Step 5: Run local artifact tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_local_artifacts.py -q
```

Expected:

```text
4 passed
```

---

### Task 4: Implement Local Runner

**Files:**

- Create: `appV2.4/appv24/workflows/runner.py`
- Modify: `appV2.4/appv24/workflows/execution.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: `appV2.4/tests/test_workflow_runner.py`
- Test: `appV2.4/tests/test_workflow_execution.py`

- [ ] **Step 1: Add approve_all to request test**

Append to `appV2.4/tests/test_workflow_execution.py`:

```python
def test_workflow_run_request_can_carry_local_approval_policy() -> None:
    request = WorkflowRunRequest(
        package_path=Path("pkg"),
        fixture_name="happy_path.yaml",
        mode=WorkflowRunMode.LOCAL,
        approve_all=True,
    )

    assert request.approve_all is True
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_execution.py::test_workflow_run_request_can_carry_local_approval_policy -q
```

Expected:

```text
TypeError: WorkflowRunRequest.__init__() got an unexpected keyword argument 'approve_all'
```

- [ ] **Step 3: Add request field**

Modify `WorkflowRunRequest` in `appV2.4/appv24/workflows/execution.py`:

```python
    approve_all: bool = False
```

- [ ] **Step 4: Create runner implementation**

Create `appV2.4/appv24/workflows/runner.py`:

```python
"""Workflow runner for controlled appv24 workflow execution."""

from __future__ import annotations

from itertools import count
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.broker import BrokerRequest, LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.execution import WorkflowRunEvent, WorkflowRunMode, WorkflowRunReport, WorkflowRunRequest
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.ir import StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.ledger import StepTrace
from appv24.workflows.loader import load_package_documents
from appv24.workflows.local_artifacts import LocalArtifactProviders
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS, evaluate_policy
from appv24.workflows.run_preview import WorkflowApprovalPreview, WorkflowBrokeredActionPreview
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.validator import validate_workflow_documents


def run_workflow(
    request: WorkflowRunRequest,
    *,
    broker: LocalWorkflowBroker | None = None,
    max_transitions: int = 200,
) -> WorkflowRunReport:
    if request.mode is not WorkflowRunMode.LOCAL:
        return _blocked_report(
            request,
            workflow_name="",
            package_fingerprint="",
            issues=(f"workflow run mode is not implemented: {request.mode.value}",),
        )

    documents = load_package_documents(request.package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in policy.issues),
        )

    fixture_name = request.fixture_name or "happy_path.yaml"
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    run_root = request.run_root or (Path(request.package_path) / ".workflow-runs")
    store = WorkflowRunStore(run_root)
    run_id = store.create_run(workflow_name, package_fingerprint)
    run_dir = store.run_dir(run_id)
    events: list[WorkflowRunEvent] = []
    event_ids = count(1)

    def append(event_type: str, status: str, *, step: WorkflowStep | None = None, details: Mapping[str, Any] | None = None) -> None:
        event = WorkflowRunEvent(
            event_id=f"event-{next(event_ids)}",
            run_id=run_id,
            event_type=event_type,
            package_fingerprint=package_fingerprint,
            step_id=step.step_id if step is not None else None,
            connector_action=step.uses.dotted if step is not None and step.uses is not None else None,
            status=status,
            details=dict(details or {}),
        )
        store.append_event(event)
        events.append(event)

    append("run_created", "running", details={"fixture_name": fixture_name, "run_dir": str(run_dir)})

    local_broker = broker
    if local_broker is None:
        providers = LocalArtifactProviders(store.artifact_root(run_id))
        local_broker = LocalWorkflowBroker(providers.provider_map())

    trace, approvals, brokered, issues, terminal_step = _execute_local(
        compiled.ir,
        fixture,
        local_broker,
        request.approve_all,
        append,
        max_transitions=max_transitions,
    )
    status = "passed" if terminal_step is not None and not issues else "blocked"
    append("run_completed", status, details={"terminal_step": terminal_step, "issues": list(issues)})

    report = WorkflowRunReport(
        run_id=run_id,
        workflow_name=workflow_name,
        mode=request.mode,
        status=status,
        package_fingerprint=package_fingerprint,
        terminal_step=terminal_step,
        step_trace=tuple(trace),
        brokered_actions=tuple(brokered),
        approvals=tuple(approvals),
        events=tuple(events),
        issues=tuple(issues),
    )
    store.write_trace(run_id, report.step_trace)
    store.write_approvals(run_id, [approval.__dict__ for approval in approvals])
    store.write_report(run_id, report.to_dict())
    return report
```

Add private helpers in the same file:

```python
def _execute_local(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    broker: LocalWorkflowBroker,
    approve_all: bool,
    append: Any,
    *,
    max_transitions: int,
) -> tuple[list[StepTrace], list[WorkflowApprovalPreview], list[WorkflowBrokeredActionPreview], list[str], str | None]:
    trace: list[StepTrace] = []
    approvals: list[WorkflowApprovalPreview] = []
    brokered: list[WorkflowBrokeredActionPreview] = []
    issues: list[str] = []
    current = ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > max_transitions:
            issues.append("transition budget exceeded")
            return trace, approvals, brokered, issues, None
        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            return trace, approvals, brokered, issues, None
        append("step_started", "running", step=step)
        selected_branch = None
        next_step = step.next_step
        outputs: Mapping[str, Any] = {}
        broker_status = None
        broker_provider_id = None

        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                issues.append(f"no branch matched for {step.step_id}")
                trace.append(_trace(step, "blocked", selected_branch, outputs, ("no branch matched",)))
                append("step_blocked", "blocked", step=step, details={"issues": ["no branch matched"]})
                return trace, approvals, brokered, issues, None

        if step.uses is not None:
            action = _action_for_step(ir, step)
            allowed_outputs = action.allowed_outputs if action is not None else ()
            idempotency_fields = action.idempotency_key_fields if action is not None else ()
            if _requires_approval(ir, step):
                approvals.append(
                    WorkflowApprovalPreview(
                        approval_id=step.approval or f"approval-{step.step_id}",
                        step_ids=(step.step_id,),
                        mode="local-auto" if approve_all else "explicit",
                        status="approved" if approve_all else "pending",
                    )
                )
                append("approval_requested", "pending", step=step)
                if not approve_all:
                    issues.append(f"approval required for {step.step_id}")
                    trace.append(_trace(step, "blocked", selected_branch, outputs, ("approval required",)))
                    append("step_blocked", "blocked", step=step, details={"issues": ["approval required"]})
                    return trace, approvals, brokered, issues, None
                append("approval_granted", "approved", step=step)
            append("broker_action_started", "running", step=step)
            result = broker.invoke(
                BrokerRequest(
                    step_id=step.step_id,
                    connector_action=step.uses.dotted,
                    input_ref=step.raw,
                    fixture=fixture,
                    allowed_outputs=allowed_outputs,
                )
            )
            outputs = result.outputs
            broker_status = result.status
            broker_provider_id = result.provider_id
            if result.issues:
                issues.extend(result.issues)
                trace.append(_trace(step, "blocked", selected_branch, outputs, tuple(result.issues), broker_status, broker_provider_id))
                append("broker_action_blocked", "blocked", step=step, details={"issues": list(result.issues)})
                return trace, approvals, brokered, issues, None
            brokered.append(
                WorkflowBrokeredActionPreview(
                    step_id=step.step_id,
                    connector_action=step.uses.dotted,
                    approval_id=step.approval,
                    side_effect_class=step.side_effect_class,
                    allowed_outputs=allowed_outputs,
                    idempotency_key_fields=idempotency_fields,
                    broker_status=broker_status,
                    broker_provider_id=broker_provider_id,
                    simulated_outputs=outputs,
                )
            )
            append("broker_action_completed", broker_status or "completed", step=step, details={"outputs": dict(outputs)})

        status = "terminal" if step.kind is StepKind.TERMINAL else "completed"
        trace.append(_trace(step, status, selected_branch, outputs, (), broker_status, broker_provider_id))
        append("step_completed", status, step=step)
        if step.kind is StepKind.TERMINAL:
            return trace, approvals, brokered, issues, step.step_id
        current = next_step

    issues.append("workflow stopped without terminal step")
    return trace, approvals, brokered, issues, None
```

Add remaining helpers:

```python
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


def _action_for_step(ir: WorkflowIR, step: WorkflowStep):
    if step.uses is None:
        return None
    connector = ir.connectors.get(step.uses.connector_id)
    return connector.actions.get(step.uses.action_id) if connector is not None else None


def _requires_approval(ir: WorkflowIR, step: WorkflowStep) -> bool:
    if step.approval:
        return True
    action = _action_for_step(ir, step)
    if action is not None and action.approval_required:
        return True
    return step.side_effect_class in MUTATING_SIDE_EFFECTS


def _trace(
    step: WorkflowStep,
    status: str,
    selected_branch: str | None,
    outputs: Mapping[str, Any],
    issues: tuple[str, ...],
    broker_status: str | None = None,
    broker_provider_id: str | None = None,
) -> StepTrace:
    return StepTrace(
        step_id=step.step_id,
        kind=step.kind.value,
        status=status,
        selected_branch=selected_branch,
        connector_action=step.uses.dotted if step.uses is not None else None,
        side_effect_class=step.side_effect_class,
        approval_id=step.approval,
        broker_status=broker_status,
        broker_provider_id=broker_provider_id,
        simulated_outputs=outputs,
        issues=issues,
    )


def _blocked_report(
    request: WorkflowRunRequest,
    *,
    workflow_name: str,
    package_fingerprint: str,
    issues: tuple[str, ...],
) -> WorkflowRunReport:
    return WorkflowRunReport(
        run_id="",
        workflow_name=workflow_name,
        mode=request.mode,
        status="blocked",
        package_fingerprint=package_fingerprint,
        terminal_step=None,
        step_trace=(),
        brokered_actions=(),
        approvals=(),
        events=(),
        issues=issues,
    )
```

- [ ] **Step 5: Export runner API**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.runner import run_workflow
```

Add `"run_workflow"` to `__all__`.

- [ ] **Step 6: Run runner tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_runner.py appV2.4/tests/test_workflow_execution.py -q
```

Expected:

```text
all selected tests pass
```

---

### Task 5: Add Live Mode Blocking Tests

**Files:**

- Modify: `appV2.4/tests/test_workflow_runner.py`

- [ ] **Step 1: Write test proving live-production remains blocked**

Append:

```python
def test_runner_blocks_live_production_by_default(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.events == ()
    assert report.step_trace == ()
    assert report.issues == ("workflow run mode is not implemented: live-production",)
```

- [ ] **Step 2: Run test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_runner.py::test_runner_blocks_live_production_by_default -q
```

Expected:

```text
1 passed
```

---

### Task 6: Workflow Verification

**Files:**

- No new files unless a failure reveals a scoped fix.

- [ ] **Step 1: Run workflow suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_*.py appV2.4/tests/test_profiles.py
```

Expected:

```text
all selected workflow/profile tests pass
```

- [ ] **Step 2: Run full appV2.4 suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected:

```text
1260+ tests pass
```

- [ ] **Step 3: Check zone scope**

Run:

```bash
git diff --name-only
```

Expected changed files only under:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_*.py
```

No red-zone files.

---

### Task 7: Optional TUI Command Bridge

This task is optional and must not start until the green runner API is passing.

**Files:**

- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Write failing TUI command test**

Add a focused test proving:

```text
/workflow-run package happy_path.yaml --mode local --approve-all
```

calls `run_workflow()` and displays:

```text
Workflow run: passed
terminal: done
run: run-...
```

- [ ] **Step 2: Implement a thin command bridge only**

Implementation rule:

```text
Do not embed workflow execution logic in TUI.
Parse command.
Call appv24.workflows.run_workflow().
Render concise result.
```

- [ ] **Step 3: Run TUI and full tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py appV2.4/tests/test_workflow_*.py
```

Expected:

```text
all selected tests pass
```

---

## Self-Review Checklist

- [ ] Plan starts with runner API and tests, not TUI.
- [ ] No red-zone files are touched.
- [ ] Yellow-zone work is optional and isolated.
- [ ] Local run creates artifacts but no external side effects.
- [ ] Live-production remains blocked by default.
- [ ] Missing approval blocks before broker action.
- [ ] Every new behavior has a failing test first.
- [ ] Full appV2.4 pytest suite is required before completion.
