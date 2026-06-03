"""Shared runtime matrix logging for graph and runtime observability."""

from __future__ import annotations

import itertools
from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock
from typing import Any


_TRACE_COUNTER = itertools.count(1)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _extract_runtime_matrix_snapshot(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, RuntimeMatrixLogger):
        return value.snapshot()
    if isinstance(value, dict):
        nested = value.get("runtime_matrix")
        if isinstance(nested, dict):
            return nested
        if "rows" in value and "trace_id" in value:
            return value
    return None


def coerce_runtime_matrix(
    logger: RuntimeMatrixLogger | None = None,
    *sources: Any | None,
) -> RuntimeMatrixLogger:
    if logger is not None:
        return logger
    for source in sources:
        snapshot = _extract_runtime_matrix_snapshot(source)
        if snapshot is not None:
            return RuntimeMatrixLogger.from_snapshot(snapshot)
    return RuntimeMatrixLogger()


def attach_runtime_matrix(metadata: dict[str, Any], logger: RuntimeMatrixLogger) -> dict[str, Any]:
    enriched = dict(metadata)
    enriched["runtime_matrix"] = logger.snapshot()
    return enriched


class RuntimeMatrixLogger:
    """Structured event recorder for end-to-end runtime execution."""

    def __init__(
        self,
        *,
        trace_id: str | None = None,
        started_at: str | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._lock = Lock()
        self.trace_id = trace_id or f"trace_{next(_TRACE_COUNTER):04d}"
        self.started_at = started_at or _now_iso()
        self._rows = list(rows or [])

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> RuntimeMatrixLogger:
        rows = snapshot.get("rows")
        return cls(
            trace_id=str(snapshot.get("trace_id") or f"trace_{next(_TRACE_COUNTER):04d}"),
            started_at=str(snapshot.get("started_at") or _now_iso()),
            rows=rows if isinstance(rows, list) else [],
        )

    def record(
        self,
        *,
        component: str,
        event: str,
        status: str,
        stage: str | None = None,
        request_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
        worker_type: str | None = None,
        elapsed_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            row = {
                "seq": len(self._rows) + 1,
                "timestamp": _now_iso(),
                "component": component,
                "stage": stage,
                "event": event,
                "status": status,
                "request_id": request_id,
                "plan_id": plan_id,
                "run_id": run_id,
                "step_id": step_id,
                "attempt_id": attempt_id,
                "worker_type": worker_type,
                "elapsed_ms": round(elapsed_ms, 3) if elapsed_ms is not None else None,
                "details": details or {},
            }
            self._rows.append(row)
        return row

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows = deepcopy(self._rows)
        return {
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "row_count": len(rows),
            "rows": rows,
        }
