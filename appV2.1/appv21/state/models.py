"""Typed state models for AppV2.1."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal


RuntimeMode = Literal[
    "START",
    "OBSERVE",
    "THINK",
    "PLAN",
    "ACT",
    "VERIFY",
    "REVISE",
    "COMPACT",
    "PAUSE",
    "FINALIZE",
    "FAILED",
]


@dataclass
class RequestEnvelope:
    request_id: str
    user_goal: str
    root_path: str
    constraints: list[str] = field(default_factory=list)


@dataclass
class WorldRef:
    ref_id: str
    kind: str
    summary: str
    payload: dict[str, Any]
    trust: str = "runtime_observed"


@dataclass
class Artifact:
    artifact_id: str
    kind: str
    content: dict[str, Any]
    producer: str
    trust: str = "model_reported"
    lifecycle: str = "proposed"
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class MutationLease:
    lease_id: str
    operation_batch_id: str
    allowed_operations: list[dict[str, Any]]
    allowed_sources: list[str] = field(default_factory=list)
    allowed_destinations: list[str] = field(default_factory=list)
    risk_level: str = "low"
    requires_human: bool = False


@dataclass
class MutationReceipt:
    receipt_id: str
    lease_id: str
    status: str
    operations: list[dict[str, Any]]
    touched_paths: list[str]
    errors: list[str] = field(default_factory=list)


@dataclass
class PauseState:
    pause_id: str
    pause_type: str
    summary: str
    options: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ConversationState:
    messages: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


@dataclass
class WorldState:
    refs: dict[str, WorldRef] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    mutation_leases: dict[str, MutationLease] = field(default_factory=dict)
    mutation_receipts: dict[str, MutationReceipt] = field(default_factory=dict)
    verification_receipts: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class PlanState:
    intent: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    current_step: str | None = None
    unknowns: list[str] = field(default_factory=list)
    runtime_plan: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextState:
    compacted_turns: int = 0
    world_digest: dict[str, Any] = field(default_factory=dict)
    conversation_digest: str = ""


@dataclass
class CostState:
    model_calls: int = 0
    tool_calls: int = 0


@dataclass
class AgentState:
    session_id: str
    run_id: str
    request: RequestEnvelope
    mode: RuntimeMode = "START"
    conversation: ConversationState = field(default_factory=ConversationState)
    world: WorldState = field(default_factory=WorldState)
    plan: PlanState | None = None
    context: ContextState = field(default_factory=ContextState)
    pauses: list[PauseState] = field(default_factory=list)
    costs: CostState = field(default_factory=CostState)
    terminal: bool = False
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
