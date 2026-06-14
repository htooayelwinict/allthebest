"""Typed runtime decisions for the AppV2.1 agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args
from uuid import uuid4

DecisionKind = Literal[
    "observe",
    "read_file",
    "plan",
    "tool_call",
    "mutation_intent",
    "verify",
    "pause",
    "compact",
    "finalize",
]


@dataclass(frozen=True)
class RuntimeDecision:
    kind: str
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    decision_id: str = field(default_factory=lambda: f"dec_{uuid4().hex}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "kind": self.kind,
            "reason": self.reason,
            "payload": self.payload,
            "evidence_refs": self.evidence_refs,
        }


KNOWN_DECISION_KINDS = set(get_args(DecisionKind))


def parse_runtime_decision(raw: dict[str, Any]) -> RuntimeDecision:
    kind = raw.get("kind")
    if kind not in KNOWN_DECISION_KINDS:
        return RuntimeDecision(
            kind=str(kind or "unknown"),
            reason=f"Rejected unknown decision kind: {kind}",
            payload={"rejected_kind": kind, "rejection_reason": "unknown_decision_kind"},
        )
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    evidence_refs = raw.get("evidence_refs") if isinstance(raw.get("evidence_refs"), list) else []
    return RuntimeDecision(
        kind=kind,
        reason=str(raw.get("reason") or ""),
        payload=payload,
        evidence_refs=[str(ref) for ref in evidence_refs],
        decision_id=str(raw.get("decision_id") or f"dec_{uuid4().hex}"),
    )


def observe_decision(reason: str = "Need current repo map before planning.") -> RuntimeDecision:
    return RuntimeDecision(kind="observe", reason=reason, payload={"tool_name": "repo_snapshot"})


def plan_decision(*, evidence_refs: list[str], reason: str = "Plan from observed world state.") -> RuntimeDecision:
    return RuntimeDecision(kind="plan", reason=reason, evidence_refs=evidence_refs)


def mutation_decision(*, plan: dict[str, Any], reason: str = "Apply runtime-compiled mutation intent.") -> RuntimeDecision:
    mutation_intent = plan.get("mutation_intent") or {}
    return RuntimeDecision(kind="mutation_intent", reason=reason, payload=mutation_intent, evidence_refs=["plan://accepted/latest"])


def verify_decision(*, plan: dict[str, Any], reason: str = "Verify applied mutation evidence.") -> RuntimeDecision:
    return RuntimeDecision(
        kind="verify",
        reason=reason,
        payload=plan.get("verification_intent") or {},
        evidence_refs=["plan://accepted/latest"],
    )


def finalize_decision(reason: str = "Finalize after successful verification.") -> RuntimeDecision:
    return RuntimeDecision(kind="finalize", reason=reason, evidence_refs=["verification://latest"])


def compact_decision(reason: str = "Compact context before continuing.") -> RuntimeDecision:
    return RuntimeDecision(kind="compact", reason=reason)


def pause_decision(*, reason: str, payload: dict[str, Any] | None = None) -> RuntimeDecision:
    return RuntimeDecision(kind="pause", reason=reason, payload=payload or {})
