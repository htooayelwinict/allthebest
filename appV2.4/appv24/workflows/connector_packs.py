"""Built-in appv24 workflow connector pack definitions."""

from __future__ import annotations

from appv24.workflows.connector_catalog import ConnectorActionMetadata, ConnectorCatalog
from appv24.workflows.connector_contracts import (
    ConnectorLiveMode,
    SideEffectClass,
    builtin_connector_contract_catalog,
)


def builtin_connector_catalog() -> ConnectorCatalog:
    actions = {
        action_ref: _metadata_from_contract(action_ref)
        for action_ref in sorted(builtin_connector_contract_catalog())
    }
    return ConnectorCatalog(actions=actions)


def _metadata_from_contract(action_ref: str) -> ConnectorActionMetadata:
    contract = builtin_connector_contract_catalog()[action_ref]
    return ConnectorActionMetadata(
        action_ref=contract.action_ref,
        side_effect=contract.side_effect,
        side_effect_class=None if contract.side_effect_class is SideEffectClass.READ else contract.side_effect_class.value,
        required_scopes=contract.required_scopes,
        allowed_outputs=contract.allowed_outputs,
        live_read_eligible=ConnectorLiveMode.LIVE_READ in contract.live_modes,
        live_draft_eligible=ConnectorLiveMode.LIVE_DRAFT in contract.live_modes,
        live_production_enabled=ConnectorLiveMode.LIVE_PRODUCTION in contract.live_modes,
        design_only=contract.design_only,
    )
