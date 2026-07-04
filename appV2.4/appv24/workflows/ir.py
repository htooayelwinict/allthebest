"""Typed intermediate representation for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.errors import WorkflowIssue


class StepKind(Enum):
    ACTION = "action"
    DECISION = "decision"
    MANUAL_APPROVAL = "manual_approval"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class ConnectorRef:
    connector_id: str
    action_id: str

    @property
    def dotted(self) -> str:
        return f"{self.connector_id}.{self.action_id}"


@dataclass(frozen=True)
class ActionCapability:
    connector_id: str
    action_id: str
    adapter: str
    side_effect: bool
    side_effect_class: str | None
    execution_mode: str | None
    approval_required: bool | None
    dry_run_supported: bool | None
    idempotency_key_fields: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    required_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    raw: Mapping[str, Any]

    @property
    def dotted(self) -> str:
        return f"{self.connector_id}.{self.action_id}"


@dataclass(frozen=True)
class ConnectorCapability:
    connector_id: str
    adapter: str
    actions: Mapping[str, ActionCapability]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ApprovalRequirement:
    approval_id: str
    required_for: tuple[str, ...]
    mode: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowTrigger:
    trigger_id: str
    trigger_type: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowDesignContract:
    intent: str
    trigger: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    assumptions: tuple[str, ...]
    side_effects: tuple[str, ...]
    approval_gates: tuple[str, ...]
    failure_modes: tuple[str, ...]
    rollback: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    kind: StepKind
    uses: ConnectorRef | None
    side_effect_class: str | None
    approval: str | None
    next_step: str | None
    branches: Mapping[str, str]
    outbound: tuple[str, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowIR:
    root: Path
    name: str
    version: str
    entrypoint: str
    triggers: tuple[WorkflowTrigger, ...]
    steps: Mapping[str, WorkflowStep]
    connectors: Mapping[str, ConnectorCapability]
    approvals: Mapping[str, ApprovalRequirement]
    design: WorkflowDesignContract


@dataclass(frozen=True)
class WorkflowCompileResult:
    root: Path
    ir: WorkflowIR | None
    issues: tuple[WorkflowIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues and self.ir is not None
