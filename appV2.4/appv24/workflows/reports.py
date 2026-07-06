"""Workflow enablement reports for appv24 passive simulations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.quality_gate import check_workflow_quality_documents
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
    quality = check_workflow_quality_documents(documents, broker=broker)
    if not quality.ok:
        return WorkflowEnablementReport(
            workflow_name=workflow_name,
            validation_ok=quality.validation_ok,
            compile_ok=quality.compile_ok,
            policy_ok=quality.policy_ok,
            simulation_status="blocked",
            terminal_step=None,
            simulated_side_effects=(),
            issues=tuple(issue.message for issue in quality.issues),
        )

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
