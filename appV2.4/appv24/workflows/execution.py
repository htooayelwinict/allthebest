"""Workflow run execution data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.ledger import StepTrace


class WorkflowRunMode(StrEnum):
    PREVIEW = "preview"
    LOCAL = "local"
    STAGED = "staged"
    CREDENTIAL_PREFLIGHT = "credential-preflight"
    LIVE_READ = "live-read"
    LIVE_DRAFT = "live-draft"
    LIVE_PRODUCTION = "live-production"


@dataclass(frozen=True)
class WorkflowRunRequest:
    package_path: Path
    fixture_name: str | None = None
    mode: WorkflowRunMode = WorkflowRunMode.PREVIEW
    run_root: Path | None = None
    approval_run_id: str | None = None
    connector_allowlist: tuple[str, ...] = ()
    approve_all: bool = False
    activation_id: str | None = None
    activation_trigger_type: str | None = None


@dataclass(frozen=True)
class WorkflowRunEvent:
    event_id: str
    run_id: str
    event_type: str
    package_fingerprint: str
    step_id: str | None
    connector_action: str | None
    status: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowRunEvent":
        return cls(
            event_id=str(value["event_id"]),
            run_id=str(value["run_id"]),
            event_type=str(value["event_type"]),
            package_fingerprint=str(value["package_fingerprint"]),
            step_id=value["step_id"] if isinstance(value.get("step_id"), str) else None,
            connector_action=value["connector_action"] if isinstance(value.get("connector_action"), str) else None,
            status=str(value["status"]),
            details=value["details"] if isinstance(value.get("details"), dict) else {},
        )


@dataclass(frozen=True)
class WorkflowRunReport:
    run_id: str
    workflow_name: str
    mode: WorkflowRunMode
    status: str
    package_fingerprint: str
    terminal_step: str | None
    step_trace: tuple[StepTrace, ...]
    brokered_actions: tuple[Any, ...]
    approvals: tuple[Any, ...]
    events: tuple[WorkflowRunEvent, ...]
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        return data
