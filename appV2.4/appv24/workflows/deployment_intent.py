"""Deployment intent data contracts for workflow packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any


@dataclass(frozen=True)
class WorkflowDeploymentIntent:
    workflow_name: str
    package_fingerprint: str
    runtime_modes: tuple[str, ...]
    env_requirements: tuple[str, ...]
    secret_refs: tuple[str, ...]
    webhook_triggers: tuple[str, ...]
    scheduler_triggers: tuple[str, ...]
    connector_scopes: dict[str, tuple[str, ...]]
    connector_targets: dict[str, tuple[str, ...]]
    connector_env_requirements: tuple[str, ...]
    middleware_ids: tuple[str, ...]
    middleware_env_requirements: tuple[str, ...]
    approval_ids: tuple[str, ...]
    approval_policy: tuple[str, ...]
    idempotency_required_for: tuple[str, ...]
    state_requirements: tuple[str, ...]
    audit_requirements: tuple[str, ...]
    local_runtime_commands: tuple[str, ...]
    rollback_required: bool


def deployment_intent_to_dict(intent: WorkflowDeploymentIntent) -> dict[str, Any]:
    data = asdict(intent)
    data["version"] = "appv24.deployment_intent/v1"
    data["runtime_modes"] = list(intent.runtime_modes)
    data["env_requirements"] = list(intent.env_requirements)
    data["secret_refs"] = list(intent.secret_refs)
    data["webhook_triggers"] = list(intent.webhook_triggers)
    data["scheduler_triggers"] = list(intent.scheduler_triggers)
    data["connector_scopes"] = {connector: list(scopes) for connector, scopes in sorted(intent.connector_scopes.items())}
    data["connector_targets"] = {action: list(targets) for action, targets in sorted(intent.connector_targets.items())}
    data["connector_env_requirements"] = list(intent.connector_env_requirements)
    data["middleware_ids"] = list(intent.middleware_ids)
    data["middleware_env_requirements"] = list(intent.middleware_env_requirements)
    data["approval_ids"] = list(intent.approval_ids)
    data["approval_policy"] = list(intent.approval_policy)
    data["idempotency_required_for"] = list(intent.idempotency_required_for)
    data["state_requirements"] = list(intent.state_requirements)
    data["audit_requirements"] = list(intent.audit_requirements)
    data["local_runtime_commands"] = list(intent.local_runtime_commands)
    data["rollback_required"] = intent.rollback_required
    return {"version": data.pop("version"), **data}


def deployment_intent_to_json(intent: WorkflowDeploymentIntent) -> str:
    return json.dumps(deployment_intent_to_dict(intent), sort_keys=True, indent=2)
