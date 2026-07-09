"""Declarative workflow extension metadata.

This module keeps extension-facing catalog and validation hints out of workflow
runtime semantics. It parses package-owned connector and middleware metadata
into immutable specs that catalogs and quality gates can consume without
importing generated connector code or calling external systems.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from appv24.workflows.connector_contracts import (
    ApprovalClass,
    ConnectorContract,
    ConnectorLiveMode,
    ConnectorSecretRef,
    ConnectorTargetPolicy,
    RetryClass,
    SideEffectClass,
    builtin_connector_contract_catalog,
)
from appv24.workflows.extension_qa import validate_extension_qa_manifest
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class WorkflowTransitConstraint:
    source_action: str
    target_action: str
    allowed_intermediates: tuple[str, ...]
    issue_code: str = "workflow_transit_constraint_violation"
    message_prefix: str = "workflow path"
    repair_hint: str | None = None


@dataclass(frozen=True)
class WorkflowActionExtensionSpec:
    action_ref: str
    catalog_group: str
    catalog_label: str
    input_fields: tuple[str, ...]
    required_input_fields: tuple[str, ...]
    required_input_any_groups: tuple[tuple[str, ...], ...]
    required_target_fields: tuple[str, ...]
    scalar_input_fields: tuple[str, ...]
    output_fields: tuple[str, ...]
    mode_labels: tuple[str, ...]
    side_effect_class: str
    approval_required: bool
    required_env_names: tuple[str, ...]
    required_scopes: tuple[str, ...]
    adapter: str = "direct_api"
    execution_mode: str = "brokered"
    dry_run_supported: bool = True
    idempotency_key_fields: tuple[str, ...] = ()
    transit_constraints: tuple[WorkflowTransitConstraint, ...] = ()
    custom: bool = True
    qa_required: bool = False
    qa_passed: bool = False


@dataclass(frozen=True)
class WorkflowMiddlewareExtensionSpec:
    middleware_id: str
    catalog_label: str
    adapter: str
    source_ref: str
    required_env_names: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowExtensionProfile:
    action_specs: tuple[WorkflowActionExtensionSpec, ...] = ()
    middleware_specs: tuple[WorkflowMiddlewareExtensionSpec, ...] = ()
    transit_constraints: tuple[WorkflowTransitConstraint, ...] = ()


def build_workflow_extension_profile(
    documents: PackageDocuments | None = None,
    *,
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> WorkflowExtensionProfile:
    connector_specs = extension_specs_from_connectors(
        documents.connectors if documents is not None else {},
        contract_catalog=contract_catalog,
    )
    middleware_specs = extension_specs_from_middlewares(documents.middlewares if documents is not None else {})
    transit_constraints = (
        *_builtin_transit_constraints(),
        *(constraint for spec in connector_specs for constraint in spec.transit_constraints),
    )
    return WorkflowExtensionProfile(
        action_specs=connector_specs,
        middleware_specs=middleware_specs,
        transit_constraints=transit_constraints,
    )


def extension_specs_from_connectors(
    connectors_document: Mapping[str, Any],
    *,
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> tuple[WorkflowActionExtensionSpec, ...]:
    builtins = builtin_connector_contract_catalog() if contract_catalog is None else contract_catalog
    specs: list[WorkflowActionExtensionSpec] = []
    for connector in _connector_entries(connectors_document):
        connector_id = _string(connector.get("id"))
        if connector_id is None:
            continue
        connector_hints = _mapping(connector.get("workflow_hints"))
        for action in _action_entries(connector):
            action_id = _string(action.get("id"))
            if action_id is None:
                continue
            action_ref = f"{connector_id}.{action_id}"
            if action_ref in builtins or _is_design_only_action(action):
                continue
            if not _is_extension_action(connector, action):
                continue
            hints = {**connector_hints, **_mapping(action.get("workflow_hints"))}
            specs.append(_action_spec(connector, action, action_ref, hints))
    return tuple(specs)


def extension_specs_from_middlewares(
    middlewares_document: Mapping[str, Any],
) -> tuple[WorkflowMiddlewareExtensionSpec, ...]:
    specs: list[WorkflowMiddlewareExtensionSpec] = []
    raw_entries = middlewares_document.get("middlewares") if isinstance(middlewares_document, Mapping) else None
    if not isinstance(raw_entries, list):
        return ()
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            continue
        middleware_id = _string(entry.get("id"))
        adapter = _string(entry.get("adapter"))
        source = _string(entry.get("source"))
        if middleware_id is None or adapter is None or source is None:
            continue
        hints = _mapping(entry.get("workflow_hints"))
        specs.append(
            WorkflowMiddlewareExtensionSpec(
                middleware_id=middleware_id,
                catalog_label=_string(hints.get("catalog_label")) or middleware_id,
                adapter=adapter,
                source_ref=source,
                required_env_names=_required_env_names(entry),
            )
        )
    return tuple(specs)


def extension_connector_contract_catalog(
    documents: PackageDocuments,
    *,
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> dict[str, ConnectorContract]:
    profile = build_workflow_extension_profile(documents, contract_catalog=contract_catalog)
    contracts: dict[str, ConnectorContract] = {}
    for spec in profile.action_specs:
        if spec.qa_required and not spec.qa_passed:
            continue
        contract = _contract_from_action_spec(spec)
        if contract is not None:
            contracts[spec.action_ref] = contract
    return contracts


def _builtin_transit_constraints() -> tuple[WorkflowTransitConstraint, ...]:
    return (
        WorkflowTransitConstraint(
            source_action="telegram.list_subscribers",
            target_action="telegram.broadcast_message",
            allowed_intermediates=("llm.classify", "llm.extract_structured", "llm.summarize"),
            issue_code="registered_user_broadcast_detour",
            message_prefix="registered-user telegram delivery",
            repair_hint=(
                "Use brave.search -> telegram.list_subscribers -> llm.summarize -> "
                "telegram.broadcast_message -> done; only pure LLM message transforms may appear "
                "between telegram.list_subscribers and telegram.broadcast_message, and approval belongs "
                "on telegram.broadcast_message."
            ),
        ),
    )


def _action_spec(
    connector: Mapping[str, Any],
    action: Mapping[str, Any],
    action_ref: str,
    hints: Mapping[str, Any],
) -> WorkflowActionExtensionSpec:
    side_effect = action.get("side_effect")
    side_effect_class = _string(action.get("side_effect_class"))
    if side_effect_class is None:
        side_effect_class = "read" if side_effect is False else "create"
    return WorkflowActionExtensionSpec(
        action_ref=action_ref,
        catalog_group=_string(hints.get("catalog_group")) or "Other",
        catalog_label=_string(hints.get("catalog_label")) or action_ref,
        input_fields=_string_tuple(hints.get("input_fields")),
        required_input_fields=_string_tuple(hints.get("required_input_fields")),
        required_input_any_groups=_string_tuple_groups(hints.get("required_input_any")),
        required_target_fields=_string_tuple(hints.get("required_target_fields")),
        scalar_input_fields=_string_tuple(hints.get("scalar_input_fields")),
        output_fields=_allowed_outputs(action),
        mode_labels=_mode_labels(action),
        side_effect_class=side_effect_class,
        approval_required=action.get("approval_required") is True,
        required_env_names=_required_env_names(connector, action),
        required_scopes=_string_tuple(action.get("required_scopes")),
        adapter=_string(connector.get("adapter")) or "direct_api",
        execution_mode=_string(action.get("execution_mode")) or "brokered",
        dry_run_supported=action.get("dry_run_supported") is not False,
        idempotency_key_fields=_idempotency_key_fields(action),
        transit_constraints=_transit_constraints(hints),
        custom=True,
        qa_required=_requires_extension_qa(hints),
        qa_passed=_extension_qa_passed(action, action_ref),
    )


def _contract_from_action_spec(spec: WorkflowActionExtensionSpec) -> ConnectorContract | None:
    live_modes = _connector_live_modes(spec.mode_labels)
    if not live_modes:
        return None
    side_effect_class = _side_effect_class(spec.side_effect_class)
    connector_id, _, action_id = spec.action_ref.partition(".")
    all_input_fields = tuple(dict.fromkeys((*spec.required_input_fields, *spec.input_fields)))
    return ConnectorContract(
        action_ref=spec.action_ref,
        connector_id=connector_id,
        action_id=action_id,
        input_schema=_input_schema(all_input_fields, spec.required_input_fields),
        required_input_fields=spec.required_input_fields,
        output_schema={"type": "object", "additionalProperties": True},
        allowed_outputs=spec.output_fields,
        required_scopes=spec.required_scopes,
        secrets=tuple(ConnectorSecretRef(name, f"{spec.action_ref} connector env") for name in spec.required_env_names),
        side_effect_class=side_effect_class,
        approval_class=_approval_class(side_effect_class, spec.approval_required),
        live_modes=live_modes,
        idempotency_key_fields=spec.idempotency_key_fields,
        retry_class=RetryClass.IDEMPOTENT_WRITE if side_effect_class is not SideEffectClass.READ else RetryClass.READ_SAFE,
        target_policy=ConnectorTargetPolicy(required_target_fields=spec.required_target_fields),
        prompt_summary=f"{spec.action_ref}(input: {', '.join(all_input_fields) or 'object'}) -> {', '.join(spec.output_fields) or 'object'}",
        adapter=spec.adapter,
        execution_mode=spec.execution_mode,
        dry_run_supported=spec.dry_run_supported,
    )


def _connector_live_modes(values: tuple[str, ...]) -> tuple[ConnectorLiveMode, ...]:
    modes: list[ConnectorLiveMode] = []
    for value in values:
        try:
            mode = ConnectorLiveMode(value)
        except ValueError:
            continue
        if mode not in modes:
            modes.append(mode)
    return tuple(modes)


def _side_effect_class(value: str) -> SideEffectClass:
    try:
        return SideEffectClass(value)
    except ValueError:
        return SideEffectClass.READ


def _approval_class(side_effect_class: SideEffectClass, approval_required: bool) -> ApprovalClass:
    if not approval_required:
        return ApprovalClass.NONE
    if side_effect_class in {SideEffectClass.SEND, SideEffectClass.PUBLISH}:
        return ApprovalClass.EXTERNAL_SEND
    if side_effect_class is SideEffectClass.PROVISION:
        return ApprovalClass.INFRA_PROVISION
    return ApprovalClass.PRODUCTION_MUTATION


def _input_schema(input_fields: tuple[str, ...], required_input_fields: tuple[str, ...]) -> dict[str, object]:
    if not input_fields:
        return {"type": "object", "additionalProperties": True}
    schema: dict[str, object] = {
        "type": "object",
        "properties": {field: {} for field in input_fields},
        "additionalProperties": False,
    }
    if required_input_fields:
        schema["required"] = list(required_input_fields)
    return schema


def _transit_constraints(hints: Mapping[str, Any]) -> tuple[WorkflowTransitConstraint, ...]:
    raw_constraints = hints.get("transit_constraints")
    if not isinstance(raw_constraints, list):
        return ()
    constraints: list[WorkflowTransitConstraint] = []
    for raw_constraint in raw_constraints:
        if not isinstance(raw_constraint, Mapping):
            continue
        source = _string(raw_constraint.get("from")) or _string(raw_constraint.get("source_action"))
        target = _string(raw_constraint.get("to")) or _string(raw_constraint.get("target_action"))
        allowed = _string_tuple(raw_constraint.get("allowed_intermediates"))
        if source is None or target is None:
            continue
        constraints.append(
            WorkflowTransitConstraint(
                source_action=source,
                target_action=target,
                allowed_intermediates=allowed,
                issue_code=_string(raw_constraint.get("issue_code")) or "workflow_transit_constraint_violation",
                message_prefix=_string(raw_constraint.get("message_prefix")) or "workflow path",
                repair_hint=_string(raw_constraint.get("repair_hint")),
            )
        )
    return tuple(constraints)


def _mode_labels(action: Mapping[str, Any]) -> tuple[str, ...]:
    live_modes = _string_tuple(action.get("live_modes"))
    if live_modes:
        return live_modes
    execution_mode = _string(action.get("execution_mode"))
    return (execution_mode,) if execution_mode else ("custom",)


def _idempotency_key_fields(action: Mapping[str, Any]) -> tuple[str, ...]:
    idempotency = action.get("idempotency")
    if not isinstance(idempotency, Mapping):
        return ()
    return _string_tuple(idempotency.get("key_fields"))


def _connector_entries(connectors_document: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_connectors = connectors_document.get("connectors") if isinstance(connectors_document, Mapping) else None
    if not isinstance(raw_connectors, list):
        return ()
    return tuple(connector for connector in raw_connectors if isinstance(connector, Mapping))


def _action_entries(connector: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_actions = connector.get("actions")
    if not isinstance(raw_actions, list):
        return ()
    return tuple(action for action in raw_actions if isinstance(action, Mapping))


def _is_extension_action(connector: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    return (
        action.get("custom") is True
        or connector.get("custom") is True
        or isinstance(action.get("source"), str)
        or isinstance(connector.get("source"), str)
    )


def _is_design_only_action(action: Mapping[str, Any]) -> bool:
    return action.get("custom") is True and action.get("execution_mode") == "design_only"


def _requires_extension_qa(hints: Mapping[str, Any]) -> bool:
    return hints.get("qa_required") is True or hints.get("generated_by") == "coding_profile"


def _extension_qa_passed(action: Mapping[str, Any], action_ref: str) -> bool:
    qa = action.get("qa")
    if not isinstance(qa, Mapping):
        return False
    return validate_extension_qa_manifest(
        qa,
        expected_kind="connector",
        expected_extension_id=action_ref,
        expected_required_scopes=_string_tuple(action.get("required_scopes")),
        expected_allowed_outputs=_allowed_outputs(action),
    ).ok


def _allowed_outputs(action: Mapping[str, Any]) -> tuple[str, ...]:
    outputs = action.get("outputs")
    if not isinstance(outputs, Mapping):
        return ()
    return _string_tuple(outputs.get("allowed"))


def _required_env_names(*entries: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for entry in entries:
        raw_env = entry.get("env")
        if not isinstance(raw_env, list):
            continue
        for env_ref in raw_env:
            if not isinstance(env_ref, Mapping):
                continue
            name = _string(env_ref.get("name"))
            if name is None or env_ref.get("required") is False:
                continue
            if name not in names:
                names.append(name)
    return tuple(sorted(names))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _string_tuple_groups(value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list | tuple):
        return ()
    groups: list[tuple[str, ...]] = []
    for item in value:
        group = _string_tuple(item)
        if group:
            groups.append(group)
    return tuple(groups)


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
