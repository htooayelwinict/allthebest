# appv24 Workflow Enablement v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a no-side-effect local broker skeleton and golden workflow fixtures so appv24 can prove valid workflow packages route through broker-shaped dry-run simulation without executing real connectors.

**Architecture:** Keep the work in the green-zone workflow package layer. Add a deterministic local broker, example packages, and an enablement report API; extend simulation to optionally route action steps through the broker. Do not add CLI/TUI commands or real connector execution.

**Tech Stack:** Python dataclasses, existing appv24 workflow loader/compiler/policy/simulator modules, pytest.

---

## File Structure

Create:

- `appV2.4/appv24/workflows/broker.py`
  Defines `BrokerRequest`, `BrokerResult`, `BrokerProvider`, and `LocalWorkflowBroker`.

- `appV2.4/appv24/workflows/examples.py`
  Provides in-memory golden workflow package documents and fixtures.

- `appV2.4/appv24/workflows/reports.py`
  Builds `WorkflowEnablementReport` from validation, compile, policy, and simulation results.

- `appV2.4/tests/test_workflow_broker.py`
  Tests broker contract and simulator routing.

- `appV2.4/tests/test_workflow_examples.py`
  Tests golden examples compile, pass policy, and simulate deterministically.

- `docs/appv24/workflow-enable-v0.md`
  Documents the passive broker-shaped enablement pipeline.

Modify:

- `appV2.4/appv24/workflows/simulator.py`
  Add optional broker routing for action steps.

- `appV2.4/appv24/workflows/ledger.py`
  Add optional broker fields to `StepTrace`.

- `appV2.4/appv24/workflows/__init__.py`
  Export the new public API.

Do not modify red-zone files. Avoid yellow-zone files.

---

### Task 1: Add Local Broker Contract

**Files:**

- Create: `appV2.4/appv24/workflows/broker.py`
- Test: `appV2.4/tests/test_workflow_broker.py`

- [ ] **Step 1: Write failing broker contract tests**

Create `appV2.4/tests/test_workflow_broker.py`:

```python
from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker


def test_local_broker_filters_outputs_to_allowed_fields() -> None:
    broker = LocalWorkflowBroker(
        {
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"message_id": "m-1", "secret": "blocked"},
            )
        }
    )

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
        )
    )

    assert result.status == "simulated"
    assert result.outputs == {"message_id": "m-1"}
    assert result.issues == ()


def test_local_broker_blocks_unknown_connector_action() -> None:
    broker = LocalWorkflowBroker({})

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("no dry-run provider registered for telegram.send_message",)


def test_local_broker_blocks_non_dry_run_request() -> None:
    broker = LocalWorkflowBroker(
        {
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"message_id": "m-1"},
            )
        }
    )

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("broker only supports dry-run requests",)
```

