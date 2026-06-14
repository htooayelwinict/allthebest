"""Capability-scoped extension runner for AppV2.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from appv21.state.events import RuntimeEvent
from appv21.state.models import AgentState


class RuntimeExtension(Protocol):
    extension_id: str
    capabilities: set[str]

    def handle(self, hook: str, state: AgentState, payload: dict[str, Any]) -> list[RuntimeEvent]: ...


@dataclass
class ExtensionRunner:
    """Runs advisory extensions without letting them bypass runtime gates."""

    extensions: list[RuntimeExtension] = field(default_factory=list)

    def run_hook(self, hook: str, state: AgentState, payload: dict[str, Any] | None = None) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for extension in self.extensions:
            if hook not in extension.capabilities:
                continue
            try:
                produced = extension.handle(hook, state, dict(payload or {}))
                events.extend(produced)
            except Exception as exc:
                events.append(
                    RuntimeEvent(
                        "ExtensionFailed",
                        {
                            "extension_id": extension.extension_id,
                            "hook": hook,
                            "error": str(exc),
                        },
                    )
                )
        return events


@dataclass
class TraceExtension:
    """Built-in trace extension for probeable hook ordering."""

    extension_id: str = "appv21.trace"
    capabilities: set[str] = field(
        default_factory=lambda: {
            "before_observe",
            "after_observe",
            "before_plan",
            "after_plan",
            "before_mutation",
            "after_mutation",
            "before_verify",
            "after_verify",
            "finalize",
        }
    )

    def handle(self, hook: str, state: AgentState, payload: dict[str, Any]) -> list[RuntimeEvent]:
        return [
            RuntimeEvent(
                "ExtensionTraceRecorded",
                {
                    "extension_id": self.extension_id,
                    "hook": hook,
                    "mode": state.mode,
                    "payload_keys": sorted(payload),
                },
            )
        ]
