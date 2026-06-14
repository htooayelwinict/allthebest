"""In-memory event store for AppV2.1."""

from __future__ import annotations

from appv21.state.events import RuntimeEvent


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []

    def append(self, event: RuntimeEvent) -> None:
        self._events.append(event)

    def extend(self, events: list[RuntimeEvent]) -> None:
        for event in events:
            self.append(event)

    def all(self) -> list[RuntimeEvent]:
        return list(self._events)

    def to_dicts(self) -> list[dict]:
        return [event.to_dict() for event in self._events]
