"""Semantic checks for appv24 workflow packages."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping

from appv24.workflows.catalog import BUILTIN_WORKFLOW_ACTIONS, is_builtin_workflow_action
from appv24.workflows.connector_contracts import (
    SideEffectClass,
    builtin_connector_contract_catalog,
    external_connector_action_refs,
    validate_external_connector_contracts,
)
from appv24.workflows.contracts import (
    BUILTIN_LLM_OUTPUT_CONTRACTS,
    builtin_action_output_fields,
    is_builtin_llm_output_contract,
    llm_output_contract_fields,
)
from appv24.workflows.catalog import built_in_capability_catalog
from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.extension_qa import validate_extension_qa_manifest
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.workflow_extensions import WorkflowActionExtensionSpec, build_workflow_extension_profile

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
SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9_])(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")
SECRET_REF_KEY_RE = re.compile(r"^secret[_-]?ref$", re.IGNORECASE)
INPUT_TEMPLATE_RE = re.compile(r"^\s*\{\{\s*(?P<path>inputs?\.[A-Za-z0-9_.-]+)\s*\}\}\s*$")
STEP_OUTPUT_REFERENCE_RE = re.compile(r"\{\{\s*steps\.([A-Za-z0-9_.-]+)\.outputs(?:\.|\s*\}\})")
GITHUB_STYLE_REFERENCE_RE = re.compile(r"\$\{\{")
ROLLBACK_COMMAND_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:[`$>#]\s*)?(?:curl|gh\s+api|aws|gcloud|az|kubectl|terraform|rm\s+-rf)\b",
)
ROLLBACK_DELETE_SCOPE_RE = re.compile(r"\bdelete_repo\b", re.IGNORECASE)
ROLLBACK_SECRET_PLACEHOLDER_RE = re.compile(r"\$\{\s*secrets\.[^}]+\}", re.IGNORECASE)
ROLLBACK_AUTH_HEADER_RE = re.compile(r"\bauthorization\s*:\s*bearer\b", re.IGNORECASE)
ROLLBACK_HTTP_DELETE_RE = re.compile(r"\b(?:-X\s+)?DELETE\b")
URL_METADATA_KEYS = {"token_url"}
UNSUPPORTED_DECISION_TRANSITION_KEYS = ("reject_next", "true_next", "false_next", "else_next")
SCALAR_ACTION_INPUT_FIELDS = {
    "brave.search": ("query",),
    "http.fetch": ("url",),
    "telegram.create_message_draft": ("chat_id", "text"),
    "telegram.send_message": ("chat_id", "text"),
    "telegram.send_message_draft": ("chat_id", "text"),
}
DESIGN_DECLARED_EXTERNAL_SYSTEMS = {
    "crm": ("crm",),
}


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
    external_action_refs = set(external_connector_action_refs(documents.connectors))
    steps_by_action = _steps_by_action_use(steps)
    extension_profile = build_workflow_extension_profile(documents)

    issues.extend(_unsupported_reference_syntax_issues("workflow.yaml", workflow))
    issues.extend(_unsupported_reference_syntax_issues("connectors.yaml", documents.connectors))
    issues.extend(_unsupported_reference_syntax_issues("permissions.yaml", documents.permissions))
    for fixture_name, fixture in documents.dry_run_fixtures.items():
        issues.extend(_unsupported_reference_syntax_issues(f"dry_run_fixtures/{fixture_name}", fixture))
    if documents.rollback_text:
        issues.extend(_unsupported_reference_syntax_issues("rollback.md", documents.rollback_text))
    issues.extend(_design_contract_issues(workflow))
    issues.extend(_duplicate_step_issues(step_ids))
    issues.extend(_entrypoint_issues(workflow, known_steps))
    issues.extend(_decision_contract_issues(steps, documents.dry_run_fixtures))
    issues.extend(_runtime_transition_issues(steps))
    issues.extend(_transition_issues(steps, known_steps))
    issues.extend(_step_output_reference_order_issues(steps))
    issues.extend(_approval_required_for_target_issues(documents.permissions, known_steps))
    issues.extend(_generated_connector_action_use_issues(extension_profile.action_specs, steps_by_action))
    issues.extend(_declared_external_system_modeling_issues(workflow, documents.connectors, steps))
    issues.extend(_connector_reference_issues(steps, connectors, permission_actions, permission_scopes, external_action_refs))
    issues.extend(_connector_required_input_issues(steps, extension_profile.action_specs))
    issues.extend(_connector_required_any_input_issues(steps, extension_profile.action_specs))
    issues.extend(_scalar_connector_input_issues(steps, documents.dry_run_fixtures, extension_profile.action_specs))
    issues.extend(_intelligence_node_contract_issues(steps, connectors))
    issues.extend(_builtin_action_output_contract_issues(action_entries))
    issues.extend(_catalog_issues(action_entries, external_action_refs))
    issues.extend(_connector_contract_issues(action_entries, external_action_refs))
    issues.extend(_extension_qa_issues(action_entries))
    issues.extend(validate_external_connector_contracts(documents.connectors))
    issues.extend(_side_effect_contract_issues(action_entries, steps_by_action, documents.permissions))
    issues.extend(_permission_scope_contract_issues(documents.permissions, connectors, external_action_refs))

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
        if "happy_path.yaml" not in documents.dry_run_fixtures:
            issues.append(
                WorkflowIssue(
                    "missing_fixture",
                    "dry_run_fixtures",
                    (),
                    "mutating workflows require dry_run_fixtures/happy_path.yaml",
                )
            )

    return list(sorted_issues(issues))


def _unsupported_reference_syntax_issues(
    filename: str,
    value: Any,
    path: tuple[str | int, ...] = (),
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            if filename == "workflow.yaml" and key == "steps" and isinstance(child, list):
                for index, step in enumerate(child):
                    step_path = (*path, "steps", _workflow_step_path_id(step, index))
                    issues.extend(_unsupported_reference_syntax_issues(filename, step, step_path))
                continue
            issues.extend(_unsupported_reference_syntax_issues(filename, child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_unsupported_reference_syntax_issues(filename, child, (*path, index)))
    elif isinstance(value, str) and GITHUB_STYLE_REFERENCE_RE.search(value):
        issues.append(
            WorkflowIssue(
                "unsupported_reference_syntax",
                filename,
                path,
                "Use appv24 double-brace references without a dollar sign; "
                'write "{{ steps.<step_id>.outputs.<field> }}", not "${{ ... }}".',
                expected='quoted appv24 reference like "{{ steps.search_ai_news.outputs.items }}"',
                repair_hint="Remove the leading dollar sign from workflow references and keep the value quoted.",
            )
        )
    return issues


def _workflow_step_path_id(step: object, index: int) -> str | int:
    if isinstance(step, Mapping):
        step_id = step.get("id")
        if isinstance(step_id, str) and step_id.strip():
            return step_id
    return index


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


def _intelligence_node_contract_issues(
    steps: Iterable[Mapping[str, Any]],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for step in steps:
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        connector_id, _, action_id = uses.partition(".")
        action = connectors.get(connector_id, {}).get(action_id, {})
        if connector_id == "llm":
            issues.extend(_llm_node_contract_issues(step, action))
        if uses == "policy.check":
            issues.extend(_policy_check_contract_issues(step))
    return issues


def _llm_node_contract_issues(step: Mapping[str, Any], action: Mapping[str, Any]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    step_id = str(step.get("id", "unknown"))
    step_with = step.get("with")
    with_map = step_with if isinstance(step_with, Mapping) else {}
    contract = with_map.get("output_contract")
    schema = with_map.get("output_schema")
    has_contract = isinstance(contract, str) and bool(contract.strip())
    has_schema = isinstance(schema, Mapping)

    if has_contract and has_schema:
        issues.append(
            WorkflowIssue(
                "ambiguous_llm_output_contract",
                "workflow.yaml",
                ("steps", step_id, "with"),
                f"{step_id} must set only one of output_contract or output_schema",
                expected="exactly one of output_contract or output_schema",
                repair_hint="Prefer compact output_contract for built-in shapes; use output_schema only for custom shapes.",
            )
        )
    elif not has_contract and not has_schema:
        issues.append(
            WorkflowIssue(
                "missing_llm_output_contract",
                "workflow.yaml",
                ("steps", step_id, "with"),
                f"{step_id} requires output_contract or output_schema",
                expected="output_contract: <known-contract> or output_schema: <json-schema>",
                repair_hint="Use compact output_contract such as event_alerts_v1 instead of embedding repeated schemas.",
            )
        )
    elif has_contract and not is_builtin_llm_output_contract(contract):
        issues.append(
            WorkflowIssue(
                "unknown_llm_output_contract",
                "workflow.yaml",
                ("steps", step_id, "with", "output_contract"),
                f"unknown LLM output contract: {contract}",
                expected=", ".join(sorted(BUILTIN_LLM_OUTPUT_CONTRACTS)),
                repair_hint="Use a built-in compact output_contract name or replace it with a single inline output_schema.",
            )
        )
    elif has_contract:
        contract_fields = set(llm_output_contract_fields(contract))
        allowed_outputs = set(_allowed_outputs(action))
        if contract_fields and not contract_fields.issubset(allowed_outputs):
            action_id = str(action.get("id", "unknown"))
            issues.append(
                WorkflowIssue(
                    "llm_output_contract_mismatch",
                    "connectors.yaml",
                    ("connectors", "llm", "actions", action_id, "outputs", "allowed"),
                    f"llm.{action_id} outputs.allowed must include {contract} fields: {', '.join(sorted(contract_fields))}",
                    expected=", ".join(sorted(contract_fields)),
                    repair_hint="Keep compact output_contract and update connectors.yaml outputs.allowed to the contract's canonical fields.",
                )
            )

    action_id = str(action.get("id", "unknown"))
    if action_id == "summarize" and not any(_has_connector_input_binding(with_map.get(field)) for field in ("text", "items", "input_ref")):
        issues.append(
            WorkflowIssue(
                "missing_llm_input",
                "workflow.yaml",
                ("steps", step_id, "with"),
                f"LLM step {step_id} using llm.summarize requires one of with.text, with.items, or with.input_ref",
                expected="with.text, with.items, or with.input_ref",
                repair_hint="Bind llm.summarize to upstream content, for example with.text: '{{ steps.search.outputs.items }}'.",
            )
        )

    step_side_effect_class = step.get("side_effect_class")
    action_side_effect_class = action.get("side_effect_class")
    if (
        step_side_effect_class in MUTATING_SIDE_EFFECTS
        or action.get("side_effect") is True
        or action_side_effect_class in MUTATING_SIDE_EFFECTS
    ):
        issues.append(
            WorkflowIssue(
                "invalid_llm_side_effect",
                "workflow.yaml",
                ("steps", step_id, "side_effect_class"),
                f"LLM step {step_id} must be read-only",
                expected="side_effect_class: read",
                repair_hint="Keep LLM nodes as typed transforms; move external actions into separate cataloged steps.",
            )
        )

    if isinstance(step.get("approval"), str) or action.get("approval_required") is True:
        issues.append(
            WorkflowIssue(
                "invalid_llm_approval",
                "workflow.yaml",
                ("steps", step_id, "approval"),
                f"LLM step {step_id} must not require approval",
                expected="no approval on llm.* steps",
                repair_hint="Put approvals on downstream mutating notification, ticket, or send steps.",
            )
        )

    if _idempotency_key_fields(action):
        issues.append(
            WorkflowIssue(
                "invalid_llm_idempotency",
                "connectors.yaml",
                (),
                f"LLM action llm.{action.get('id', 'unknown')} must not declare idempotency keys",
                expected="no idempotency on llm.* actions",
                repair_hint="Idempotency belongs on downstream mutating actions, not read-only LLM transforms.",
            )
        )
    return issues


def _policy_check_contract_issues(step: Mapping[str, Any]) -> list[WorkflowIssue]:
    step_id = str(step.get("id", "unknown"))
    step_with = step.get("with")
    with_map = step_with if isinstance(step_with, Mapping) else {}
    checks = with_map.get("checks")
    if isinstance(checks, Mapping) and checks:
        return []
    return [
        WorkflowIssue(
            "missing_policy_checks",
            "workflow.yaml",
            ("steps", step_id, "with", "checks"),
            f"policy.check step {step_id} requires with.checks",
            expected="non-empty checks mapping",
            repair_hint="Add deterministic with.checks instead of relying on LLM judgment for policy decisions.",
        )
    ]


def _builtin_action_output_contract_issues(actions: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for (connector_id, action_id), action in sorted(actions.items()):
        action_ref = f"{connector_id}.{action_id}"
        supported_outputs = set(builtin_action_output_fields(action_ref))
        allowed_outputs = set(_allowed_outputs(action))
        if not supported_outputs or not allowed_outputs or supported_outputs.intersection(allowed_outputs):
            continue
        expected = ", ".join(sorted(supported_outputs))
        issues.append(
            WorkflowIssue(
                "builtin_output_contract_mismatch",
                "connectors.yaml",
                ("connectors", connector_id, "actions", action_id, "outputs", "allowed"),
                f"{action_ref} outputs.allowed must include at least one supported top-level field: {expected}",
                expected=expected,
                repair_hint=(
                    "Use top-level provider output fields in connectors.yaml outputs.allowed; "
                    "nested item fields belong inside fixture data or downstream input refs."
                ),
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


def _connector_required_input_issues(
    steps: Iterable[Mapping[str, Any]],
    extension_specs: Iterable[WorkflowActionExtensionSpec] = (),
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    required_fields = {
        action_ref: contract.required_input_fields
        for action_ref, contract in builtin_connector_contract_catalog().items()
        if contract.required_input_fields
    }
    for spec in extension_specs:
        if spec.required_input_fields:
            required_fields[spec.action_ref] = spec.required_input_fields
    for index, step in enumerate(steps):
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        fields = required_fields.get(uses)
        if not fields:
            continue
        step_id = str(step.get("id", f"steps[{index}]"))
        step_with = step.get("with")
        with_map = step_with if isinstance(step_with, Mapping) else {}
        for field in fields:
            if _has_connector_input_binding(with_map.get(field)):
                continue
            issues.append(
                WorkflowIssue(
                    "missing_connector_required_input",
                    "workflow.yaml",
                    ("steps", step_id, "with", field),
                    f"{uses} requires workflow.yaml step {step_id} to bind {field}",
                    expected=f"workflow.yaml steps[].with.{field}",
                    repair_hint=(
                        f"Add workflow.yaml step {step_id} with.{field}. Required connector inputs "
                        "must be bound in workflow.yaml, not only in dry_run_fixtures."
                    ),
                )
            )
    return issues


def _has_connector_input_binding(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        return len(value) == 1 and isinstance(input_ref, str) and bool(input_ref.strip())
    if isinstance(value, (list, tuple)):
        return bool(value)
    return True


def _connector_required_any_input_issues(
    steps: Iterable[Mapping[str, Any]],
    extension_specs: Iterable[WorkflowActionExtensionSpec] = (),
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    groups_by_action = {
        spec.action_ref: spec.required_input_any_groups
        for spec in extension_specs
        if spec.required_input_any_groups
    }
    for index, step in enumerate(steps):
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        groups = groups_by_action.get(uses, ())
        if not groups:
            continue
        step_id = str(step.get("id", f"steps[{index}]"))
        step_with = step.get("with")
        with_map = step_with if isinstance(step_with, Mapping) else {}
        for group in groups:
            if any(_has_connector_input_binding(with_map.get(field)) for field in group):
                continue
            field_list = ", ".join(group)
            issues.append(
                WorkflowIssue(
                    "missing_connector_required_input_any",
                    "workflow.yaml",
                    ("steps", step_id, "with"),
                    f"{uses} requires workflow.yaml step {step_id} to bind one of {field_list}",
                    expected=f"one of {field_list}",
                    repair_hint=(
                        f"Add one of workflow.yaml step {step_id} fields: {field_list}. "
                        "Required connector input alternatives must be bound in workflow.yaml."
                    ),
                )
            )
    return issues


def _scalar_connector_input_issues(
    steps: Iterable[Mapping[str, Any]],
    dry_run_fixtures: Mapping[str, Mapping[str, Any]],
    extension_specs: Iterable[WorkflowActionExtensionSpec] = (),
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    reported: set[tuple[str, str]] = set()
    scalar_fields_by_action = dict(SCALAR_ACTION_INPUT_FIELDS)
    for spec in extension_specs:
        if spec.scalar_input_fields:
            scalar_fields_by_action[spec.action_ref] = spec.scalar_input_fields
    for index, step in enumerate(steps):
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        scalar_fields = scalar_fields_by_action.get(uses, ())
        if not scalar_fields:
            continue
        step_id = str(step.get("id", f"steps[{index}]"))
        step_with = step.get("with")
        with_map = step_with if isinstance(step_with, Mapping) else {}
        for field in scalar_fields:
            value = with_map.get(field)
            issue_key = (step_id, field)
            if _is_collection(value):
                issues.append(
                    _scalar_input_issue(
                        uses,
                        step_id,
                        field,
                        "literal collection",
                    )
                )
                reported.add(issue_key)
                continue
            input_path = _input_template_path(value)
            if input_path is None:
                continue
            for fixture_name, fixture in sorted(dry_run_fixtures.items()):
                resolved = _resolve_fixture_input_path(fixture, input_path)
                if _is_collection(resolved):
                    issues.append(
                        _scalar_input_issue(
                            uses,
                            step_id,
                            field,
                            f"dry_run_fixtures/{fixture_name}:{'.'.join(input_path)}",
                        )
                    )
                    reported.add(issue_key)
                    break
            if issue_key in reported:
                continue
    return issues


def _scalar_input_issue(action_ref: str, step_id: str, field: str, source: str) -> WorkflowIssue:
    return WorkflowIssue(
        "connector_scalar_input_collection",
        "workflow.yaml",
        ("steps", step_id, "with", field),
        f"{action_ref} {field} must resolve to a scalar value; {source} is a collection",
        expected="single string, number, or boolean value",
        repair_hint=(
            "Use one explicit scalar target for this action, or keep collection fanout as "
            "fixture/future-connector design until a cataloged broadcast action exists."
        ),
    )


def _input_template_path(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        if len(value) == 1 and isinstance(input_ref, str) and input_ref.strip():
            return ("input", *tuple(part for part in input_ref.split(".") if part))
        return None
    if not isinstance(value, str):
        return None
    match = INPUT_TEMPLATE_RE.match(value)
    if match is None:
        return None
    return tuple(part for part in match.group("path").split(".") if part)


def _resolve_fixture_input_path(fixture: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    if not path:
        return None
    variants = [path]
    if path[0] == "inputs":
        variants.append(("input", *path[1:]))
    elif path[0] == "input":
        variants.append(("inputs", *path[1:]))
    for candidate in variants:
        current: Any = fixture
        for part in candidate:
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current is not None:
            return current
    return None


def _is_collection(value: Any) -> bool:
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        if len(value) == 1 and isinstance(input_ref, str) and input_ref.strip():
            return False
    return isinstance(value, (Mapping, list, tuple))


def _runtime_transition_issues(steps: Iterable[Mapping[str, Any]]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for index, step in enumerate(steps):
        step_id = str(step.get("id", f"steps[{index}]"))
        step_type = step.get("type")
        if step_type in {"terminal", "decision"}:
            continue
        next_step = step.get("next")
        if not isinstance(next_step, str) or not next_step.strip():
            issues.append(
                WorkflowIssue(
                    "missing_step_transition",
                    "workflow.yaml",
                    ("steps", step_id, "next"),
                    f"non-terminal step {step_id} requires a runtime transition",
                    expected="next: <target-step>",
                    repair_hint=(
                        "Add next: <target-step> to action/manual steps, or set type: terminal "
                        "if the step intentionally ends the workflow."
                    ),
                )
            )
    return issues


def _step_output_reference_order_issues(steps: Iterable[Mapping[str, Any]]) -> list[WorkflowIssue]:
    step_list = list(steps)
    known_steps = {str(step.get("id")) for step in step_list if isinstance(step.get("id"), str)}
    outbound = _step_outbound_targets(step_list)
    issues: list[WorkflowIssue] = []
    for index, step in enumerate(step_list):
        step_id = str(step.get("id", f"steps[{index}]"))
        for referenced_step, path in _step_output_references(step.get("with"), ("steps", step_id, "with")):
            if referenced_step not in known_steps:
                issues.append(
                    WorkflowIssue(
                        "unknown_step_output_reference",
                        "workflow.yaml",
                        path,
                        f"{step_id} references outputs from unknown step {referenced_step}",
                        expected="existing upstream workflow step id",
                        repair_hint="Reference outputs only from existing upstream workflow steps.",
                    )
                )
                continue
            if referenced_step == step_id or _can_reach_step(outbound, referenced_step, step_id):
                continue
            issues.append(
                WorkflowIssue(
                    "future_step_output_reference",
                    "workflow.yaml",
                    path,
                    f"{step_id} references outputs from {referenced_step}, but {referenced_step} does not run before {step_id}",
                    expected="reference only upstream step outputs",
                    repair_hint=(
                        f"Move {step_id} after {referenced_step} in the workflow graph, "
                        f"or change the reference to an output from a step that runs before {step_id}."
                    ),
                )
            )
    return issues


def _step_output_references(value: object, path: tuple[str | int, ...]) -> list[tuple[str, tuple[str | int, ...]]]:
    references: list[tuple[str, tuple[str | int, ...]]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            references.extend(_step_output_references(child, (*path, str(key))))
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            references.extend(_step_output_references(child, (*path, index)))
    elif isinstance(value, str):
        references.extend((match.group(1), path) for match in STEP_OUTPUT_REFERENCE_RE.finditer(value))
    return references


def _step_outbound_targets(steps: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
    outbound: dict[str, set[str]] = {}
    for index, step in enumerate(steps):
        step_id = str(step.get("id", f"steps[{index}]"))
        targets: set[str] = set()
        next_step = step.get("next")
        if isinstance(next_step, str) and next_step.strip():
            targets.add(next_step)
        branches = step.get("branches")
        if isinstance(branches, Mapping):
            targets.update(str(target) for target in branches.values() if isinstance(target, str) and target.strip())
        outbound[step_id] = targets
    return outbound


def _can_reach_step(outbound: Mapping[str, set[str]], source: str, target: str) -> bool:
    seen: set[str] = set()
    stack = [source]
    while stack:
        step_id = stack.pop()
        if step_id == target:
            return True
        if step_id in seen:
            continue
        seen.add(step_id)
        stack.extend(next_step for next_step in outbound.get(step_id, set()) if next_step not in seen)
    return False


def _decision_contract_issues(
    steps: Iterable[Mapping[str, Any]],
    dry_run_fixtures: Mapping[str, Mapping[str, Any]],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    branching_decisions: dict[str, set[str]] = {}
    for step in steps:
        if step.get("type") != "decision":
            continue
        step_id = str(step.get("id", "unknown"))
        branches = _decision_branches(step)
        if not branches:
            issues.append(
                WorkflowIssue(
                    "missing_decision_branches",
                    "workflow.yaml",
                    ("steps", step_id, "branches"),
                    f"decision step {step_id} requires branches for runtime simulation",
                    expected="branches mapping such as branches: {approved: next_step, rejected: rejected_step}",
                    repair_hint="Replace condition/next/reject_next decision syntax with workflow.yaml steps[].branches.",
                )
            )
        if len(branches) > 1:
            branching_decisions[step_id] = set(branches)
        for key in UNSUPPORTED_DECISION_TRANSITION_KEYS:
            if key in step:
                issues.append(
                    WorkflowIssue(
                        "unsupported_decision_syntax",
                        "workflow.yaml",
                        ("steps", step_id, key),
                        f"{key} is not a runtime transition; decision step {step_id} must use branches",
                        expected="branches mapping",
                        repair_hint="Move every decision outcome into workflow.yaml steps[].branches and fixture decision maps.",
                    )
                )

    if not branching_decisions:
        return issues

    for fixture_name, fixture in sorted(dry_run_fixtures.items()):
        decisions = fixture.get("decision") if isinstance(fixture, Mapping) else None
        decision_map = decisions if isinstance(decisions, Mapping) else {}
        for step_id, branch_names in sorted(branching_decisions.items()):
            selected = decision_map.get(step_id)
            if selected is None:
                issues.append(
                    WorkflowIssue(
                        "missing_fixture_decision",
                        f"dry_run_fixtures/{fixture_name}",
                        ("decision", step_id),
                        f"fixture must select a branch for decision step {step_id}",
                        expected=f"one of: {', '.join(sorted(branch_names))}",
                        repair_hint=f"Add decision: {{{step_id}: <branch-name>}} to dry_run_fixtures/{fixture_name}.",
                    )
                )
            elif not isinstance(selected, str) or selected not in branch_names:
                rendered = str(selected)
                issues.append(
                    WorkflowIssue(
                        "unknown_fixture_decision_branch",
                        f"dry_run_fixtures/{fixture_name}",
                        ("decision", step_id),
                        f"{rendered} is not a branch for decision step {step_id}",
                        expected=f"one of: {', '.join(sorted(branch_names))}",
                        repair_hint=f"Set decision.{step_id} to one of the workflow.yaml branches.",
                    )
                )
    return issues


def _decision_branches(step: Mapping[str, Any]) -> dict[str, str]:
    branches = step.get("branches")
    if not isinstance(branches, Mapping):
        return {}
    return {
        branch: target
        for branch, target in branches.items()
        if isinstance(branch, str) and branch and isinstance(target, str) and target
    }


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


def _catalog_issues(
    actions: Mapping[tuple[str, str], Mapping[str, Any]],
    external_action_refs: set[str],
) -> list[WorkflowIssue]:
    catalog = built_in_capability_catalog()
    issues: list[WorkflowIssue] = []
    for (connector_id, action_id), action in sorted(actions.items()):
        action_ref = f"{connector_id}.{action_id}"
        if action.get("custom") is True and action.get("execution_mode") == "design_only":
            continue
        if action_ref in external_action_refs:
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
                            "Use a built-in catalog action, declare a strict external connector "
                            "interface with custom: true and a versioned source, or mark custom "
                            "design-only actions with custom: true and execution_mode: design_only."
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


def _extension_qa_issues(actions: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for (connector_id, action_id), action in sorted(actions.items()):
        action_ref = f"{connector_id}.{action_id}"
        if not _requires_extension_qa(action):
            continue
        path = ("connectors", connector_id, "actions", action_id, "qa")
        qa = action.get("qa")
        if not isinstance(qa, Mapping):
            issues.append(
                WorkflowIssue(
                    "extension_qa_missing",
                    "connectors.yaml",
                    path,
                    f"{action_ref} requires passed extension QA evidence",
                    expected="qa manifest with status: passed",
                    repair_hint="Generate the connector with the coding profile, run QA, and attach its appv24.extension_qa/v1 manifest.",
                )
            )
            continue
        report = validate_extension_qa_manifest(
            qa,
            expected_kind="connector",
            expected_extension_id=action_ref,
            expected_required_scopes=tuple(
                scope for scope in action.get("required_scopes", []) if isinstance(scope, str) and scope
            ),
            expected_allowed_outputs=tuple(_allowed_outputs(action)),
        )
        for issue in report.issues:
            issues.append(
                WorkflowIssue(
                    issue.code,
                    "connectors.yaml",
                    (*path, *issue.path),
                    f"{action_ref} extension QA failed: {issue.message}",
                    expected=issue.expected,
                    repair_hint=issue.repair_hint,
                )
            )
    return issues


def _requires_extension_qa(action: Mapping[str, Any]) -> bool:
    hints = action.get("workflow_hints")
    if not isinstance(hints, Mapping):
        return False
    return hints.get("qa_required") is True or hints.get("generated_by") == "coding_profile"


def _connector_contract_issues(
    actions: Mapping[tuple[str, str], Mapping[str, Any]],
    external_action_refs: set[str],
) -> list[WorkflowIssue]:
    contracts = builtin_connector_contract_catalog()
    issues: list[WorkflowIssue] = []
    for (connector_id, action_id), action in sorted(actions.items()):
        action_ref = f"{connector_id}.{action_id}"
        if action.get("custom") is True and action.get("execution_mode") == "design_only":
            continue
        if action_ref in external_action_refs:
            continue
        contract = contracts.get(action_ref)
        if contract is None:
            issues.append(
                WorkflowIssue(
                    "unknown_connector_contract",
                    "connectors.yaml",
                    ("connectors", connector_id, "actions", action_id),
                    f"{action_ref} is not in the built-in connector contract catalog",
                    expected=", ".join(sorted(contracts)),
                    repair_hint=(
                        "Use a built-in action contract, declare a strict external connector interface "
                        "with custom: true and a versioned source, or make the action custom: true "
                        "with execution_mode: design_only."
                    ),
                )
            )
            continue

        declared_side_effect = action.get("side_effect")
        if declared_side_effect != contract.side_effect:
            issues.append(
                WorkflowIssue(
                    "connector_contract_mismatch",
                    "connectors.yaml",
                    ("connectors", connector_id, "actions", action_id, "side_effect"),
                    f"{action_ref} side_effect must match connector contract value {contract.side_effect!r}",
                    expected=str(contract.side_effect).lower(),
                )
            )

        declared_class = action.get("side_effect_class")
        expected_class = None if contract.side_effect_class is SideEffectClass.READ else contract.side_effect_class.value
        if expected_class is not None and declared_class != expected_class:
            issues.append(
                WorkflowIssue(
                    "connector_contract_mismatch",
                    "connectors.yaml",
                    ("connectors", connector_id, "actions", action_id, "side_effect_class"),
                    f"{action_ref} side_effect_class must match connector contract value {expected_class}",
                    expected=expected_class,
                )
            )
        elif expected_class is None and declared_class not in {None, "read"}:
            issues.append(
                WorkflowIssue(
                    "connector_contract_mismatch",
                    "connectors.yaml",
                    ("connectors", connector_id, "actions", action_id, "side_effect_class"),
                    f"{action_ref} side_effect_class must be read-only",
                    expected="read or omitted",
                )
            )

        declared_scopes = set(str(scope) for scope in action.get("required_scopes", []) if isinstance(scope, str))
        for scope in contract.required_scopes:
            if scope not in declared_scopes:
                issues.append(
                    WorkflowIssue(
                        "connector_contract_scope_mismatch",
                        "connectors.yaml",
                        ("connectors", connector_id, "actions", action_id, "required_scopes"),
                        f"{action_ref} required_scopes must include {scope}",
                        expected=scope,
                    )
                )

        allowed_outputs = set(_allowed_outputs(action))
        unsupported_outputs = sorted(output for output in allowed_outputs if output not in contract.allowed_outputs)
        if unsupported_outputs:
            issues.append(
                WorkflowIssue(
                    "connector_contract_output_mismatch",
                    "connectors.yaml",
                    ("connectors", connector_id, "actions", action_id, "outputs", "allowed"),
                    f"{action_ref} outputs.allowed contains unsupported fields: {', '.join(unsupported_outputs)}",
                    expected=", ".join(contract.allowed_outputs),
                )
            )

        declared_idempotency = tuple(_idempotency_key_fields(action))
        if (
            contract.input_schema.get("additionalProperties") is False
            and (declared_idempotency or contract.idempotency_key_fields)
            and declared_idempotency != contract.idempotency_key_fields
        ):
            expected = ", ".join(contract.idempotency_key_fields) if contract.idempotency_key_fields else "no idempotency.key_fields"
            issues.append(
                WorkflowIssue(
                    "connector_contract_idempotency_mismatch",
                    "connectors.yaml",
                    ("connectors", connector_id, "actions", action_id, "idempotency", "key_fields"),
                    f"{action_ref} idempotency.key_fields must match connector contract: {expected}",
                    expected=expected,
                    repair_hint="Use the built-in connector contract idempotency fields; do not invent workflow-local checksum or alias fields.",
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


def _generated_connector_action_use_issues(
    extension_specs: Iterable[WorkflowActionExtensionSpec],
    steps_by_action: Mapping[tuple[str, str], list[Mapping[str, Any]]],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for spec in extension_specs:
        if not spec.qa_required:
            continue
        connector_id, _, action_id = spec.action_ref.partition(".")
        if steps_by_action.get((connector_id, action_id)):
            continue
        issues.append(
            WorkflowIssue(
                "orphan_generated_connector_action",
                "connectors.yaml",
                ("connectors", connector_id, "actions", action_id),
                f"{spec.action_ref} is generated by the coding profile but no workflow step calls it",
                expected=f"workflow.yaml steps[].uses: {spec.action_ref}",
                repair_hint=(
                    "Wire the generated connector action into workflow.yaml at the intended point, "
                    "bind its required inputs, and update transitions, permissions, approvals, and fixtures. "
                    "Do not remove a coding-profile generated connector to make validation pass."
                ),
            )
        )
    return issues


def _declared_external_system_modeling_issues(
    workflow: Mapping[str, Any],
    connectors_document: Mapping[str, Any],
    steps: Iterable[Mapping[str, Any]],
) -> list[WorkflowIssue]:
    design_text = _design_text(workflow.get("design"))
    if not design_text:
        return []
    used_connectors = _used_connector_ids(steps)
    declared_connectors = _declared_connector_ids(connectors_document)
    issues: list[WorkflowIssue] = []
    for system_id, labels in sorted(DESIGN_DECLARED_EXTERNAL_SYSTEMS.items()):
        if not any(_mentions_design_label(design_text, label) for label in labels):
            continue
        if system_id in used_connectors:
            continue
        connector_detail = "no connector is declared" if system_id not in declared_connectors else "no workflow step uses it"
        issues.append(
            WorkflowIssue(
                "declared_external_system_unmodeled",
                "workflow.yaml",
                ("design",),
                f"workflow design mentions {system_id.upper()} but no {system_id} connector step models it",
                expected=f"reachable workflow.yaml step using {system_id}.<action>",
                repair_hint=(
                    f"Add a reachable {system_id} connector action step for the design-declared system; {connector_detail}. "
                    "Do not satisfy the workflow by leaving external systems only in design prose."
                ),
            )
        )
    return issues


def _design_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_design_text(child) for child in value.values())
    if isinstance(value, list | tuple):
        return " ".join(_design_text(child) for child in value)
    return ""


def _mentions_design_label(text: str, label: str) -> bool:
    return re.search(rf"\b{re.escape(label)}\b", text, flags=re.IGNORECASE) is not None


def _used_connector_ids(steps: Iterable[Mapping[str, Any]]) -> set[str]:
    connector_ids: set[str] = set()
    for step in steps:
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        connector_id, _, _ = uses.partition(".")
        if connector_id:
            connector_ids.add(connector_id.lower())
    return connector_ids


def _declared_connector_ids(connectors_document: Mapping[str, Any]) -> set[str]:
    connector_ids: set[str] = set()
    for connector in connectors_document.get("connectors", []):
        if isinstance(connector, Mapping) and isinstance(connector.get("id"), str):
            connector_ids.add(str(connector["id"]).lower())
    return connector_ids


def _connector_reference_issues(
    steps: Iterable[Mapping[str, Any]],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
    permission_actions: Mapping[str, set[str]],
    permission_scopes: Mapping[str, set[str]],
    external_action_refs: set[str],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    wildcard_scope_reported: set[str] = set()
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
        if not is_builtin_workflow_action(uses) and uses not in external_action_refs:
            issues.append(
                WorkflowIssue(
                    "unknown_catalog_action",
                    "workflow.yaml",
                    ("steps", index, "uses"),
                    f"{uses} is not in the built-in workflow capability catalog",
                    expected=", ".join(sorted(BUILTIN_WORKFLOW_ACTIONS)),
                    repair_hint=(
                        "Use a built-in workflow capability catalog action, declare a strict external "
                        "connector interface in connectors.yaml, or model non-executed inputs as "
                        "triggers, fixture data, decisions, or local artifacts instead of connector actions."
                    ),
                )
            )
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
        if "*" in granted_scopes and connector_id not in wildcard_scope_reported:
            issues.append(
                WorkflowIssue(
                    "permission_wildcard_scope",
                    "permissions.yaml",
                    ("permissions",),
                    f"permissions for {connector_id} must not use wildcard scope; grant explicit required scopes",
                )
            )
            wildcard_scope_reported.add(connector_id)
        action = connectors[connector_id][action_id]
        contract = builtin_connector_contract_catalog().get(uses)
        required_scopes = {
            str(scope)
            for scope in action.get("required_scopes", [])
            if isinstance(scope, str)
        }
        if contract is not None:
            required_scopes.update(contract.required_scopes)
        for scope in sorted(required_scopes):
            if scope not in granted_scopes:
                issues.append(
                    WorkflowIssue(
                        "permission_missing_scope",
                        "permissions.yaml",
                        ("permissions",),
                        f"permissions for {connector_id} do not grant {scope} required by {action_id}",
                    )
                )
    return issues


def _permission_scope_contract_issues(
    permissions_document: Mapping[str, Any],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
    external_action_refs: set[str],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    builtin_contracts = builtin_connector_contract_catalog()
    for permission in permissions_document.get("permissions", []):
        if not isinstance(permission, Mapping) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        raw_actions = permission.get("actions", [])
        action_ids = tuple(action for action in raw_actions if isinstance(action, str))
        granted_scopes = tuple(scope for scope in permission.get("scopes", []) if isinstance(scope, str))
        if not action_ids or not granted_scopes:
            continue
        allowed_scopes = _allowed_permission_scopes_for_actions(connector_id, action_ids, connectors, builtin_contracts, external_action_refs)
        for scope in sorted(set(granted_scopes)):
            if scope == "*":
                continue
            if scope not in allowed_scopes:
                expected = ", ".join(sorted(allowed_scopes)) if allowed_scopes else "no scopes"
                issues.append(
                    WorkflowIssue(
                        "permission_unsupported_scope",
                        "permissions.yaml",
                        ("permissions",),
                        f"permissions for {connector_id} grant unsupported scope {scope}",
                        expected=expected,
                        repair_hint=(
                            "Use only connector-owned required_scopes for the actions listed in this permission entry; "
                            "remove invented or provider-generic scope aliases."
                        ),
                    )
                )
    return issues


def _allowed_permission_scopes_for_actions(
    connector_id: str,
    action_ids: tuple[str, ...],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
    builtin_contracts: Mapping[str, Any],
    external_action_refs: set[str],
) -> set[str]:
    connector_actions = connectors.get(connector_id, {})
    if "*" in action_ids:
        action_ids = tuple(connector_actions)
    allowed: set[str] = set()
    for action_id in action_ids:
        action_ref = f"{connector_id}.{action_id}"
        contract = builtin_contracts.get(action_ref)
        if contract is not None:
            allowed.update(contract.required_scopes)
            continue
        action = connector_actions.get(action_id)
        if action is None or action_ref not in external_action_refs:
            continue
        allowed.update(str(scope) for scope in action.get("required_scopes", []) if isinstance(scope, str))
    return allowed


def _side_effect_contract_issues(
    actions: Mapping[tuple[str, str], Mapping[str, Any]],
    steps_by_action: Mapping[tuple[str, str], list[Mapping[str, Any]]],
    permissions_document: Mapping[str, Any],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    design_only_actions: set[tuple[str, str]] = set()

    for action_key, action in sorted(actions.items()):
        connector_id, action_id = action_key
        action_ref = f"{connector_id}.{action_id}"
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

        if action.get("side_effect") is not True:
            continue

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

        if side_effect_class is not None and side_effect_class not in MUTATING_SIDE_EFFECTS:
            issues.append(
                WorkflowIssue(
                    "invalid_side_effect_contract",
                    "connectors.yaml",
                    (),
                    f"{action_ref} mutating actions require side_effect_class create, update, delete, send, publish, or provision",
                )
            )
        if execution_mode == "design_only":
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


def _approval_required_for_target_issues(
    permissions_document: Mapping[str, Any],
    known_steps: set[str],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for approval_index, approval in enumerate(permissions_document.get("approvals", [])):
        if not isinstance(approval, Mapping) or not isinstance(approval.get("id"), str):
            continue
        approval_id = str(approval["id"])
        required_for = approval.get("required_for", [])
        if not isinstance(required_for, list):
            continue
        for target_index, step_id in enumerate(required_for):
            if not isinstance(step_id, str) or step_id in known_steps:
                continue
            issues.append(
                WorkflowIssue(
                    "unknown_approval_target",
                    "permissions.yaml",
                    ("approvals", approval_index, "required_for", target_index),
                    f"approval {approval_id} targets unknown workflow step {step_id}",
                    expected="existing workflow.yaml step id",
                    repair_hint=(
                        "Update approvals[].required_for to existing workflow step ids, "
                        "or remove the approval gate if no workflow step uses it."
                    ),
                )
            )
    return issues


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
