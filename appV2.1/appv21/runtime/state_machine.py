"""Formal runtime transition policy for AppV2.1."""

from __future__ import annotations

from dataclasses import dataclass, field

from appv21.runtime.decisions import RuntimeDecision
from appv21.state.models import RuntimeMode


TRANSITIONS: dict[str, set[str]] = {
    "START": {"observe", "tool_call", "read_file", "pause"},
    "THINK": {"observe", "tool_call", "read_file", "plan", "mutation_intent", "verify", "compact", "pause", "finalize"},
    "OBSERVE": {"observe", "tool_call", "read_file", "plan", "compact", "pause", "finalize"},
    "PLAN": {"observe", "tool_call", "read_file", "mutation_intent", "compact", "pause", "finalize"},
    "ACT": {"verify", "observe", "tool_call", "read_file", "compact", "pause", "finalize"},
    "VERIFY": {"finalize", "plan", "observe", "tool_call", "read_file", "compact", "pause"},
    "REVISE": {"observe", "tool_call", "read_file", "plan", "pause"},
    "COMPACT": {"observe", "tool_call", "read_file", "plan", "mutation_intent", "verify", "pause", "finalize"},
    "PAUSE": set(),
    "FINALIZE": set(),
    "FAILED": set(),
}

TARGET_MODE_BY_DECISION: dict[str, RuntimeMode] = {
    "observe": "OBSERVE",
    "tool_call": "OBSERVE",
    "read_file": "OBSERVE",
    "plan": "PLAN",
    "mutation_intent": "ACT",
    "verify": "VERIFY",
    "compact": "COMPACT",
    "pause": "PAUSE",
    "finalize": "FINALIZE",
}


@dataclass
class RuntimeStateMachine:
    max_repeated_decisions: int = 3
    _repeated: dict[str, int] = field(default_factory=dict)

    def validate_transition(self, current_mode: str, decision: RuntimeDecision) -> str | None:
        allowed = TRANSITIONS.get(current_mode, set())
        if decision.kind not in allowed:
            return f"invalid_transition:{current_mode}->{decision.kind}"
        return None

    def next_mode(self, current_mode: str, decision: RuntimeDecision) -> RuntimeMode:
        rejection = self.validate_transition(current_mode, decision)
        if rejection is not None:
            raise ValueError(rejection)
        return TARGET_MODE_BY_DECISION[decision.kind]

    def record_progress(self, decision: RuntimeDecision, *, changed: bool) -> str | None:
        key = decision.kind
        if changed:
            self._repeated.clear()
            return None
        self._repeated[key] = self._repeated.get(key, 0) + 1
        if self._repeated[key] >= self.max_repeated_decisions:
            return f"repeated_loop:{key}"
        return None
