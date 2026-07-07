"""Deployment intent export for appv24 workflow packages."""

from __future__ import annotations

from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_contracts import builtin_connector_contract_catalog
from appv24.workflows.credential_refs import credential_env_name
from appv24.workflows.deployment_intent import WorkflowDeploymentIntent
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.ir import WorkflowIR
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.loader import load_package_documents
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.validator import validate_workflow_documents


def export_deployment_intent(ir: WorkflowIR, documents: PackageDocuments) -> WorkflowDeploymentIntent:
    connector_scopes: dict[str, tuple[str, ...]] = {}
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_scopes[str(permission["connector"])] = tuple(
            str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str)
        )
    contracts = builtin_connector_contract_catalog()
    step_actions = tuple(step.uses.dotted for step in ir.steps.values() if step.uses is not None)
    env_requirements: list[str] = []
    secret_refs: list[str] = []
    for connector in sorted(connector_scopes):
        env_name = credential_env_name(connector)
        env_requirements.append(f"APPV24_{env_name}_CREDENTIAL_REF")
        env_requirements.append(f"APPV24_{env_name}_SCOPES")
        secret_refs.append(f"{connector}.credential")
    connector_targets: dict[str, tuple[str, ...]] = {}
    for action_ref in step_actions:
        contract = contracts.get(action_ref)
        if contract is None:
            continue
        for secret in contract.secrets:
            env_requirements.append(secret.env_name)
            secret_refs.append(secret.env_name)
        targets = tuple(
            value
            for value in (
                contract.target_policy.allow_env,
                contract.target_policy.allowed_chat_ids_env,
            )
            if value
        )
        if targets:
            env_requirements.extend(targets)
            connector_targets[action_ref] = targets
    return WorkflowDeploymentIntent(
        workflow_name=ir.name,
        package_fingerprint=fingerprint_package_documents(documents),
        runtime_modes=_runtime_modes(step_actions),
        env_requirements=_unique(env_requirements),
        secret_refs=_unique(secret_refs),
        webhook_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "webhook"),
        scheduler_triggers=tuple(trigger.trigger_id for trigger in ir.triggers if trigger.trigger_type == "schedule"),
        connector_scopes=connector_scopes,
        connector_targets=connector_targets,
        approval_ids=tuple(sorted(ir.approvals)),
        approval_policy=_approval_policy_lines(ir),
        idempotency_required_for=_idempotency_required_for(ir),
        state_requirements=_state_requirements(ir),
        audit_requirements=("events.jsonl", "report.json", "trace.json", "approvals.json"),
        local_runtime_commands=_local_runtime_commands(ir),
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
    lines.append(f"package fingerprint: {intent.package_fingerprint}")
    _append_section(lines, "runtime modes", intent.runtime_modes)
    _append_section(lines, "env requirements", intent.env_requirements)
    _append_section(lines, "secret refs", intent.secret_refs)
    _append_section(lines, "webhook ingress", tuple(f"webhook: {trigger}" for trigger in intent.webhook_triggers))
    _append_section(lines, "schedules", tuple(f"schedule: {trigger}" for trigger in intent.scheduler_triggers))
    _append_section(
        lines,
        "connector scopes",
        tuple(f"{connector}: {', '.join(scopes) if scopes else '<none>'}" for connector, scopes in sorted(intent.connector_scopes.items())),
    )
    _append_section(
        lines,
        "connector targets",
        tuple(f"{action}: {', '.join(targets)}" for action, targets in sorted(intent.connector_targets.items())),
    )
    _append_section(lines, "approval policy", intent.approval_policy)
    _append_section(lines, "idempotency required for", intent.idempotency_required_for)
    _append_section(lines, "state requirements", intent.state_requirements)
    _append_section(lines, "audit requirements", intent.audit_requirements)
    _append_section(lines, "local runtime commands", intent.local_runtime_commands)
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


def _runtime_modes(step_actions: tuple[str, ...]) -> tuple[str, ...]:
    contracts = builtin_connector_contract_catalog()
    modes = {"local"}
    for action_ref in step_actions:
        contract = contracts.get(action_ref)
        if contract is None:
            continue
        modes.update(mode.value for mode in contract.live_modes)
    order = ("local", "live-read", "live-draft", "live-production")
    return tuple(mode for mode in order if mode in modes)


def _idempotency_required_for(ir: WorkflowIR) -> tuple[str, ...]:
    values: list[str] = []
    for step in ir.steps.values():
        if step.uses is None:
            continue
        connector = ir.connectors.get(step.uses.connector_id)
        action = connector.actions.get(step.uses.action_id) if connector is not None else None
        if action is not None and action.idempotency_key_fields:
            values.append(f"{step.step_id}:{step.uses.dotted}")
    return tuple(values)


def _state_requirements(ir: WorkflowIR) -> tuple[str, ...]:
    requirements = ["workflow_run_store"]
    if ir.approvals:
        requirements.append("approval_ledger")
    if _idempotency_required_for(ir):
        requirements.append("idempotency_ledger")
    trigger_types = {trigger.trigger_type for trigger in ir.triggers}
    if trigger_types.intersection({"schedule", "poll", "webhook"}):
        requirements.append("activation_state")
    if "webhook" in trigger_types:
        requirements.append("webhook_replay_store")
    if "poll" in trigger_types:
        requirements.append("cursor_store")
    return tuple(requirements)


def _local_runtime_commands(ir: WorkflowIR) -> tuple[str, ...]:
    trigger_types = {trigger.trigger_type for trigger in ir.triggers}
    commands: list[str] = []
    if "schedule" in trigger_types or "poll" in trigger_types:
        commands.append("/workflow-activation-loop <ticks> [interval-seconds]")
    if "webhook" in trigger_types:
        commands.append("/workflow-webhook-serve <max-requests> [host] [port]")
    return tuple(commands)


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(sorted(value for value in values if value)))
