"""Typed workflow connector catalog metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ConnectorActionMetadata:
    action_ref: str
    side_effect: bool
    side_effect_class: str | None = None
    required_scopes: tuple[str, ...] = ()
    allowed_outputs: tuple[str, ...] = ()
    live_read_eligible: bool = False
    live_draft_eligible: bool = False
    live_production_enabled: bool = False
    design_only: bool = False


@dataclass(frozen=True)
class ConnectorCatalog:
    actions: Mapping[str, ConnectorActionMetadata]

    @property
    def action_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self.actions))

    def has_action(self, action_ref: str) -> bool:
        return action_ref in self.actions

    def action(self, action_ref: str) -> ConnectorActionMetadata:
        try:
            return self.actions[action_ref]
        except KeyError as error:
            raise KeyError(f"unknown workflow connector action: {action_ref}") from error
