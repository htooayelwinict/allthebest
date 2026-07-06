"""Multi-phase workflow runner orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.credential_refs import CredentialRefResolver
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunReport, WorkflowRunRequest
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_runner import run_live_production_workflow, run_live_read_workflow
from appv24.workflows.runner import DEFAULT_FIXTURE_NAME, run_workflow_from_handoff


@dataclass(frozen=True)
class WorkflowPhaseRunReport:
    status: str
    live_read_run_id: str
    local_run_id: str
    approval_run_id: str | None
    phase_statuses: tuple[str, ...]
    next_commands: tuple[str, ...]
    live_production_run_id: str = ""
    live_production_report: WorkflowRunReport | None = None
    issues: tuple[str, ...] = ()


def run_workflow_live_read_to_approval(
    request: WorkflowRunRequest,
    *,
    live_read_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    local_broker: LocalWorkflowBroker | None = None,
) -> WorkflowPhaseRunReport:
    """Run live-read, then resume locally until an approval gate or terminal state."""

    live_report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=request.package_path,
            fixture_name=request.fixture_name,
            mode=WorkflowRunMode.LIVE_READ,
            run_root=request.run_root,
            connector_allowlist=request.connector_allowlist,
            activation_id=request.activation_id,
            activation_trigger_type=request.activation_trigger_type,
        ),
        broker=live_read_broker,
        credential_resolver=credential_resolver,
    )
    if live_report.status != "paused":
        return WorkflowPhaseRunReport(
            status=live_report.status,
            live_read_run_id=live_report.run_id,
            local_run_id="",
            approval_run_id=None,
            phase_statuses=(f"live-read: {live_report.status}",),
            next_commands=(),
            issues=live_report.issues,
        )

    local_report = run_workflow_from_handoff(
        WorkflowRunRequest(
            package_path=request.package_path,
            fixture_name=request.fixture_name,
            mode=WorkflowRunMode.LOCAL,
            run_root=request.run_root,
            approve_all=False,
            activation_id=request.activation_id,
            activation_trigger_type=request.activation_trigger_type,
        ),
        handoff_run_id=live_report.run_id,
        broker=local_broker,
    )
    approval_required = _approval_required(local_report.issues)
    status = "approval-required" if approval_required else local_report.status
    approval_run_id = local_report.run_id if approval_required else None
    return WorkflowPhaseRunReport(
        status=status,
        live_read_run_id=live_report.run_id,
        local_run_id=local_report.run_id,
        approval_run_id=approval_run_id,
        phase_statuses=(
            f"live-read: {live_report.status}",
            f"local-resume: {local_report.status}",
        ),
        next_commands=_next_commands(request, approval_run_id=approval_run_id),
        issues=local_report.issues,
    )


def run_workflow_approved_live_production(
    request: WorkflowRunRequest,
    *,
    approval_run_id: str,
    live_production_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
) -> WorkflowPhaseRunReport:
    """Run an approved workflow package through the live-production phase."""

    live_report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=request.package_path,
            fixture_name=request.fixture_name,
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=request.run_root,
            approval_run_id=approval_run_id,
            connector_allowlist=request.connector_allowlist,
            activation_id=request.activation_id,
            activation_trigger_type=request.activation_trigger_type,
        ),
        broker=live_production_broker,
        credential_resolver=credential_resolver,
    )
    return WorkflowPhaseRunReport(
        status=live_report.status,
        live_read_run_id="",
        local_run_id="",
        approval_run_id=approval_run_id,
        phase_statuses=(f"live-production: {live_report.status}",),
        next_commands=(),
        live_production_run_id=live_report.run_id,
        live_production_report=live_report,
        issues=live_report.issues,
    )


def _approval_required(issues: tuple[str, ...]) -> bool:
    return any(issue.startswith("approval required for ") for issue in issues)


def _next_commands(request: WorkflowRunRequest, *, approval_run_id: str | None) -> tuple[str, ...]:
    if approval_run_id is None:
        return ()
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    return (
        f"/workflow-approvals {package} {approval_run_id}",
        f"/workflow-live-plan {package} {fixture_name}",
    )
