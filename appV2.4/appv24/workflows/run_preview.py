"""Dry-run workflow preview reports for saved appv24 workflow packages."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.execution import WorkflowRunEvent
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.idempotency import build_idempotency_key
from appv24.workflows.ir import ActionCapability, WorkflowIR
from appv24.workflows.ledger import StepTrace
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.simulator import simulate_workflow_fixture
from appv24.workflows.validator import validate_workflow_documents


@dataclass(frozen=True)
class WorkflowBrokeredActionPreview:
    step_id: str
    connector_action: str
    approval_id: str | None
    side_effect_class: str | None
    allowed_outputs: tuple[str, ...]
    idempotency_key_fields: tuple[str, ...]
    idempotency_key: str | None
    broker_status: str | None
    broker_provider_id: str | None
    simulated_outputs: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowApprovalPreview:
    approval_id: str
    step_ids: tuple[str, ...]
    mode: str
    status: str = "pending"


@dataclass(frozen=True)
class WorkflowLedgerPreviewEvent:
    event_type: str
    step_id: str
    status: str
    approval_id: str | None = None
    connector_action: str | None = None
    broker_provider_id: str | None = None
    outputs: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class WorkflowRunPreview:
    workflow_name: str
    fixture_name: str
    package_fingerprint: str
    status: str
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    terminal_step: str | None
    step_trace: tuple[StepTrace, ...]
    brokered_actions: tuple[WorkflowBrokeredActionPreview, ...]
    approvals_required: tuple[WorkflowApprovalPreview, ...]
    ledger_preview: tuple[WorkflowLedgerPreviewEvent, ...]
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_run_preview_from_path(
    package_path: str | Path,
    fixture_name: str,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowRunPreview:
    return build_run_preview(load_package_documents(package_path), fixture_name, broker=broker)


def build_run_preview(
    documents: PackageDocuments,
    fixture_name: str,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowRunPreview:
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_preview(
            workflow_name,
            fixture_name,
            package_fingerprint=package_fingerprint,
            validation_ok=False,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_preview(
            workflow_name,
            fixture_name,
            package_fingerprint=package_fingerprint,
            validation_ok=True,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_preview(
            workflow_name,
            fixture_name,
            package_fingerprint=package_fingerprint,
            validation_ok=True,
            compile_ok=True,
            policy_ok=False,
            issues=tuple(issue.message for issue in policy.issues),
        )

    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    ledger = simulate_workflow_fixture(compiled.ir, fixture_name, fixture, broker=broker)
    brokered_actions = _brokered_actions(compiled.ir, ledger.trace, package_fingerprint=package_fingerprint, fixture=fixture)
    return WorkflowRunPreview(
        workflow_name=workflow_name,
        fixture_name=fixture_name,
        package_fingerprint=package_fingerprint,
        status=ledger.status,
        validation_ok=True,
        compile_ok=True,
        policy_ok=True,
        terminal_step=ledger.terminal_step,
        step_trace=ledger.trace,
        brokered_actions=brokered_actions,
        approvals_required=_approvals_required(compiled.ir, ledger.trace),
        ledger_preview=_ledger_preview(brokered_actions),
        issues=ledger.issues,
    )


def persist_run_preview(store: WorkflowRunStore, preview: WorkflowRunPreview) -> str:
    run_id = store.create_run(
        preview.workflow_name,
        preview.package_fingerprint,
        metadata={
            "mode": "preview",
            "fixture_name": preview.fixture_name,
            "approve_all": False,
            "approval_mode": "preview",
            "activation_id": None,
            "activation_trigger_type": None,
        },
    )
    preview_path = store.root / run_id / "preview.json"
    preview_path.write_text(json.dumps(preview.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    store.append_event(
        WorkflowRunEvent(
            event_id="event-1",
            run_id=run_id,
            event_type="preview_created",
            package_fingerprint=preview.package_fingerprint,
            step_id=None,
            connector_action=None,
            status=preview.status,
            details={"fixture_name": preview.fixture_name},
        )
    )
    for index, event in enumerate(preview.ledger_preview, start=2):
        store.append_event(
            WorkflowRunEvent(
                event_id=f"event-{index}",
                run_id=run_id,
                event_type=event.event_type,
                package_fingerprint=preview.package_fingerprint,
                step_id=event.step_id,
                connector_action=event.connector_action,
                status=event.status,
                details={
                    "approval_id": event.approval_id,
                    "broker_provider_id": event.broker_provider_id,
                    "outputs": dict(event.outputs or {}),
                },
            )
        )
    return run_id


def _blocked_preview(
    workflow_name: str,
    fixture_name: str,
    *,
    package_fingerprint: str,
    validation_ok: bool,
    compile_ok: bool,
    policy_ok: bool,
    issues: tuple[str, ...],
) -> WorkflowRunPreview:
    return WorkflowRunPreview(
        workflow_name=workflow_name,
        fixture_name=fixture_name,
        package_fingerprint=package_fingerprint,
        status="blocked",
        validation_ok=validation_ok,
        compile_ok=compile_ok,
        policy_ok=policy_ok,
        terminal_step=None,
        step_trace=(),
        brokered_actions=(),
        approvals_required=(),
        ledger_preview=(),
        issues=issues,
    )


def _brokered_actions(
    ir: WorkflowIR,
    trace: tuple[StepTrace, ...],
    *,
    package_fingerprint: str,
    fixture: Mapping[str, Any],
) -> tuple[WorkflowBrokeredActionPreview, ...]:
    previews: list[WorkflowBrokeredActionPreview] = []
    for step_trace in trace:
        if step_trace.connector_action is None:
            continue
        action = _action_for_step(ir, step_trace.step_id)
        step = ir.steps.get(step_trace.step_id)
        idempotency_key_fields = action.idempotency_key_fields if action is not None else ()
        previews.append(
            WorkflowBrokeredActionPreview(
                step_id=step_trace.step_id,
                connector_action=step_trace.connector_action,
                approval_id=step_trace.approval_id,
                side_effect_class=step_trace.side_effect_class,
                allowed_outputs=action.allowed_outputs if action is not None else (),
                idempotency_key_fields=idempotency_key_fields,
                idempotency_key=build_idempotency_key(
                    package_fingerprint=package_fingerprint,
                    step_id=step_trace.step_id,
                    connector_action=step_trace.connector_action,
                    key_fields=idempotency_key_fields,
                    input_ref=step.raw if step is not None else {},
                    fixture=fixture,
                ),
                broker_status=step_trace.broker_status,
                broker_provider_id=step_trace.broker_provider_id,
                simulated_outputs=dict(step_trace.simulated_outputs or {}),
            )
        )
    return tuple(previews)


def _approvals_required(
    ir: WorkflowIR,
    trace: tuple[StepTrace, ...],
) -> tuple[WorkflowApprovalPreview, ...]:
    grouped: dict[str, list[str]] = {}
    for step_trace in trace:
        if step_trace.approval_id is not None:
            grouped.setdefault(step_trace.approval_id, []).append(step_trace.step_id)
    return tuple(
        WorkflowApprovalPreview(
            approval_id=approval_id,
            step_ids=tuple(step_ids),
            mode=ir.approvals[approval_id].mode if approval_id in ir.approvals else "explicit",
        )
        for approval_id, step_ids in grouped.items()
    )


def _ledger_preview(
    actions: tuple[WorkflowBrokeredActionPreview, ...],
) -> tuple[WorkflowLedgerPreviewEvent, ...]:
    events: list[WorkflowLedgerPreviewEvent] = []
    for action in actions:
        if action.approval_id is not None:
            events.append(
                WorkflowLedgerPreviewEvent(
                    event_type="approval_requested",
                    step_id=action.step_id,
                    status="pending",
                    approval_id=action.approval_id,
                    connector_action=action.connector_action,
                )
            )
            events.append(
                WorkflowLedgerPreviewEvent(
                    event_type="approval_decision_placeholder",
                    step_id=action.step_id,
                    status="granted_or_denied_required",
                    approval_id=action.approval_id,
                    connector_action=action.connector_action,
                )
            )
        events.append(
            WorkflowLedgerPreviewEvent(
                event_type="broker_action_planned",
                step_id=action.step_id,
                status=action.broker_status or "planned",
                approval_id=action.approval_id,
                connector_action=action.connector_action,
                broker_provider_id=action.broker_provider_id,
            )
        )
        events.append(
            WorkflowLedgerPreviewEvent(
                event_type="outputs_simulated",
                step_id=action.step_id,
                status="simulated",
                approval_id=action.approval_id,
                connector_action=action.connector_action,
                broker_provider_id=action.broker_provider_id,
                outputs=action.simulated_outputs,
            )
        )
    return tuple(events)


def _action_for_step(ir: WorkflowIR, step_id: str) -> ActionCapability | None:
    step = ir.steps.get(step_id)
    if step is None or step.uses is None:
        return None
    connector = ir.connectors.get(step.uses.connector_id)
    if connector is None:
        return None
    return connector.actions.get(step.uses.action_id)
