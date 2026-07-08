"""Capability extraction for appv24 workflow IR."""

from __future__ import annotations

from typing import Any, Mapping

from appv24.workflows.connector_contracts import ConnectorContract, builtin_connector_contract_catalog
from appv24.workflows.ir import ActionCapability, ConnectorCapability


def build_connector_capabilities(
    connectors_document: Mapping[str, Any],
    permissions_document: Mapping[str, Any],
) -> dict[str, ConnectorCapability]:
    granted_scopes = _permission_scopes(permissions_document)
    builtin_contracts = builtin_connector_contract_catalog()
    capabilities: dict[str, ConnectorCapability] = {}
    for connector in connectors_document.get("connectors", []):
        if not isinstance(connector, dict) or not isinstance(connector.get("id"), str):
            continue
        connector_id = str(connector["id"])
        adapter = str(connector.get("adapter", "manual"))
        actions: dict[str, ActionCapability] = {}
        for action in connector.get("actions", []):
            if not isinstance(action, dict) or not isinstance(action.get("id"), str):
                continue
            action_id = str(action["id"])
            contract = builtin_contracts.get(f"{connector_id}.{action_id}")
            action_raw = _action_raw_with_connector_interface(action, connector)
            actions[action_id] = ActionCapability(
                connector_id=connector_id,
                action_id=action_id,
                adapter=adapter,
                side_effect=bool(action.get("side_effect")),
                side_effect_class=_optional_str(action.get("side_effect_class")),
                execution_mode=_optional_str(action.get("execution_mode")),
                approval_required=_optional_bool(action.get("approval_required")),
                dry_run_supported=_optional_bool(action.get("dry_run_supported")),
                idempotency_key_fields=tuple(_string_list(_mapping(action.get("idempotency")).get("key_fields"))),
                allowed_outputs=_allowed_outputs(action, contract),
                required_scopes=tuple(_string_list(action.get("required_scopes"))),
                granted_scopes=tuple(sorted(granted_scopes.get(connector_id, set()))),
                raw=action_raw,
            )
        capabilities[connector_id] = ConnectorCapability(
            connector_id=connector_id,
            adapter=adapter,
            actions=actions,
            raw=connector,
        )
    return capabilities


def _permission_scopes(permissions_document: Mapping[str, Any]) -> dict[str, set[str]]:
    scopes: dict[str, set[str]] = {}
    for permission in permissions_document.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        scopes[str(permission["connector"])] = {
            str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str)
        }
    return scopes


def _action_raw_with_connector_interface(action: Mapping[str, Any], connector: Mapping[str, Any]) -> Mapping[str, Any]:
    inherited: dict[str, Any] = dict(action)
    for key in ("custom", "source", "env"):
        if key not in inherited and key in connector:
            inherited[key] = connector[key]
    return inherited


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _allowed_outputs(action: Mapping[str, Any], contract: ConnectorContract | None) -> tuple[str, ...]:
    outputs = _mapping(action.get("outputs"))
    if "allowed" in outputs:
        return tuple(_string_list(outputs.get("allowed")))
    if contract is None:
        return ()
    return contract.allowed_outputs


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
