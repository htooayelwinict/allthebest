"""Simulation ledger types for appv24 workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StepTrace:
    step_id: str
    kind: str
    status: str
    selected_branch: str | None = None
    connector_action: str | None = None
    side_effect_class: str | None = None
    approval_id: str | None = None
    simulated_outputs: Mapping[str, Any] | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class SimulationLedger:
    workflow_name: str
    fixture_name: str
    status: str
    trace: tuple[StepTrace, ...]
    terminal_step: str | None
    simulated_side_effects: tuple[str, ...]
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
