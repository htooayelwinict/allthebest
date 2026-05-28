"""Thin dispatcher that routes tasks to registered workers."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.registry import WorkerRegistry


class WorkerDispatcher:
    def __init__(self, registry: WorkerRegistry) -> None:
        self._registry = registry

    def dispatch(self, task: Task) -> Result:
        worker = self._registry.get(task.worker_type)
        return worker.run(task)
