"""Base worker protocol."""

from __future__ import annotations

from typing import Protocol

from app.schemas import Result, Task


class BaseWorker(Protocol):
    worker_type: str

    def run(self, task: Task) -> Result:
        """Run the task and return a `Result`."""