- [ ] **Step 2: Run broker tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_broker.py -q
```

Expected: fail because `appv24.workflows.broker` does not exist.

- [ ] **Step 3: Implement broker contract**

Create `appV2.4/appv24/workflows/broker.py`:

```python
"""No-side-effect local broker for appv24 workflow simulation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BrokerRequest:
    step_id: str
    connector_action: str
    input_ref: Mapping[str, Any]
    fixture: Mapping[str, Any]
    allowed_outputs: tuple[str, ...]
    dry_run: bool = True


@dataclass(frozen=True)
class BrokerResult:
    status: str
    provider_id: str
    outputs: Mapping[str, Any]
    issues: tuple[str, ...] = ()


BrokerProvider = Callable[[BrokerRequest], BrokerResult]


class LocalWorkflowBroker:
    """Routes IR action steps to deterministic dry-run providers only."""

    def __init__(self, providers: Mapping[str, BrokerProvider]):
        self._providers = dict(providers)

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if not request.dry_run:
            return BrokerResult(
                status="blocked",
                provider_id="local",
                outputs={},
                issues=("broker only supports dry-run requests",),
            )
        provider = self._providers.get(request.connector_action)
        if provider is None:
            return BrokerResult(
                status="blocked",
                provider_id="local",
                outputs={},
                issues=(f"no dry-run provider registered for {request.connector_action}",),
            )
        result = provider(request)
        allowed = set(request.allowed_outputs)
        filtered = {key: value for key, value in result.outputs.items() if key in allowed}
        return BrokerResult(
            status=result.status,
            provider_id=result.provider_id,
            outputs=filtered,
            issues=result.issues,
        )
```

- [ ] **Step 4: Run broker tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_broker.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit task 1**

```bash
git add appV2.4/appv24/workflows/broker.py appV2.4/tests/test_workflow_broker.py
git commit -m "feat(appv24): add local workflow broker"
```

---

### Task 2: Route Simulator Action Steps Through Broker

**Files:**

- Modify: `appV2.4/appv24/workflows/simulator.py`
- Modify: `appV2.4/appv24/workflows/ledger.py`
- Test: `appV2.4/tests/test_workflow_broker.py`

- [ ] **Step 1: Add failing simulator broker routing test**

Append to `appV2.4/tests/test_workflow_broker.py`:

```python
from pathlib import Path

from appv24.workflows.ir import (
    ActionCapability,
    ApprovalRequirement,
    ConnectorCapability,
    ConnectorRef,
    StepKind,
    WorkflowDesignContract,
    WorkflowIR,
    WorkflowStep,
)
from appv24.workflows.simulator import simulate_workflow_fixture


def _minimal_ir() -> WorkflowIR:
    action = ActionCapability(
        connector_id="telegram",
        action_id="send_message",
        adapter="direct_api",
        side_effect=True,
        side_effect_class="send",
        execution_mode="brokered",
        approval_required=True,
        dry_run_supported=True,
        idempotency_key_fields=("message_key",),
        allowed_outputs=("message_id",),
        required_scopes=(),
        granted_scopes=(),
        raw={},
    )
    return WorkflowIR(
        root=Path("."),
        name="test",
        version="1.0.0",
        entrypoint="notify_admin",
        triggers=(),
        steps={
            "notify_admin": WorkflowStep(
                step_id="notify_admin",
                kind=StepKind.ACTION,
                uses=ConnectorRef("telegram", "send_message"),
                side_effect_class="send",
                approval="admin_approval",
                next_step="done",
                branches={},
                outbound=("done",),
                raw={},
            ),
            "done": WorkflowStep(
                step_id="done",
                kind=StepKind.TERMINAL,
                uses=None,
                side_effect_class=None,
                approval=None,
                next_step=None,
                branches={},
                outbound=(),
                raw={},
            ),
        },
        connectors={
            "telegram": ConnectorCapability(
                connector_id="telegram",
                adapter="direct_api",
                actions={"send_message": action},
                raw={},
            )
        },
        approvals={
            "admin_approval": ApprovalRequirement(
                approval_id="admin_approval",
                required_for=("notify_admin",),
                mode="explicit",
                raw={},
            )
        },
        design=WorkflowDesignContract(
            intent="test",
            trigger="manual",
            inputs=(),
            outputs=(),
            assumptions=(),
            side_effects=("send",),
            approval_gates=("admin_approval",),
            failure_modes=(),
            rollback=(),
        ),
    )


def test_simulator_uses_broker_outputs_for_action_steps() -> None:
    broker = LocalWorkflowBroker(
        {
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"message_id": "m-1", "extra": "blocked"},
            )
        }
    )

    ledger = simulate_workflow_fixture(
        _minimal_ir(),
        "happy_path.yaml",
        {},
        broker=broker,
    )

    assert ledger.status == "passed"
    assert ledger.trace[0].broker_status == "simulated"
    assert ledger.trace[0].broker_provider_id == "fixture"
    assert ledger.trace[0].simulated_outputs == {"message_id": "m-1"}
```

- [ ] **Step 2: Run the new test and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_broker.py::test_simulator_uses_broker_outputs_for_action_steps -q
```

Expected: fail because `simulate_workflow_fixture` has no `broker` argument or `StepTrace` has no broker fields.

- [ ] **Step 3: Add broker fields to ledger**

Modify `appV2.4/appv24/workflows/ledger.py`:

```python
@dataclass(frozen=True)
class StepTrace:
    step_id: str
    kind: str
    status: str
    selected_branch: str | None = None
    connector_action: str | None = None
    side_effect_class: str | None = None
    approval_id: str | None = None
    broker_status: str | None = None
    broker_provider_id: str | None = None
    simulated_outputs: Mapping[str, Any] | None = None
    issues: tuple[str, ...] = ()
```

- [ ] **Step 4: Add optional broker routing in simulator**

Modify `appV2.4/appv24/workflows/simulator.py`:

```python
from appv24.workflows.broker import BrokerRequest, LocalWorkflowBroker
```

Update the function signature:

```python
def simulate_workflow_fixture(
    ir: WorkflowIR,
    fixture_name: str,
    fixture: Mapping[str, Any],
    *,
    max_transitions: int = 200,
    broker: LocalWorkflowBroker | None = None,
) -> SimulationLedger:
```

Replace the output lookup inside the loop and initialize branch state before broker output resolution:

```python
        selected_branch = None
        next_step = step.next_step
        outputs, broker_status, broker_provider_id, broker_issues = _step_outputs(ir, step, fixture, broker)
        if broker_issues:
            issues.extend(broker_issues)
            trace.append(_trace(step, "blocked", selected_branch, outputs, tuple(broker_issues), broker_status, broker_provider_id))
            return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
```

Add helpers:

```python
def _step_outputs(
    ir: WorkflowIR,
    step: WorkflowStep,
    fixture: Mapping[str, Any],
    broker: LocalWorkflowBroker | None,
) -> tuple[Mapping[str, Any], str | None, str | None, tuple[str, ...]]:
    if broker is None or step.uses is None:
        return _mock_outputs(step, fixture), None, None, ()
    connector = ir.connectors.get(step.uses.connector_id)
    action = connector.actions.get(step.uses.action_id) if connector is not None else None
    allowed_outputs = action.allowed_outputs if action is not None else ()
    result = broker.invoke(
        BrokerRequest(
            step_id=step.step_id,
            connector_action=step.uses.dotted,
            input_ref=step.raw,
            fixture=fixture,
            allowed_outputs=allowed_outputs,
        )
    )
    return result.outputs, result.status, result.provider_id, result.issues
```

Update `_trace` signature and construction:

```python
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
```

- [ ] **Step 5: Run workflow broker tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_broker.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Run existing simulator tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_simulator.py -q
```

Expected: pass, proving non-broker behavior remains compatible.

- [ ] **Step 7: Commit task 2**

```bash
git add appV2.4/appv24/workflows/simulator.py appV2.4/appv24/workflows/ledger.py appV2.4/tests/test_workflow_broker.py
git commit -m "feat(appv24): route workflow simulation through broker"
```

---

### Task 3: Add Golden Workflow Examples

**Files:**

- Create: `appV2.4/appv24/workflows/examples.py`
- Test: `appV2.4/tests/test_workflow_examples.py`

- [ ] **Step 1: Write failing golden example tests**

Create `appV2.4/tests/test_workflow_examples.py`:

```python
from __future__ import annotations

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.simulator import simulate_workflow_fixture


def test_golden_examples_compile_and_pass_policy() -> None:
    for example in golden_workflow_examples():
        result = compile_workflow_documents(example.documents)
        assert result.ok, example.name
        assert result.ir is not None
        assert evaluate_policy(result.ir).ok, example.name


def test_telegram_support_ticket_happy_path_is_deterministic() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = compile_workflow_documents(example.documents)
    assert result.ir is not None

    first = simulate_workflow_fixture(result.ir, "happy_path.yaml", example.fixtures["happy_path.yaml"], broker=example.broker)
    second = simulate_workflow_fixture(result.ir, "happy_path.yaml", example.fixtures["happy_path.yaml"], broker=example.broker)

    assert first.to_dict() == second.to_dict()
    assert first.status == "passed"
    assert first.terminal_step == "done"
    assert first.simulated_side_effects == ("create_ticket_draft", "notify_admin")


def test_telegram_support_ticket_rejection_path_has_no_side_effects() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = compile_workflow_documents(example.documents)
    assert result.ir is not None

    ledger = simulate_workflow_fixture(
        result.ir,
        "rejected_customer.yaml",
        example.fixtures["rejected_customer.yaml"],
        broker=example.broker,
    )

    assert ledger.status == "passed"
    assert ledger.terminal_step == "rejected"
    assert ledger.simulated_side_effects == ()
```

- [ ] **Step 2: Run golden example tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_examples.py -q
```

Expected: fail because `appv24.workflows.examples` does not exist.

- [ ] **Step 3: Implement golden example module**

Create `appV2.4/appv24/workflows/examples.py`:

```python
"""Golden workflow package examples for appv24 tests and docs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from appv24.workflows.broker import BrokerResult, LocalWorkflowBroker
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class GoldenWorkflowExample:
    name: str
    documents: PackageDocuments
    fixtures: Mapping[str, Mapping[str, object]]
    broker: LocalWorkflowBroker


