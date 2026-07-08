"""Guided user-mode orchestration over existing workflow live phases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.connector_contracts import ConnectorLiveMode, builtin_connector_contract_catalog, workflow_operator_env_from_env
from appv24.workflows.credential_refs import CredentialRefResolver
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_plan import build_workflow_live_plan_from_path
from appv24.workflows.live_readiness import inspect_workflow_live_readiness
from appv24.workflows.phase_runner import WorkflowPhaseRunReport, run_workflow_approved_live_production, run_workflow_live_read_to_approval
from appv24.workflows.run_store import WorkflowRunStore


@dataclass(frozen=True)
class GuidedWorkflowRunReport:
    status: str
    package_path: Path
    fixture_name: str
    live_plan_status: str = ""
    live_read_run_id: str = ""
    local_run_id: str = ""
    approval_run_id: str | None = None
    approval_request_ids: tuple[str, ...] = ()
    readiness_status: str = ""
    live_production_run_id: str = ""
    issues: tuple[str, ...] = ()
    next_action: str = ""


ReadinessInspector = Callable[[WorkflowRunRequest, Mapping[str, str]], object]


@dataclass(frozen=True)
class _ApprovalItem:
    approval_request_id: str
    status: str
    connector_action: str


def prepare_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    live_read_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    local_broker: LocalWorkflowBroker | None = None,
) -> GuidedWorkflowRunReport:
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or "happy_path.yaml"
    plan = build_workflow_live_plan_from_path(package, fixture_name)
    if plan.status == "blocked":
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            live_plan_status=plan.status,
            issues=plan.issues,
            next_action="fix workflow package",
        )

    allowlist = _allowlist_from_command(plan.next_commands, "/workflow-run-live-read-to-approval")
    if not allowlist:
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            live_plan_status=plan.status,
            issues=("guided run requires a live-read-to-approval phase",),
            next_action="inspect live plan",
        )

    phase_report = run_workflow_live_read_to_approval(
        WorkflowRunRequest(
            package_path=package,
            fixture_name=fixture_name,
            mode=WorkflowRunMode.LIVE_READ,
            run_root=request.run_root,
            connector_allowlist=allowlist,
            activation_id=request.activation_id,
            activation_trigger_type=request.activation_trigger_type,
            operator_env=request.operator_env,
        ),
        live_read_broker=live_read_broker,
        credential_resolver=credential_resolver,
        local_broker=local_broker,
    )
    return _from_phase_report(package, fixture_name, plan.status, phase_report)


def continue_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    approval_run_id: str,
    live_production_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    env: Mapping[str, str],
    readiness_inspector: ReadinessInspector | None = None,
) -> GuidedWorkflowRunReport:
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or "happy_path.yaml"
    approvals = _approval_items(request, approval_run_id=approval_run_id)
    pending = tuple(item.approval_request_id for item in approvals if item.status == "pending")
    if pending:
        return GuidedWorkflowRunReport(
            status="approval-required",
            package_path=package,
            fixture_name=fixture_name,
            approval_run_id=approval_run_id,
            approval_request_ids=pending,
            next_action="approve external side effect",
        )
    denied = tuple(item.approval_request_id for item in approvals if item.status == "denied")
    if denied:
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            approval_run_id=approval_run_id,
            approval_request_ids=denied,
            issues=("approval denied for external side effect",),
            next_action="rerun approval preparation",
        )

    production_actions = _production_actions_for_approvals(approvals)
    if not production_actions:
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            approval_run_id=approval_run_id,
            issues=("no approved live-production connector actions found",),
            next_action="inspect approvals",
        )

    production_request = WorkflowRunRequest(
        package_path=package,
        fixture_name=fixture_name,
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        run_root=request.run_root,
        approval_run_id=approval_run_id,
        connector_allowlist=production_actions,
        activation_id=request.activation_id,
        activation_trigger_type=request.activation_trigger_type,
        operator_env=request.operator_env or workflow_operator_env_from_env(env),
    )
    readiness = (readiness_inspector or _inspect_readiness)(production_request, env)
    readiness_status = _readiness_status(readiness)
    readiness_issues = _readiness_issues(readiness)
    if readiness_status != "ready":
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            approval_run_id=approval_run_id,
            readiness_status=readiness_status,
            issues=readiness_issues,
            next_action="fix readiness issues",
        )

    phase_report = run_workflow_approved_live_production(
        production_request,
        approval_run_id=approval_run_id,
        live_production_broker=live_production_broker,
        credential_resolver=credential_resolver,
    )
    return GuidedWorkflowRunReport(
        status=phase_report.status,
        package_path=package,
        fixture_name=fixture_name,
        approval_run_id=approval_run_id,
        readiness_status=readiness_status,
        live_production_run_id=phase_report.live_production_run_id,
        issues=phase_report.issues,
        next_action="" if phase_report.status == "passed" else "inspect live-production report",
    )


def format_guided_workflow_run_report(report: GuidedWorkflowRunReport) -> str:
    lines = [
        f"Workflow guided run: {report.status}",
        f"package: {report.package_path}",
        f"fixture: {report.fixture_name}",
    ]
    if report.live_plan_status:
        lines.append(f"live plan: {report.live_plan_status}")
    if report.live_read_run_id:
        lines.append(f"live-read run: {report.live_read_run_id}")
    if report.local_run_id:
        lines.append(f"local run: {report.local_run_id}")
    if report.approval_run_id:
        lines.append(f"approval run: {report.approval_run_id}")
    if report.approval_request_ids:
        lines.append("approval requests:")
        lines.extend(f"- {request_id}" for request_id in report.approval_request_ids)
    if report.readiness_status:
        lines.append(f"readiness: {report.readiness_status}")
    if report.live_production_run_id:
        lines.append(f"live-production run: {report.live_production_run_id}")
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    if report.next_action:
        lines.append(f"next action: {report.next_action}")
    return "\n".join(lines)


def _from_phase_report(
    package: Path,
    fixture_name: str,
    live_plan_status: str,
    phase_report: WorkflowPhaseRunReport,
) -> GuidedWorkflowRunReport:
    next_action = "approve external side effect" if phase_report.status == "approval-required" else ""
    if phase_report.status not in {"passed", "approval-required"} and not next_action:
        next_action = "inspect phase report"
    return GuidedWorkflowRunReport(
        status=phase_report.status,
        package_path=package,
        fixture_name=fixture_name,
        live_plan_status=live_plan_status,
        live_read_run_id=phase_report.live_read_run_id,
        local_run_id=phase_report.local_run_id,
        approval_run_id=phase_report.approval_run_id,
        approval_request_ids=phase_report.approval_request_ids,
        issues=phase_report.issues,
        next_action=next_action,
    )


def _approval_items(request: WorkflowRunRequest, *, approval_run_id: str) -> tuple[_ApprovalItem, ...]:
    package = Path(request.package_path).expanduser().resolve()
    store = WorkflowRunStore(request.run_root or (package / ".workflow-runs"))
    decisions = _latest_decisions_by_request_id(store.read_approval_decisions(approval_run_id))
    return tuple(
        _ApprovalItem(
            approval_request_id=approval.approval_request_id,
            status=decisions.get(approval.approval_request_id, approval.status),
            connector_action=approval.connector_action,
        )
        for approval in store.read_approval_requests(approval_run_id)
    )


def _latest_decisions_by_request_id(decisions: tuple[dict[str, Any], ...]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for decision in decisions:
        request_id = decision.get("approval_request_id")
        status = decision.get("status")
        if isinstance(request_id, str) and isinstance(status, str):
            latest[request_id] = status
    return latest


def _allowlist_from_command(commands: tuple[str, ...], command_name: str) -> tuple[str, ...]:
    prefix = f"{command_name} "
    for command in commands:
        if not command.startswith(prefix) or " --allow " not in command:
            continue
        _, allow = command.rsplit(" --allow ", 1)
        return tuple(action.strip() for action in allow.split(",") if action.strip())
    return ()


def _production_actions_for_approvals(items: tuple[_ApprovalItem, ...]) -> tuple[str, ...]:
    contracts = builtin_connector_contract_catalog()
    actions: list[str] = []
    for item in items:
        if item.status != "approved":
            continue
        contract = contracts.get(item.connector_action)
        if contract is None or ConnectorLiveMode.LIVE_PRODUCTION not in contract.live_modes:
            continue
        if item.connector_action not in actions:
            actions.append(item.connector_action)
    return tuple(actions)


def _inspect_readiness(request: WorkflowRunRequest, env: Mapping[str, str]):
    return inspect_workflow_live_readiness(request, env=env)


def _readiness_status(readiness: object) -> str:
    if isinstance(readiness, str):
        return readiness
    return str(getattr(readiness, "status", "blocked"))


def _readiness_issues(readiness: object) -> tuple[str, ...]:
    if isinstance(readiness, str):
        return ()
    return tuple(str(issue) for issue in getattr(readiness, "issues", ()))
