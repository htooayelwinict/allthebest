"""Persistable trigger runtime state contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowTriggerState:
    activation_id: str
    trigger_type: str
    package_fingerprint: str
    cursor: str | None
    last_run_id: str | None
    last_started_at: str | None
    last_finished_at: str | None
    consecutive_failures: int
    enabled: bool
