"""Capability catalog contracts for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class CatalogCapability:
    action: str
    adapter: str
    side_effect: bool
    side_effect_class: str | None
    execution_mode: str
    approval_required: bool
    dry_run_supported: bool
    required_scopes: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    idempotency_key_fields: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityCatalog:
    capabilities: Mapping[str, CatalogCapability]

    def get(self, action: str) -> CatalogCapability | None:
        return self.capabilities.get(action)

    def require(self, action: str) -> CatalogCapability:
        capability = self.get(action)
        if capability is None:
            raise KeyError(action)
        return capability


def built_in_capability_catalog() -> CapabilityCatalog:
    from appv24.workflows.catalog_builtins import BUILT_IN_CAPABILITIES

    return CapabilityCatalog(BUILT_IN_CAPABILITIES)