def golden_workflow_examples() -> dict[str, GoldenWorkflowExample]:
    return {"telegram_support_ticket": _telegram_support_ticket()}


def _telegram_support_ticket() -> GoldenWorkflowExample:
    workflow = {
        "version": "appv24.workflow/v1",
        "name": "telegram_support_ticket",
        "description": "Create a support ticket draft from an authorized Telegram customer message and notify an admin.",
        "entrypoint": "check_authorized_customer",
        "triggers": [{"id": "telegram_message", "type": "webhook"}],
        "design": {
            "intent": "Create a support ticket draft from an authorized Telegram customer message and notify an admin.",
            "trigger": "Telegram inbound message webhook.",
            "inputs": ["telegram_user_id", "message_text"],
            "outputs": ["ticket_id", "admin_message_id"],
            "assumptions": ["authorized customer ids are configured outside the workflow package"],
            "side_effects": ["create ticket draft", "send admin notification"],
            "approval_gates": ["admin_approval"],
            "failure_modes": ["unauthorized sender", "ticket draft failure", "admin notification failure"],
            "rollback": ["delete local draft artifacts only"],
        },
        "steps": [
            {
                "id": "check_authorized_customer",
                "type": "decision",
                "branches": {
                    "authorized": "create_ticket_draft",
                    "rejected": "rejected",
                },
            },
            {
                "id": "create_ticket_draft",
                "type": "action",
                "uses": "ticket.create_draft",
                "side_effect_class": "create",
                "approval": "admin_approval",
                "next": "notify_admin",
            },
            {
                "id": "notify_admin",
                "type": "action",
                "uses": "telegram.send_message",
                "side_effect_class": "send",
                "approval": "admin_approval",
                "next": "done",
            },
            {"id": "rejected", "type": "terminal"},
            {"id": "done", "type": "terminal"},
        ],
    }
    connectors = {
        "version": "appv24.connectors/v1",
        "connectors": [
            {
                "id": "ticket",
                "adapter": "manual",
                "description": "Local ticket draft adapter.",
                "actions": [
                    {
                        "id": "create_draft",
                        "operationId": "ticket_create_draft",
                        "execution_mode": "brokered",
                        "side_effect": True,
                        "side_effect_class": "create",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["ticket:draft"],
                        "outputs": {"allowed": ["ticket_id"]},
                        "idempotency": {"key_fields": ["telegram_user_id", "message_text"]},
                    }
                ],
            },
            {
                "id": "telegram",
                "adapter": "direct_api",
                "description": "Telegram Bot API.",
                "actions": [
                    {
                        "id": "send_message",
                        "operationId": "telegram_send_message",
                        "execution_mode": "brokered",
                        "side_effect": True,
                        "side_effect_class": "send",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["telegram:send"],
                        "outputs": {"allowed": ["message_id"]},
                        "idempotency": {"key_fields": ["ticket_id", "admin_chat_id"]},
                    }
                ],
            },
        ]
    }
    permissions = {
        "version": "appv24.permissions/v1",
        "permissions": [
            {"connector": "ticket", "scopes": ["ticket:draft"], "actions": ["create_draft"]},
            {"connector": "telegram", "scopes": ["telegram:send"], "actions": ["send_message"]},
        ],
        "approvals": [
            {
                "id": "admin_approval",
                "mode": "explicit",
                "required_for": ["create_ticket_draft", "notify_admin"],
            }
        ],
    }
    fixtures = {
        "happy_path.yaml": {
            "decision": {"check_authorized_customer": "authorized"},
        },
        "rejected_customer.yaml": {
            "decision": {"check_authorized_customer": "rejected"},
        },
    }
    broker = LocalWorkflowBroker(
        {
            "ticket.create_draft": lambda request: BrokerResult(
                status="simulated",
                provider_id="golden_fixture",
                outputs={"ticket_id": "ticket-1"},
            ),
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="golden_fixture",
                outputs={"message_id": "msg-1"},
            ),
        }
    )
    documents = PackageDocuments(
        root=Path("golden/telegram_support_ticket"),
        workflow=workflow,
        connectors=connectors,
        permissions=permissions,
        dry_run_fixtures=fixtures,
        rollback_text="# Rollback\n\nDelete local draft artifacts only.\n",
    )
    return GoldenWorkflowExample(
        name="telegram_support_ticket",
        documents=documents,
        fixtures=fixtures,
        broker=broker,
    )
