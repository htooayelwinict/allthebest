"""Semantic checks for appv24 workflow packages."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping

from appv24.workflows.catalog import built_in_capability_catalog
from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.loader import PackageDocuments

MUTATING_SIDE_EFFECTS = {"create", "update", "delete", "send", "publish", "provision"}
BROKERED_IDEMPOTENCY_CLASSES = {"create", "send", "publish", "provision"}
SIDE_EFFECT_CONTRACT_FIELDS = ("side_effect_class", "execution_mode", "approval_required", "dry_run_supported")
DESIGN_CONTRACT_FIELDS = (
    "intent",
    "trigger",
    "inputs",
    "outputs",
    "assumptions",
    "side_effects",
    "approval_gates",
    "failure_modes",
    "rollback",
)
DESIGN_CONTRACT_EXPECTED = (
    "design with intent, trigger, inputs, outputs, assumptions, side_effects, "
    "approval_gates, failure_modes, and rollback"
)
SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|credential)", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")
SECRET_REF_KEY_RE = re.compile(r"^secret[_-]?ref$", re.IGNORECASE)
ROLLBACK_COMMAND_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:[`$>#]\s*)?(?:curl|gh\s+api|aws|gcloud|az|kubectl|terraform|rm\s+-rf)\b",
)
ROLLBACK_DELETE_SCOPE_RE = re.compile(r"\bdelete_repo\b", re.IGNORECASE)
ROLLBACK_SECRET_PLACEHOLDER_RE = re.compile(r"\$\{\s*secrets\.[^}]+\}", re.IGNORECASE)
ROLLBACK_AUTH_HEADER_RE = re.compile(r"\bauthorization\s*:\s*bearer\b", re.IGNORECASE)
ROLLBACK_HTTP_DELETE_RE = re.compile(r"\b(?:-X\s+)?DELETE\b")
URL_METADATA_KEYS = {"token_url"}


def validate_semantics(documents: PackageDocuments) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    workflow = documents.workflow
    connectors = _connector_actions(documents.connectors)
    approval_ids = _approval_ids(documents.permissions)
    approval_required_for = _approval_required_for(documents.permissions)
    permission_actions = _permission_actions(documents.permissions)
    permission_scopes = _permission_scopes(documents.permissions)
    steps = _steps(workflow)
    step_ids = [str(step.get("id", "")) for step in steps]
    known_steps = set(step_ids)
    action_entries = _connector_action_entries(documents.connectors)
    steps_by_action = _steps_by_action_use(steps)

    issues.extend(_design_contract_issues(workflow))
    issues.extend(_duplicate_step_issues(step_ids))
    issues.extend(_entrypoint_issues(workflow, known_steps))
    issues.extend(_transition_issues(steps, known_steps))
    issues.extend(_connector_reference_issues(steps, connectors, permission_actions, permission_scopes))
    issues.extend(_catalog_issues(action_entries))
    issues.extend(_side_effect_contract_issues(action_entries, steps_by_action, documents.permissions))

    mutating_steps = _mutating_steps(steps, connectors)
    issues.extend(_approval_issues(mutating_steps, approval_ids, approval_required_for))
    issues.extend(_raw_secret_issues("workflow.yaml", workflow))
    issues.extend(_raw_secret_issues("connectors.yaml", documents.connectors))
    issues.extend(_raw_secret_issues("permissions.yaml", documents.permissions))
    for fixture_name, fixture in documents.dry_run_fixtures.items():
        issues.extend(_raw_secret_issues(f"dry_run_fixtures/{fixture_name}", fixture))
    issues.extend(_rollback_safety_issues(documents.rollback_text or ""))

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


def _design_contract_issues(workflow: Mapping[str, Any]) -> list[WorkflowIssue]:
    design = workflow.get("design")
    if not isinstance(design, dict):
        return [
            WorkflowIssue(
                "missing_design_contract",
                "workflow.yaml",
                ("design",),
                "workflow.yaml requires design metadata",
                expected=DESIGN_CONTRACT_EXPECTED,
                repair_hint=(
                    "Add workflow.yaml design metadata with intent, trigger, inputs, outputs, "
                    "assumptions, side_effects, approval_gates, failure_modes, and rollback."
                ),
            )
        ]

    issues: list[WorkflowIssue] = []
    list_fields = {
        "inputs",
        "outputs",
        "assumptions",
        "side_effects",
        "approval_gates",
        "failure_modes",
        "rollback",
    }
    for field in DESIGN_CONTRACT_FIELDS:
        value = design.get(field)
        if field in list_fields:
            valid = isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
            expected = "non-empty list of strings"
        else:
            valid = isinstance(value, str) and bool(value.strip())
            expected = "non-empty string"
        if not valid:
            issues.append(
                WorkflowIssue(
                    "missing_design_contract",
                    "workflow.yaml",
                    ("design", field),
                    f"workflow.yaml design requires {field}",
                    expected=expected,
                    repair_hint=f"Set workflow.yaml design.{field} to a {expected}.",
                )
            )
    return issues


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


def _connector_action_entries(connectors_document: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    entries: dict[tuple[str, str], Mapping[str, Any]] = {}
    for connector in connectors_document.get("connectors", []):
        if not isinstance(connector, dict) or not isinstance(connector.get("id"), str):
            continue
        connector_id = str(connector["id"])
        for action in connector.get("actions", []):
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                entries[(connector_id, str(action["id"]))] = action
    return entries


def _catalog_issues(actions: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[WorkflowIssue]:
    catalog = built_in_capability_catalog()
    issues: list[WorkflowIssue] = []
    for (connector_id, action_id), action in sorted(actions.items()):
        action_ref = f"{connector_id}.{action_id}"
        if action.get("custom") is True and action.get("execution_mode") == "design_only":
            continue
        capability = catalog.get(action_ref)
        if capability is None:
            if action.get("side_effect") is True or action.get("execution_mode") in {"brokered", "manual"}:
                issues.append(
                    WorkflowIssue(
                        "unknown_catalog_action",
                        "connectors.yaml",
                        (),
                        f"{action_ref} is not in the built-in capability catalog",
                        repair_hint=(
                            "Use a built-in catalog action or mark custom design-only actions "
                            "with custom: true and execution_mode: design_only."
                        ),
                    )
                )
            continue

        if not capability.side_effect:
            if action.get("side_effect") != capability.side_effect:
                issues.append(
                    WorkflowIssue(
                        "catalog_metadata_mismatch",
                        "connectors.yaml",
                        (),
                        f"{action_ref} side_effect must match catalog value {capability.side_effect!r}",
                    )
                )
            continue

        expected = {
            "side_effect": capability.side_effect,
            "side_effect_class": capability.side_effect_class,
            "execution_mode": capability.execution_mode,
            "approval_required": capability.approval_required,
            "dry_run_supported": capability.dry_run_supported,
        }
        for field, expected_value in expected.items():
            if field not in action and action.get("side_effect") is not True:
                continue
            if action.get(field) != expected_value:
                issues.append(
                    WorkflowIssue(
                        "catalog_metadata_mismatch",
                        "connectors.yaml",
                        (),
                        f"{action_ref} {field} must match catalog value {expected_value!r}",
                    )
                )
    return issues


def _steps_by_action_use(steps: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    used: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for step in steps:
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        connector_id, _, action_id = uses.partition(".")
        used.setdefault((connector_id, action_id), []).append(step)
    return used


def _connector_reference_issues(
    steps: Iterable[Mapping[str, Any]],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
    permission_actions: Mapping[str, set[str]],
    permission_scopes: Mapping[str, set[str]],
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
        granted_scopes = permission_scopes.get(connector_id, set())
        action = connectors[connector_id][action_id]
        for scope in sorted(str(scope) for scope in action.get("required_scopes", []) if isinstance(scope, str)):
            if scope not in granted_scopes and "*" not in granted_scopes:
                issues.append(
                    WorkflowIssue(
                        "permission_missing_scope",
                        "permissions.yaml",
                        ("permissions",),
                        f"permissions for {connector_id} do not grant {scope} required by {action_id}",
                    )
                )
    return issues


def _side_effect_contract_issues(
    actions: Mapping[tuple[str, str], Mapping[str, Any]],
    steps_by_action: Mapping[tuple[str, str], list[Mapping[str, Any]]],
    permissions_document: Mapping[str, Any],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    design_only_actions: set[tuple[str, str]] = set()

    for action_key, action in sorted(actions.items()):
        if action.get("side_effect") is not True:
            continue

        connector_id, action_id = action_key
        action_ref = f"{connector_id}.{action_id}"
        for field in SIDE_EFFECT_CONTRACT_FIELDS:
            if field not in action:
                issues.append(
                    WorkflowIssue(
                        "missing_side_effect_contract",
                        "connectors.yaml",
                        (),
                        f"{action_ref} requires {field}",
                    )
                )

        execution_mode = action.get("execution_mode")
        side_effect_class = action.get("side_effect_class")
        if execution_mode == "design_only":
            design_only_actions.add(action_key)
            for step in steps_by_action.get(action_key, []):
                step_id = str(step.get("id", "unknown"))
                issues.append(
                    WorkflowIssue(
                        "design_only_action_used",
                        "workflow.yaml",
                        ("steps", step_id),
                        f"{step_id} must not call design-only action {action_ref}",
                    )
                )
            continue

        if execution_mode != "brokered":
            continue

        if action.get("approval_required") is not True:
            issues.append(
                WorkflowIssue(
                    "invalid_side_effect_contract",
                    "connectors.yaml",
                    (),
                    f"{action_ref} brokered actions require approval_required: true",
                )
            )
        if action.get("dry_run_supported") is not True:
            issues.append(
                WorkflowIssue(
                    "invalid_side_effect_contract",
                    "connectors.yaml",
                    (),
                    f"{action_ref} brokered actions require dry_run_supported: true",
                )
            )

        allowed_outputs = _allowed_outputs(action)
        if not allowed_outputs:
            issues.append(
                WorkflowIssue(
                    "missing_side_effect_contract",
                    "connectors.yaml",
                    (),
                    f"{action_ref} requires outputs.allowed",
                )
            )
        elif any(output == "*" for output in allowed_outputs):
            issues.append(
                WorkflowIssue(
                    "invalid_side_effect_contract",
                    "connectors.yaml",
                    (),
                    f"{action_ref} outputs.allowed must not include wildcard",
                )
            )

        if side_effect_class in BROKERED_IDEMPOTENCY_CLASSES and not _idempotency_key_fields(action):
            issues.append(
                WorkflowIssue(
                    "missing_side_effect_contract",
                    "connectors.yaml",
                    (),
                    f"{action_ref} requires idempotency.key_fields",
                )
            )

    issues.extend(_design_only_permission_issues(design_only_actions, permissions_document))
    return issues


def _allowed_outputs(action: Mapping[str, Any]) -> list[str]:
    outputs = action.get("outputs")
    if not isinstance(outputs, dict):
        return []
    allowed = outputs.get("allowed")
    if not isinstance(allowed, list):
        return []
    return [output for output in allowed if isinstance(output, str) and output]


def _idempotency_key_fields(action: Mapping[str, Any]) -> list[str]:
    idempotency = action.get("idempotency")
    if not isinstance(idempotency, dict):
        return []
    key_fields = idempotency.get("key_fields")
    if not isinstance(key_fields, list):
        return []
    return [field for field in key_fields if isinstance(field, str) and field]


def _design_only_permission_issues(
    design_only_actions: set[tuple[str, str]],
    permissions_document: Mapping[str, Any],
) -> list[WorkflowIssue]:
    if not design_only_actions:
        return []

    issues: list[WorkflowIssue] = []
    for index, permission in enumerate(permissions_document.get("permissions", [])):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        actions = permission.get("actions", [])
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, str):
                continue
            if (connector_id, action) in design_only_actions and permission.get("design_only") is not True:
                issues.append(
                    WorkflowIssue(
                        "invalid_side_effect_contract",
                        "permissions.yaml",
                        ("permissions", index),
                        f"permissions for design-only action {connector_id}.{action} must set design_only: true",
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


def _permission_scopes(permissions_document: Mapping[str, Any]) -> dict[str, set[str]]:
    scopes: dict[str, set[str]] = {}
    for permission in permissions_document.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        raw_scopes = permission.get("scopes", [])
        scopes[connector_id] = {str(scope) for scope in raw_scopes if isinstance(scope, str)}
    return scopes


def _approval_ids(permissions_document: Mapping[str, Any]) -> set[str]:
    approvals = permissions_document.get("approvals", [])
    return {str(approval["id"]) for approval in approvals if isinstance(approval, dict) and isinstance(approval.get("id"), str)}


def _approval_required_for(permissions_document: Mapping[str, Any]) -> dict[str, set[str]]:
    requirements: dict[str, set[str]] = {}
    for approval in permissions_document.get("approvals", []):
        if not isinstance(approval, dict) or not isinstance(approval.get("id"), str):
            continue
        raw_required_for = approval.get("required_for", [])
        requirements[str(approval["id"])] = {str(step_id) for step_id in raw_required_for if isinstance(step_id, str)}
    return requirements


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


def _approval_issues(
    mutating_steps: Iterable[Mapping[str, Any]],
    approval_ids: set[str],
    approval_required_for: Mapping[str, set[str]],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for step in mutating_steps:
        step_id = str(step.get("id", "unknown"))
        side_effect_class = step.get("side_effect_class")
        approval = step.get("approval")
        if side_effect_class not in MUTATING_SIDE_EFFECTS:
            issues.append(WorkflowIssue("missing_side_effect_class", "workflow.yaml", ("steps", step_id), f"{step_id} requires side_effect_class"))
        if not isinstance(approval, str) or approval not in approval_ids:
            issues.append(WorkflowIssue("missing_approval", "workflow.yaml", ("steps", step_id), f"{step_id} requires an explicit approval gate"))
        elif step_id not in approval_required_for.get(approval, set()):
            issues.append(
                WorkflowIssue(
                    "missing_approval",
                    "workflow.yaml",
                    ("steps", step_id),
                    f"{step_id} requires approval {approval} to list the step in required_for",
                )
            )
    return issues


def _raw_secret_issues(filename: str, value: Any, path: tuple[str | int, ...] = ()) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    if isinstance(value, dict):
        if set(value) == {"secretRef"} and isinstance(value.get("secretRef"), str):
            if SECRET_VALUE_RE.search(value["secretRef"]):
                return [
                    WorkflowIssue(
                        "raw_secret",
                        filename,
                        (*path, "secretRef"),
                        "secretRef must reference a secret name, not contain a raw secret-looking value",
                    )
                ]
            return []
        for key, child in value.items():
            child_path = (*path, str(key))
            if _is_secret_ref_key(key) and isinstance(child, str):
                if SECRET_VALUE_RE.search(child):
                    issues.append(
                        WorkflowIssue(
                            "raw_secret",
                            filename,
                            child_path,
                            "secretRef must reference a secret name, not contain a raw secret-looking value",
                        )
                )
                continue
            if SECRET_KEY_RE.search(str(key)) and isinstance(child, str) and not _is_url_metadata(key, child):
                if _is_secret_ref_scalar(child):
                    continue
                issues.append(
                    WorkflowIssue(
                        "raw_secret",
                        filename,
                        child_path,
                        "replace inline secret value with a secret reference, checksum, or redacted marker",
                    )
                )
            issues.extend(_raw_secret_issues(filename, child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_raw_secret_issues(filename, child, (*path, index)))
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        issues.append(WorkflowIssue("raw_secret", filename, path, "inline secret-looking value is not allowed"))
    return issues


def _is_secret_ref_scalar(value: str) -> bool:
    prefix, separator, reference = value.partition(":")
    if separator != ":" or prefix.strip().lower() != "secretref":
        return False
    reference = reference.strip()
    return bool(reference) and SECRET_VALUE_RE.search(reference) is None


def _rollback_safety_issues(text: str) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    if not text.strip():
        return issues

    for line_number, line in enumerate(text.splitlines(), start=1):
        if ROLLBACK_COMMAND_RE.search(line):
            issues.append(
                WorkflowIssue(
                    "unsafe_rollback",
                    "rollback.md",
                    (line_number,),
                    "rollback.md must use manual, non-executable rollback notes; remove command examples",
                )
            )

    text_checks = (
        (ROLLBACK_DELETE_SCOPE_RE, "rollback.md must not recommend delete_repo or similarly broad destructive scopes"),
        (ROLLBACK_SECRET_PLACEHOLDER_RE, "rollback.md must not include secret placeholders or token examples"),
        (ROLLBACK_AUTH_HEADER_RE, "rollback.md must not include authorization header examples"),
        (ROLLBACK_HTTP_DELETE_RE, "rollback.md must not include destructive HTTP method examples"),
    )
    for pattern, message in text_checks:
        if pattern.search(text):
            issues.append(WorkflowIssue("unsafe_rollback", "rollback.md", (), message))

    return issues


def _is_url_metadata(key: object, value: str) -> bool:
    normalized_key = str(key).lower().replace("-", "_")
    return normalized_key in URL_METADATA_KEYS and value.startswith(("http://", "https://"))


def _is_secret_ref_key(key: object) -> bool:
    return bool(SECRET_REF_KEY_RE.match(str(key).replace("-", "_")))
