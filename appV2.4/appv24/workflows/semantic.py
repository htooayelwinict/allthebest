"""Semantic checks for appv24 workflow packages."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping

from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.loader import PackageDocuments

MUTATING_SIDE_EFFECTS = {"create", "update", "delete", "send", "publish", "provision"}
SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|credential)", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")


def validate_semantics(documents: PackageDocuments) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    workflow = documents.workflow
    connectors = _connector_actions(documents.connectors)
    approval_ids = _approval_ids(documents.permissions)
    permission_actions = _permission_actions(documents.permissions)
    steps = _steps(workflow)
    step_ids = [str(step.get("id", "")) for step in steps]
    known_steps = set(step_ids)

    issues.extend(_duplicate_step_issues(step_ids))
    issues.extend(_entrypoint_issues(workflow, known_steps))
    issues.extend(_transition_issues(steps, known_steps))
    issues.extend(_connector_reference_issues(steps, connectors, permission_actions))

    mutating_steps = _mutating_steps(steps, connectors)
    issues.extend(_approval_issues(mutating_steps, approval_ids))
    issues.extend(_raw_secret_issues("workflow.yaml", workflow))
    issues.extend(_raw_secret_issues("connectors.yaml", documents.connectors))
    issues.extend(_raw_secret_issues("permissions.yaml", documents.permissions))
    for fixture_name, fixture in documents.dry_run_fixtures.items():
        issues.extend(_raw_secret_issues(f"dry_run_fixtures/{fixture_name}", fixture))

    if mutating_steps:
        if not documents.rollback_text or not documents.rollback_text.strip():
            issues.append(WorkflowIssue("missing_rollback", "rollback.md", (), "mutating workflows require rollback.md"))
        for required_fixture in ("happy_path.yaml", "rejection_path.yaml"):
            if required_fixture not in documents.dry_run_fixtures:
                issues.append(
                    WorkflowIssue(
                        "missing_fixture",
                        "dry_run_fixtures",
                        (),
                        f"mutating workflows require dry_run_fixtures/{required_fixture}",
                    )
                )

    return list(sorted_issues(issues))


def _steps(workflow: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = workflow.get("steps")
    return [step for step in value if isinstance(step, dict)] if isinstance(value, list) else []


def _duplicate_step_issues(step_ids: list[str]) -> list[WorkflowIssue]:
    counts = Counter(step_ids)
    return [
        WorkflowIssue("duplicate_step", "workflow.yaml", ("steps",), f"duplicate step id: {step_id}")
        for step_id, count in sorted(counts.items())
        if step_id and count > 1
    ]


def _entrypoint_issues(workflow: Mapping[str, Any], known_steps: set[str]) -> list[WorkflowIssue]:
    entrypoint = workflow.get("entrypoint")
    if isinstance(entrypoint, str) and entrypoint not in known_steps:
        return [WorkflowIssue("unknown_entrypoint", "workflow.yaml", ("entrypoint",), f"entrypoint does not exist: {entrypoint}")]
    return []


def _transition_issues(steps: Iterable[Mapping[str, Any]], known_steps: set[str]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for index, step in enumerate(steps):
        source = str(step.get("id", f"steps[{index}]"))
        targets: list[str] = []
        next_step = step.get("next")
        if isinstance(next_step, str):
            targets.append(next_step)
        branches = step.get("branches")
        if isinstance(branches, dict):
            targets.extend(str(target) for target in branches.values())
        for target in targets:
            if target not in known_steps:
                issues.append(
                    WorkflowIssue(
                        "unknown_transition",
                        "workflow.yaml",
                        ("steps", index),
                        f"step {source} points to {target}",
                    )
                )
    return issues


def _connector_actions(connectors_document: Mapping[str, Any]) -> dict[str, dict[str, Mapping[str, Any]]]:
    connectors: dict[str, dict[str, Mapping[str, Any]]] = {}
    for connector in connectors_document.get("connectors", []):
        if not isinstance(connector, dict):
            continue
        connector_id = connector.get("id")
        if not isinstance(connector_id, str):
            continue
        actions: dict[str, Mapping[str, Any]] = {}
        for action in connector.get("actions", []):
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                actions[str(action["id"])] = action
        connectors[connector_id] = actions
    return connectors


def _connector_reference_issues(
    steps: Iterable[Mapping[str, Any]],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
    permission_actions: Mapping[str, set[str]],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for index, step in enumerate(steps):
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        connector_id, _, action_id = uses.partition(".")
        if connector_id not in connectors:
            issues.append(
                WorkflowIssue("unknown_connector", "workflow.yaml", ("steps", index, "uses"), f"unknown connector: {connector_id}")
            )
            continue
        if action_id not in connectors[connector_id]:
            issues.append(WorkflowIssue("unknown_action", "workflow.yaml", ("steps", index, "uses"), f"unknown action: {uses}"))
            continue
        allowed_actions = permission_actions.get(connector_id, set())
        if action_id not in allowed_actions and "*" not in allowed_actions:
            issues.append(
                WorkflowIssue(
                    "permission_missing_action",
                    "permissions.yaml",
                    ("permissions",),
                    f"permissions for {connector_id} do not allow {action_id}",
                )
            )
    return issues


def _permission_actions(permissions_document: Mapping[str, Any]) -> dict[str, set[str]]:
    actions: dict[str, set[str]] = {}
    for permission in permissions_document.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        raw_actions = permission.get("actions", [])
        actions[connector_id] = {str(action) for action in raw_actions if isinstance(action, str)}
    return actions


def _approval_ids(permissions_document: Mapping[str, Any]) -> set[str]:
    approvals = permissions_document.get("approvals", [])
    return {str(approval["id"]) for approval in approvals if isinstance(approval, dict) and isinstance(approval.get("id"), str)}


def _mutating_steps(
    steps: Iterable[Mapping[str, Any]],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[Mapping[str, Any]]:
    mutating: list[Mapping[str, Any]] = []
    for step in steps:
        side_effect_class = step.get("side_effect_class")
        uses = step.get("uses")
        action_side_effect = False
        if isinstance(uses, str):
            connector_id, _, action_id = uses.partition(".")
            action = connectors.get(connector_id, {}).get(action_id, {})
            action_side_effect = bool(action.get("side_effect"))
        if side_effect_class in MUTATING_SIDE_EFFECTS or action_side_effect:
            mutating.append(step)
    return mutating


def _approval_issues(mutating_steps: Iterable[Mapping[str, Any]], approval_ids: set[str]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for step in mutating_steps:
        step_id = str(step.get("id", "unknown"))
        side_effect_class = step.get("side_effect_class")
        approval = step.get("approval")
        if side_effect_class not in MUTATING_SIDE_EFFECTS:
            issues.append(WorkflowIssue("missing_side_effect_class", "workflow.yaml", ("steps", step_id), f"{step_id} requires side_effect_class"))
        if not isinstance(approval, str) or approval not in approval_ids:
            issues.append(WorkflowIssue("missing_approval", "workflow.yaml", ("steps", step_id), f"{step_id} requires an explicit approval gate"))
    return issues


def _raw_secret_issues(filename: str, value: Any, path: tuple[str | int, ...] = ()) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    if isinstance(value, dict):
        if set(value) == {"secretRef"} and isinstance(value.get("secretRef"), str):
            return []
        for key, child in value.items():
            child_path = (*path, str(key))
            if SECRET_KEY_RE.search(str(key)) and isinstance(child, str):
                issues.append(WorkflowIssue("raw_secret", filename, child_path, "use secretRef instead of inline secret value"))
            issues.extend(_raw_secret_issues(filename, child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_raw_secret_issues(filename, child, (*path, index)))
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        issues.append(WorkflowIssue("raw_secret", filename, path, "inline secret-looking value is not allowed"))
    return issues
