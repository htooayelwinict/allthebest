"""In-process runtime event bus for AppV2.1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from appv21.state.events import RuntimeEvent

EventHandler = Callable[[RuntimeEvent], None]


@dataclass
class EventBus:
    """Small synchronous event bus with isolated subscriber failures.

    Pi keeps lifecycle events decoupled from UI/extensions. Hermes keeps runtime
    internals behind one facade. This bus gives AppV2.1 the same boundary: core
    state reduction is not allowed to depend on observers succeeding.
    """

    _subscribers: dict[str, list[EventHandler]] = field(default_factory=dict)
    _dead_letters: list[dict[str, Any]] = field(default_factory=list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: RuntimeEvent) -> None:
        handlers = [*self._subscribers.get("*", []), *self._subscribers.get(event.event_type, [])]
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # pragma: no cover - defensive isolation
                self._dead_letters.append(
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "handler": getattr(handler, "__name__", handler.__class__.__name__),
                        "error": str(exc),
                    }
                )

    def dead_letters(self) -> list[dict[str, Any]]:
        return list(self._dead_letters)
