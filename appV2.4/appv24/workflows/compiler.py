"""Compile appv24 workflow package documents into typed IR."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.capabilities import build_connector_capabilities
from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.ir import (
    ApprovalRequirement,
    ConnectorCapability,
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
        issues.append(
            WorkflowIssue(
                "step_limit_exceeded",
                "workflow.yaml",
                ("steps",),
                "workflow has more than 100 steps",
            )
        )

    capabilities = build_connector_capabilities(documents.connectors, documents.permissions)
    approvals = _build_approvals(documents.permissions)
    steps: dict[str, WorkflowStep] = {}
    seen: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        step_id = str(raw_step.get("id", ""))
        if step_id in seen:
            issues.append(
                WorkflowIssue(
                    "duplicate_step",
                    "workflow.yaml",
                    ("steps", index, "id"),
                    f"duplicate step id: {step_id}",
                )
            )
            continue
        seen.add(step_id)
        step, step_issues = _compile_step(index, raw_step, capabilities, approvals)
        issues.extend(step_issues)
        if step is not None:
            steps[step_id] = step

    known_steps = set(steps)
    entrypoint = str(workflow.get("entrypoint", ""))
    if entrypoint not in known_steps:
        issues.append(
            WorkflowIssue(
                "unknown_entrypoint",
                "workflow.yaml",
                ("entrypoint",),
                f"entrypoint does not exist: {entrypoint}",
            )
        )
    for step in steps.values():
        for target in step.outbound:
            if target not in known_steps:
                issues.append(
                    WorkflowIssue(
                        "unknown_transition",
                        "workflow.yaml",
                        ("steps", step.step_id),
                        f"step {step.step_id} points to {target}",
                    )
                )

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
    capabilities: Mapping[str, ConnectorCapability],
    approvals: Mapping[str, ApprovalRequirement],
) -> tuple[WorkflowStep | None, list[WorkflowIssue]]:
    issues: list[WorkflowIssue] = []
    step_id = str(raw_step.get("id", ""))
    kind = _step_kind(raw_step)
    uses = _connector_ref(raw_step.get("uses"))
    if uses is not None:
        connector = capabilities.get(uses.connector_id)
        if connector is None:
            issues.append(
                WorkflowIssue(
                    "unknown_connector",
                    "workflow.yaml",
                    ("steps", index, "uses"),
                    f"unknown connector: {uses.connector_id}",
                )
            )
        elif uses.action_id not in connector.actions:
            issues.append(
                WorkflowIssue(
                    "unknown_action",
                    "workflow.yaml",
                    ("steps", index, "uses"),
                    f"unknown action: {uses.dotted}",
                )
            )
    approval = raw_step.get("approval")
    approval_id = approval if isinstance(approval, str) else None
    if approval_id is not None and approval_id not in approvals:
        issues.append(
            WorkflowIssue(
                "unknown_approval",
                "workflow.yaml",
                ("steps", index, "approval"),
                f"unknown approval: {approval_id}",
            )
        )
    next_step = raw_step.get("next") if isinstance(raw_step.get("next"), str) else None
    raw_branches = raw_step.get("branches")
    branches = (
        {str(key): str(value) for key, value in raw_branches.items() if isinstance(value, str)}
        if isinstance(raw_branches, dict)
        else {}
    )
    outbound = tuple(target for target in (next_step, *branches.values()) if isinstance(target, str))
    return (
        WorkflowStep(
            step_id=step_id,
            kind=kind,
            uses=uses,
            side_effect_class=raw_step.get("side_effect_class")
            if isinstance(raw_step.get("side_effect_class"), str)
            else None,
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
