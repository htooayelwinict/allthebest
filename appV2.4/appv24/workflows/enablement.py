"""Package-level workflow enablement reports for appv24."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from appv24.workflows.approval_ledger import ApprovalPlan, build_approval_plan
from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.ir import StepKind, WorkflowIR
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.quality_gate import WorkflowFixtureQualityReport, check_workflow_quality_documents


@dataclass(frozen=True)
class CapabilityPlanItem:
    step_id: str
    action: str
    side_effect: bool
    side_effect_class: str | None
    execution_mode: str | None
    approval_required: bool | None
    approval_id: str | None
    dry_run_supported: bool | None
    idempotency_key_fields: tuple[str, ...]
    allowed_outputs: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowEnablementV1Report:
    workflow_name: str
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    quality_ok: bool
    capability_plan: tuple[CapabilityPlanItem, ...]
    approval_plan: ApprovalPlan
    fixtures: tuple[WorkflowFixtureQualityReport, ...]
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.validation_ok and self.compile_ok and self.policy_ok and self.quality_ok and not self.issues

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_workflow_enablement_report(
    documents: PackageDocuments,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowEnablementV1Report:
    workflow_name = str(documents.workflow.get("name", ""))
    quality = check_workflow_quality_documents(documents, broker=broker)
    capability_plan: tuple[CapabilityPlanItem, ...] = ()
    approval_plan = ApprovalPlan(workflow_name=workflow_name, items=())

    compiled = compile_workflow_documents(documents)
    if compiled.ir is not None:
        workflow_name = compiled.ir.name
        capability_plan = tuple(_capability_plan(compiled.ir))
        approval_plan = build_approval_plan(compiled.ir)

    return WorkflowEnablementV1Report(
        workflow_name=workflow_name,
        validation_ok=quality.validation_ok,
        compile_ok=quality.compile_ok,
        policy_ok=quality.policy_ok,
        quality_ok=quality.ok,
        capability_plan=capability_plan,
        approval_plan=approval_plan,
        fixtures=quality.fixture_reports,
        issues=tuple(issue.message for issue in quality.issues),
    )


def _capability_plan(ir: WorkflowIR) -> list[CapabilityPlanItem]:
    items: list[CapabilityPlanItem] = []
    for step in ir.steps.values():
        if step.kind is StepKind.TERMINAL or step.uses is None:
            continue
        connector = ir.connectors.get(step.uses.connector_id)
        action = connector.actions.get(step.uses.action_id) if connector is not None else None
        if action is None:
            continue
        items.append(
            CapabilityPlanItem(
                step_id=step.step_id,
                action=step.uses.dotted,
                side_effect=action.side_effect,
                side_effect_class=action.side_effect_class,
                execution_mode=action.execution_mode,
                approval_required=action.approval_required,
                approval_id=step.approval,
                dry_run_supported=action.dry_run_supported,
                idempotency_key_fields=action.idempotency_key_fields,
                allowed_outputs=action.allowed_outputs,
            )
        )
    return items
