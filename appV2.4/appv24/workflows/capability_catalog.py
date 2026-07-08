"""User-facing workflow capability catalog derived from connector contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from appv24.workflows.connector_contracts import ConnectorContract, builtin_connector_contract_catalog, live_mode_values


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

GROUP_ORDER = ("Read data", "Transform locally", "Draft or send", "Other")


@dataclass(frozen=True)
class WorkflowCapability:
    group: str
    label: str
    action_ref: str
    input_fields: tuple[str, ...]
    required_input_fields: tuple[str, ...]
    output_fields: tuple[str, ...]
    mode_labels: tuple[str, ...]
    side_effect_class: str
    approval_required: bool
    required_env_names: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowCapabilityCatalog:
    capabilities: tuple[WorkflowCapability, ...]


def build_workflow_capability_catalog(
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> WorkflowCapabilityCatalog:
    contracts = builtin_connector_contract_catalog() if contract_catalog is None else contract_catalog
    capabilities = tuple(
        _capability(action_ref, contract)
        for action_ref, contract in sorted(contracts.items(), key=lambda item: (_group_sort_key(item[0]), item[0]))
    )
    return WorkflowCapabilityCatalog(capabilities=capabilities)


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
    return "\n".join(lines)


def _capability(action_ref: str, contract: ConnectorContract) -> WorkflowCapability:
    return WorkflowCapability(
        group=ACTION_GROUPS.get(action_ref, "Other"),
        label=ACTION_LABELS.get(action_ref, action_ref),
        action_ref=action_ref,
        input_fields=_input_fields(contract),
        required_input_fields=contract.required_input_fields,
        output_fields=contract.allowed_outputs,
        mode_labels=live_mode_values(contract) or ("local",),
        side_effect_class=contract.side_effect_class.value,
        approval_required=contract.approval_required,
        required_env_names=_required_env_names(contract),
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


def _group_sort_key(action_ref: str) -> tuple[int, str]:
    group = ACTION_GROUPS.get(action_ref, "Other")
    try:
        group_index = GROUP_ORDER.index(group)
    except ValueError:
        group_index = len(GROUP_ORDER)
    return group_index, action_ref
