"""Deployment intent export for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass

from appv24.workflows.ir import WorkflowIR
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class WorkflowDeploymentIntent:
    workflow_name: str
    env_requirements: tuple[str, ...]
    secret_refs: tuple[str, ...]
    webhook_triggers: tuple[str, ...]
    scheduler_triggers: tuple[str, ...]
    connector_scopes: dict[str, tuple[str, ...]]
    approval_ids: tuple[str, ...]
    rollback_required: bool


def export_deployment_intent(ir: WorkflowIR, documents: PackageDocuments) -> WorkflowDeploymentIntent:
    connector_scopes: dict[str, tuple[str, ...]] = {}
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_scopes[str(permission["connector"])] = tuple(
            str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str)
        )
    return WorkflowDeploymentIntent(
        workflow_name=ir.name,
        env_requirements=tuple(f"APPV24_{connector.upper()}_CREDENTIAL_REF" for connector in sorted(connector_scopes)),
        secret_refs=tuple(f"{connector}.credential" for connector in sorted(connector_scopes)),
        webhook_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "webhook"),
        scheduler_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "schedule"),
        connector_scopes=connector_scopes,
        approval_ids=tuple(sorted(ir.approvals)),
        rollback_required=bool(documents.rollback_text and documents.rollback_text.strip()),
    )
