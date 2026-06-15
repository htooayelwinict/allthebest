from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

KNOWN_DECISION_KINDS = frozenset(
    {"tool_call", "plan", "mutation_intent", "verify", "compact", "pause", "finalize"}
)


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
            "evidence_refs": list(self.evidence_refs),
        }
