"""User-facing workflow capability catalog derived from connector contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from appv24.workflows.connector_contracts import ConnectorContract, builtin_connector_contract_catalog, live_mode_values
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.middleware_contracts import MiddlewareContract, builtin_middleware_contract_catalog
from appv24.workflows.workflow_extensions import (
    WorkflowActionExtensionSpec,
    WorkflowExtensionProfile,
    WorkflowMiddlewareExtensionSpec,
    build_workflow_extension_profile,
)


ACTION_LABELS = {
    "brave.search": "Search web/news with Brave Search",
    "http.fetch": "Fetch an allowed URL",
    "source.fetch_document": "Fetch source document",
    "source.fetch_items": "Fetch source items",
    "telegram.list_subscribers": "List Telegram subscribers",
    "llm.summarize": "Summarize text/items",
    "llm.classify": "Classify text",
    "llm.extract_structured": "Extract structured fields",
    "policy.check": "Check deterministic policy",
    "gmail.create_draft": "Create Gmail draft",
    "notification.create_draft": "Create notification draft",
    "telegram.create_message_draft": "Create Telegram message draft",
    "telegram.send_message": "Send Telegram message to one allowed chat",
    "telegram.broadcast_message": "Broadcast Telegram message to registered subscribers",
}

ACTION_GROUPS = {
    "brave.search": "Read data",
    "http.fetch": "Read data",
    "source.fetch_document": "Read data",
    "source.fetch_items": "Read data",
    "telegram.list_subscribers": "Read data",
    "llm.summarize": "Transform locally",
    "llm.classify": "Transform locally",
    "llm.extract_structured": "Transform locally",
    "policy.check": "Transform locally",
    "gmail.create_draft": "Draft or send",
    "notification.create_draft": "Draft or send",
    "telegram.create_message_draft": "Draft or send",
    "telegram.send_message": "Draft or send",
    "telegram.broadcast_message": "Draft or send",
}

MIDDLEWARE_LABELS = {
    "audit.log": "Audit log",
}

GROUP_ORDER = ("Read data", "Transform locally", "Draft or send", "Other")


@dataclass(frozen=True)
class WorkflowCapability:
    group: str
    label: str
    action_ref: str
    input_fields: tuple[str, ...]
    required_input_fields: tuple[str, ...]
    required_input_any_groups: tuple[tuple[str, ...], ...]
    output_fields: tuple[str, ...]
    mode_labels: tuple[str, ...]
    side_effect_class: str
    approval_required: bool
    required_env_names: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowMiddlewareCapability:
    label: str
    middleware_id: str
    adapter: str
    source_ref: str
    required_env_names: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowCapabilityCatalog:
    capabilities: tuple[WorkflowCapability, ...]
    middleware_capabilities: tuple[WorkflowMiddlewareCapability, ...] = ()
    package_authoring_context: str = ""


def build_workflow_capability_catalog(
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
    *,
    documents: PackageDocuments | None = None,
    extension_profile: WorkflowExtensionProfile | None = None,
    package_authoring_context: str = "",
) -> WorkflowCapabilityCatalog:
    contracts = builtin_connector_contract_catalog() if contract_catalog is None else contract_catalog
    middleware_contracts = builtin_middleware_contract_catalog() if contract_catalog is None else {}
    profile = extension_profile or build_workflow_extension_profile(documents, contract_catalog=contracts)
    builtin_capabilities = tuple(
        _capability(action_ref, contract)
        for action_ref, contract in sorted(contracts.items(), key=lambda item: (_group_sort_key(item[0]), item[0]))
    )
    extension_capabilities = tuple(
        _extension_capability(spec)
        for spec in sorted(
            profile.action_specs,
            key=lambda item: (_group_sort_key(item.action_ref, item.catalog_group), item.action_ref),
        )
        if spec.action_ref not in contracts
    )
    builtin_middleware_capabilities = tuple(
        _middleware_capability(middleware_id, contract)
        for middleware_id, contract in sorted(middleware_contracts.items())
    )
    extension_middleware_capabilities = tuple(
        _extension_middleware_capability(spec)
        for spec in sorted(profile.middleware_specs, key=lambda item: item.middleware_id)
        if spec.middleware_id not in middleware_contracts
    )
    return WorkflowCapabilityCatalog(
        capabilities=(*builtin_capabilities, *extension_capabilities),
        middleware_capabilities=(*builtin_middleware_capabilities, *extension_middleware_capabilities),
        package_authoring_context=package_authoring_context.strip(),
    )


def format_workflow_capability_catalog(catalog: WorkflowCapabilityCatalog) -> str:
    lines = ["Workflow capability catalog"]
    for group in GROUP_ORDER:
        items = tuple(item for item in catalog.capabilities if item.group == group)
        if not items:
            continue
        lines.append("")
        lines.append(group)
        for item in items:
            approval = "required" if item.approval_required else "not required"
            modes = ", ".join(item.mode_labels)
            inputs = ", ".join(item.input_fields) if item.input_fields else "none"
            outputs = ", ".join(item.output_fields) if item.output_fields else "none"
            env = ", ".join(item.required_env_names) if item.required_env_names else "none"
            lines.append(f"- {item.label} ({item.action_ref})")
            lines.append(f"  modes: {modes}; side effect: {item.side_effect_class}; approval: {approval}")
            lines.append(f"  inputs: {inputs}")
            lines.append(f"  outputs: {outputs}")
            lines.append(f"  env: {env}")
    if catalog.middleware_capabilities:
        lines.append("")
        lines.append("Middleware")
        for item in catalog.middleware_capabilities:
            env = ", ".join(item.required_env_names) if item.required_env_names else "none"
            lines.append(f"- {item.label} ({item.middleware_id})")
            lines.append(f"  adapter: {item.adapter}; source: {item.source_ref}")
            lines.append(f"  env: {env}")
    return "\n".join(lines)


def format_workflow_extension_capability_context(
    catalog: WorkflowCapabilityCatalog | None,
    *,
    include_package_authoring_context: bool = False,
) -> str:
    if catalog is None or (not catalog.capabilities and not catalog.middleware_capabilities):
        return ""

    lines = [
        "Active reusable workflow extension catalog:",
        "Use these connector-owned action refs and middleware ids exactly when they match the user's request.",
        "If a needed action is absent, do not invent it; produce an extension handoff need instead.",
    ]
    for item in catalog.capabilities:
        approval = "required" if item.approval_required else "not required"
        modes = ", ".join(item.mode_labels) if item.mode_labels else "local"
        inputs = ", ".join(item.input_fields) if item.input_fields else "none"
        required_inputs = ", ".join(item.required_input_fields) if item.required_input_fields else "none"
        required_any = _required_any_label(item.required_input_any_groups)
        outputs = ", ".join(item.output_fields) if item.output_fields else "none"
        env = ", ".join(item.required_env_names) if item.required_env_names else "none"
        lines.append(f"- {item.label} ({item.action_ref})")
        lines.append(f"  modes: {modes}; side effect: {item.side_effect_class}; approval: {approval}")
        lines.append(f"  inputs: {inputs}; required inputs: {required_inputs}; one of: {required_any}")
        lines.append(f"  outputs: {outputs}")
        lines.append(f"  env: {env}")
    if catalog.middleware_capabilities:
        lines.append("Middleware interfaces:")
        for item in catalog.middleware_capabilities:
            env = ", ".join(item.required_env_names) if item.required_env_names else "none"
            lines.append(f"- {item.label} ({item.middleware_id})")
            lines.append(f"  adapter: {item.adapter}; source: {item.source_ref}")
            lines.append(f"  env: {env}")
    if include_package_authoring_context and catalog.package_authoring_context:
        lines.append("")
        lines.append(catalog.package_authoring_context)
    return "\n".join(lines)


def _capability(action_ref: str, contract: ConnectorContract) -> WorkflowCapability:
    return WorkflowCapability(
        group=ACTION_GROUPS.get(action_ref, "Other"),
        label=ACTION_LABELS.get(action_ref, action_ref),
        action_ref=action_ref,
        input_fields=_input_fields(contract),
        required_input_fields=contract.required_input_fields,
        required_input_any_groups=(),
        output_fields=contract.allowed_outputs,
        mode_labels=live_mode_values(contract) or ("local",),
        side_effect_class=contract.side_effect_class.value,
        approval_required=contract.approval_required,
        required_env_names=_required_env_names(contract),
    )


def _extension_capability(spec: WorkflowActionExtensionSpec) -> WorkflowCapability:
    return WorkflowCapability(
        group=spec.catalog_group,
        label=spec.catalog_label,
        action_ref=spec.action_ref,
        input_fields=spec.input_fields,
        required_input_fields=spec.required_input_fields,
        required_input_any_groups=spec.required_input_any_groups,
        output_fields=spec.output_fields,
        mode_labels=spec.mode_labels,
        side_effect_class=spec.side_effect_class,
        approval_required=spec.approval_required,
        required_env_names=spec.required_env_names,
    )


def _middleware_capability(middleware_id: str, contract: MiddlewareContract) -> WorkflowMiddlewareCapability:
    return WorkflowMiddlewareCapability(
        label=MIDDLEWARE_LABELS.get(middleware_id, middleware_id),
        middleware_id=middleware_id,
        adapter=contract.adapter,
        source_ref=contract.source_ref,
        required_env_names=tuple(env_ref.env_name for env_ref in contract.env if env_ref.required),
    )


def _extension_middleware_capability(spec: WorkflowMiddlewareExtensionSpec) -> WorkflowMiddlewareCapability:
    return WorkflowMiddlewareCapability(
        label=spec.catalog_label,
        middleware_id=spec.middleware_id,
        adapter=spec.adapter,
        source_ref=spec.source_ref,
        required_env_names=spec.required_env_names,
    )


def _input_fields(contract: ConnectorContract) -> tuple[str, ...]:
    properties = contract.input_schema.get("properties", {})
    if not isinstance(properties, dict):
        return contract.required_input_fields
    return tuple(sorted(str(name) for name in properties))


def _required_env_names(contract: ConnectorContract) -> tuple[str, ...]:
    names = {secret.env_name for secret in contract.secrets if secret.required}
    if contract.target_policy.allow_env:
        names.add(contract.target_policy.allow_env)
    if contract.target_policy.allowed_chat_ids_env:
        names.add(contract.target_policy.allowed_chat_ids_env)
    return tuple(sorted(names))


def _required_any_label(groups: tuple[tuple[str, ...], ...]) -> str:
    if not groups:
        return "none"
    return "; ".join(" or ".join(group) for group in groups)


def _group_sort_key(action_ref: str, group_override: str | None = None) -> tuple[int, str]:
    group = group_override or ACTION_GROUPS.get(action_ref, "Other")
    try:
        group_index = GROUP_ORDER.index(group)
    except ValueError:
        group_index = len(GROUP_ORDER)
    return group_index, action_ref
