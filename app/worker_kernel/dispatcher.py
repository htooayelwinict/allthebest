"""Thin dispatcher that routes tasks to registered workers."""

from __future__ import annotations

import inspect

from app.runtime_matrix import RuntimeMatrixLogger
from app.schemas import Result, Task
from app.worker_kernel.registry import WorkerRegistry


class WorkerDispatcher:
    def __init__(self, registry: WorkerRegistry) -> None:
        self._registry = registry

    def dispatch(self, task: Task, *, trace: RuntimeMatrixLogger | None = None) -> Result:
        worker = self._registry.get(task.worker_type)
        try:
            signature = inspect.signature(worker.run)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and "trace" in signature.parameters:
            return worker.run(task, trace=trace)
        return worker.run(task)
