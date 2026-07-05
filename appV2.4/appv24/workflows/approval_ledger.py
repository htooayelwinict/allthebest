"""Approval and execution contract ledger helpers for appv24 workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from appv24.workflows.ir import WorkflowIR
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS


@dataclass(frozen=True)
class ApprovalPlanItem:
    step_id: str
    action: str
    side_effect_class: str
    approval_id: str
    idempotency_key_fields: tuple[str, ...]
    allowed_outputs: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalPlan:
    workflow_name: str
    items: tuple[ApprovalPlanItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_approval_plan(ir: WorkflowIR) -> ApprovalPlan:
    items: list[ApprovalPlanItem] = []
    for step in ir.steps.values():
        if step.uses is None or step.side_effect_class not in MUTATING_SIDE_EFFECTS:
            continue
        connector = ir.connectors.get(step.uses.connector_id)
        action = connector.actions.get(step.uses.action_id) if connector is not None else None
        if action is None or step.approval is None:
            continue
        items.append(
            ApprovalPlanItem(
                step_id=step.step_id,
                action=step.uses.dotted,
                side_effect_class=step.side_effect_class,
                approval_id=step.approval,
                idempotency_key_fields=action.idempotency_key_fields,
                allowed_outputs=action.allowed_outputs,
            )
        )
    return ApprovalPlan(workflow_name=ir.name, items=tuple(items))


@dataclass(frozen=True)
class ApprovalLedgerEvent:
    event_id: str
    workflow_name: str
    fixture_name: str
    step_id: str
    action: str
    status: str
    approval_id: str | None
    idempotency_key: str | None
    issues: tuple[str, ...]


def encode_ledger_event(event: ApprovalLedgerEvent) -> str:
    return json.dumps(asdict(event), sort_keys=True, separators=(",", ":"))


def decode_ledger_event(line: str) -> ApprovalLedgerEvent:
    data = json.loads(line)
    return ApprovalLedgerEvent(
        event_id=str(data["event_id"]),
        workflow_name=str(data["workflow_name"]),
        fixture_name=str(data["fixture_name"]),
        step_id=str(data["step_id"]),
        action=str(data["action"]),
        status=str(data["status"]),
        approval_id=data["approval_id"] if isinstance(data.get("approval_id"), str) else None,
        idempotency_key=data["idempotency_key"] if isinstance(data.get("idempotency_key"), str) else None,
        issues=tuple(str(issue) for issue in data.get("issues", [])),
    )
