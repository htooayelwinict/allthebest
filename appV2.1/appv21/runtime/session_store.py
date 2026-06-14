"""Append-only JSONL session store for AppV2.1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appv21.state.events import RuntimeEvent


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    run_id: str
    event: RuntimeEvent
    parent_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = self.event.to_dict()
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "parent_event_id": self.parent_event_id,
            **data,
        }


class JsonlSessionStore:
    """Pi-style durable lineage with Hermes-style runtime-owned writes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_event(
        self,
        *,
        session_id: str,
        run_id: str,
        event: RuntimeEvent,
        parent_event_id: str | None = None,
    ) -> None:
        record = SessionRecord(session_id=session_id, run_id=run_id, event=event, parent_event_id=parent_event_id)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def events_for_run(self, *, session_id: str, run_id: str) -> list[RuntimeEvent]:
        return [
            event_from_record(row)
            for row in self.read_all()
            if row.get("session_id") == session_id and row.get("run_id") == run_id
        ]


def event_from_record(record: dict[str, Any]) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=str(record["event_type"]),
        payload=dict(record.get("payload") or {}),
        event_id=str(record["event_id"]),
        timestamp=str(record["timestamp"]),
    )
