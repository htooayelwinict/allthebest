"""Deployment intent export for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.credential_refs import credential_env_name
from appv24.workflows.ir import WorkflowIR
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.loader import load_package_documents
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.validator import validate_workflow_documents


@dataclass(frozen=True)
class WorkflowDeploymentIntent:
    workflow_name: str
    env_requirements: tuple[str, ...]
    secret_refs: tuple[str, ...]
    webhook_triggers: tuple[str, ...]
    scheduler_triggers: tuple[str, ...]
    connector_scopes: dict[str, tuple[str, ...]]
    approval_ids: tuple[str, ...]
    approval_policy: tuple[str, ...]
    rollback_required: bool


def export_deployment_intent(ir: WorkflowIR, documents: PackageDocuments) -> WorkflowDeploymentIntent:
    connector_scopes: dict[str, tuple[str, ...]] = {}
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_scopes[str(permission["connector"])] = tuple(
            str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str)
        )
    env_requirements: list[str] = []
    for connector in sorted(connector_scopes):
        env_name = credential_env_name(connector)
        env_requirements.append(f"APPV24_{env_name}_CREDENTIAL_REF")
        env_requirements.append(f"APPV24_{env_name}_SCOPES")
    return WorkflowDeploymentIntent(
        workflow_name=ir.name,
        env_requirements=tuple(env_requirements),
        secret_refs=tuple(f"{connector}.credential" for connector in sorted(connector_scopes)),
        webhook_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "webhook"),
        scheduler_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "schedule"),
        connector_scopes=connector_scopes,
        approval_ids=tuple(sorted(ir.approvals)),
        approval_policy=_approval_policy_lines(ir),
        rollback_required=bool(documents.rollback_text and documents.rollback_text.strip()),
    )


def export_deployment_intent_from_path(package_path: str | Path) -> WorkflowDeploymentIntent:
    documents = load_package_documents(package_path)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        issues = "; ".join(issue.message for issue in validation.issues[:5])
        raise ValueError(f"workflow package is not exportable: {issues}")
    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        issues = "; ".join(issue.message for issue in compiled.issues[:5])
        raise ValueError(f"workflow package is not exportable: {issues}")
    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        issues = "; ".join(issue.message for issue in policy.issues[:5])
        raise ValueError(f"workflow package is not exportable: {issues}")
    return export_deployment_intent(compiled.ir, documents)


def format_deployment_intent(intent: WorkflowDeploymentIntent) -> str:
    lines = [f"Deployment intent: {intent.workflow_name}"]
    _append_section(lines, "env requirements", intent.env_requirements)
    _append_section(lines, "secret refs", intent.secret_refs)
    _append_section(lines, "webhook ingress", tuple(f"webhook: {trigger}" for trigger in intent.webhook_triggers))
    _append_section(lines, "schedules", tuple(f"schedule: {trigger}" for trigger in intent.scheduler_triggers))
    _append_section(
        lines,
        "connector scopes",
        tuple(f"{connector}: {', '.join(scopes) if scopes else '<none>'}" for connector, scopes in sorted(intent.connector_scopes.items())),
    )
    _append_section(lines, "approval policy", intent.approval_policy)
    lines.append(f"rollback required: {'yes' if intent.rollback_required else 'no'}")
    return "\n".join(lines)


def _approval_policy_lines(ir: WorkflowIR) -> tuple[str, ...]:
    grouped: dict[str, list[str]] = {}
    for step in ir.steps.values():
        if step.approval is None:
            continue
        action = step.uses.dotted if step.uses is not None else "<manual>"
        grouped.setdefault(step.approval, []).append(f"{step.step_id}({action})")
    return tuple(f"{approval}: {', '.join(grouped[approval])}" for approval in sorted(grouped))


def _append_section(lines: list[str], title: str, values: tuple[str, ...]) -> None:
    lines.append(f"{title}:")
    if values:
        lines.extend(f"- {value}" for value in values)
    else:
        lines.append("- none")
