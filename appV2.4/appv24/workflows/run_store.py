"""Append-only workflow run storage."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from appv24.workflows.execution import WorkflowRunEvent

SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")


class WorkflowRunStore:
    def __init__(self, root: Path):
        self.root = root

    def create_run(self, workflow_name: str, package_fingerprint: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        run_id = f"run-{uuid4().hex}"
        run_dir = self.root / run_id
        run_dir.mkdir()
        (run_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "workflow_name": workflow_name,
                    "package_fingerprint": package_fingerprint,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "events.jsonl").write_text("", encoding="utf-8")
        return run_id

    def append_event(self, event: WorkflowRunEvent) -> None:
        run_dir = self.root / event.run_id
        if not run_dir.is_dir():
            raise ValueError(f"unknown workflow run: {event.run_id}")
        encoded = json.dumps(event.to_dict(), sort_keys=True)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow run event contains a raw secret-looking value")
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")

    def read_events(self, run_id: str) -> tuple[WorkflowRunEvent, ...]:
        path = self.root / run_id / "events.jsonl"
        if not path.is_file():
            raise ValueError(f"unknown workflow run: {run_id}")
        events: list[WorkflowRunEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(WorkflowRunEvent.from_dict(json.loads(line)))
        return tuple(events)