```

- [ ] **Step 4: Run golden example tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_examples.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit task 3**

```bash
git add appV2.4/appv24/workflows/examples.py appV2.4/tests/test_workflow_examples.py
git commit -m "test(appv24): add golden workflow examples"
```

---

### Task 4: Add Enablement Report API

**Files:**

- Create: `appV2.4/appv24/workflows/reports.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: `appV2.4/tests/test_workflow_examples.py`

- [ ] **Step 1: Write failing report test**

Append to `appV2.4/tests/test_workflow_examples.py`:

```python
from appv24.workflows.reports import build_enablement_report


def test_enablement_report_summarizes_golden_example() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    report = build_enablement_report(
        example.documents,
        "happy_path.yaml",
        example.fixtures["happy_path.yaml"],
        broker=example.broker,
    )

    assert report.workflow_name == "telegram_support_ticket"
    assert report.validation_ok is True
    assert report.compile_ok is True
    assert report.policy_ok is True
    assert report.simulation_status == "passed"
    assert report.terminal_step == "done"
    assert report.simulated_side_effects == ("create_ticket_draft", "notify_admin")
    assert report.issues == ()
```

- [ ] **Step 2: Run report test and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_examples.py::test_enablement_report_summarizes_golden_example -q
```

Expected: fail because `appv24.workflows.reports` does not exist.

- [ ] **Step 3: Implement report module**

Create `appV2.4/appv24/workflows/reports.py`:

```python
"""Workflow enablement reports for appv24 passive simulations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.simulator import simulate_workflow_fixture
from appv24.workflows.validator import validate_workflow_documents


@dataclass(frozen=True)
class WorkflowEnablementReport:
    workflow_name: str
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    simulation_status: str
    terminal_step: str | None
    simulated_side_effects: tuple[str, ...]
    issues: tuple[str, ...]


def build_enablement_report(
    documents: PackageDocuments,
    fixture_name: str,
    fixture: Mapping[str, Any],
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowEnablementReport:
    workflow_name = str(documents.workflow.get("name", ""))
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return WorkflowEnablementReport(
            workflow_name=workflow_name,
            validation_ok=False,
            compile_ok=False,
            policy_ok=False,
            simulation_status="blocked",
            terminal_step=None,
            simulated_side_effects=(),
            issues=tuple(issue.message for issue in validation.issues),
        )
    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return WorkflowEnablementReport(
            workflow_name=workflow_name,
            validation_ok=True,
            compile_ok=False,
            policy_ok=False,
            simulation_status="blocked",
            terminal_step=None,
            simulated_side_effects=(),
            issues=tuple(issue.message for issue in compiled.issues),
        )
    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return WorkflowEnablementReport(
            workflow_name=workflow_name,
            validation_ok=True,
            compile_ok=True,
            policy_ok=False,
            simulation_status="blocked",
            terminal_step=None,
            simulated_side_effects=(),
            issues=tuple(issue.message for issue in policy.issues),
        )
    ledger = simulate_workflow_fixture(compiled.ir, fixture_name, fixture, broker=broker)
    return WorkflowEnablementReport(
        workflow_name=workflow_name,
        validation_ok=True,
        compile_ok=True,
        policy_ok=True,
        simulation_status=ledger.status,
        terminal_step=ledger.terminal_step,
        simulated_side_effects=ledger.simulated_side_effects,
        issues=ledger.issues,
    )
```

- [ ] **Step 4: Export report and broker API**

Modify `appV2.4/appv24/workflows/__init__.py` to include:

```python
from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.examples import GoldenWorkflowExample, golden_workflow_examples
from appv24.workflows.reports import WorkflowEnablementReport, build_enablement_report
```

Add these names to `__all__` if the file defines `__all__`.

- [ ] **Step 5: Run report tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_examples.py -q
```

Expected: pass.

- [ ] **Step 6: Commit task 4**

```bash
git add appV2.4/appv24/workflows/reports.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_examples.py
git commit -m "feat(appv24): add workflow enablement report"
```

---

### Task 5: Document Workflow Enablement v0

**Files:**

- Create: `docs/appv24/workflow-enable-v0.md`

- [ ] **Step 1: Add docs**

Create `docs/appv24/workflow-enable-v0.md`:

```markdown
# appv24 Workflow Enablement v0

Workflow Enablement v0 proves that a saved appv24 workflow package can be validated, compiled, policy-checked, and routed through a no-side-effect local broker during dry-run simulation.

This is not workflow execution. It does not call external APIs, send messages, provision infrastructure, read secrets, run shell commands, or mutate files.

## Pipeline

```text
PackageDocuments
  -> validate_workflow_documents
  -> compile_workflow_documents
  -> evaluate_policy
  -> LocalWorkflowBroker
  -> simulate_workflow_fixture
  -> WorkflowEnablementReport
```

## Python API

```python
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.reports import build_enablement_report

example = golden_workflow_examples()["telegram_support_ticket"]
report = build_enablement_report(
    example.documents,
    "happy_path.yaml",
    example.fixtures["happy_path.yaml"],
    broker=example.broker,
)
```

## Safety Boundary

- broker requests are dry-run only
- provider outputs are filtered to declared `outputs.allowed`
- unknown connector actions block simulation
- real connector execution is out of scope
- CLI/TUI commands are deferred
```

- [ ] **Step 2: Commit docs**

```bash
git add docs/appv24/workflow-enable-v0.md
git commit -m "docs(appv24): document workflow enablement v0"
```

---

### Task 6: Full Verification

**Files:**

- No edits

- [ ] **Step 1: Run focused workflow tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_broker.py appV2.4/tests/test_workflow_examples.py appV2.4/tests/test_workflow_simulator.py -q
```

Expected: pass.

- [ ] **Step 2: Run all workflow tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_workflow_ir.py appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_simulator.py appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_broker.py appV2.4/tests/test_workflow_examples.py -q
```

Expected: pass.

- [ ] **Step 3: Run full appV2.4 suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider appV2.4/tests -q
```

Expected: pass.

- [ ] **Step 4: Check zone impact**

Run:

```bash
git diff --name-only HEAD~5..HEAD
```

Expected changed paths are limited to:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_broker.py
appV2.4/tests/test_workflow_examples.py
docs/appv24/workflow-enable-v0.md
```

No red-zone files should appear. No yellow-zone files should appear.

- [ ] **Step 5: Final commit if needed**

If verification required docs or test adjustments, commit them:

```bash
git add appV2.4/appv24/workflows appV2.4/tests docs/appv24
git commit -m "test(appv24): verify workflow enablement v0"
```

If no files changed, skip this commit.

---

## Self-Review

Spec coverage:

- No-side-effect broker skeleton: Task 1 and Task 2.
- Golden fixtures: Task 3.
- Enablement report: Task 4.
- Documentation: Task 5.
- Verification and zone check: Task 6.

Scope check:

- No red-zone files.
- No yellow-zone files.
- No real connector execution.
- No CLI/TUI commands.

Rollback:

- Revert the commits from this plan.
- Since no runtime red/yellow files are touched, rollback is isolated to workflow modules, tests, and docs.
