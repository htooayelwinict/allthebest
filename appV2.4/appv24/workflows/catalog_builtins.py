"""Built-in appv24 draft-safe capability manifests."""

from __future__ import annotations

from appv24.workflows.catalog import CatalogCapability
from appv24.workflows.connector_contracts import (
    SideEffectClass,
    builtin_connector_contract_catalog,
)


def _capability_from_contract(action_ref: str) -> CatalogCapability:
    contract = builtin_connector_contract_catalog()[action_ref]
    return CatalogCapability(
        action=contract.action_ref,
        adapter=contract.adapter,
        side_effect=contract.side_effect,
        side_effect_class=None if contract.side_effect_class is SideEffectClass.READ else contract.side_effect_class.value,
        execution_mode=contract.execution_mode,
        approval_required=contract.approval_required,
        dry_run_supported=contract.dry_run_supported,
        required_scopes=contract.required_scopes,
        allowed_outputs=contract.allowed_outputs,
        idempotency_key_fields=contract.idempotency_key_fields,
    )


BUILT_IN_CAPABILITIES: dict[str, CatalogCapability] = {
    action_ref: _capability_from_contract(action_ref)
    for action_ref in sorted(builtin_connector_contract_catalog())
}
