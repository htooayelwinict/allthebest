"""Multi-phase workflow runner orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.approval_gate import ApprovalRequest
from appv24.workflows.broker import BrokerRequest, LocalWorkflowBroker
from appv24.workflows.connector_contracts import ConnectorLiveMode, builtin_connector_contract_catalog
from appv24.workflows.credential_refs import CredentialRefResolver
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunReport, WorkflowRunRequest
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_runner import run_live_draft_workflow, run_live_production_workflow, run_live_read_workflow
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import DEFAULT_FIXTURE_NAME, run_workflow_from_handoff


@dataclass(frozen=True)
class WorkflowPhaseRunReport:
    status: str
    live_read_run_id: str
    local_run_id: str
    approval_run_id: str | None
    phase_statuses: tuple[str, ...]
    next_commands: tuple[str, ...]
    approval_request_ids: tuple[str, ...] = ()
    live_draft_run_id: str = ""
    live_draft_report: WorkflowRunReport | None = None
    live_production_run_id: str = ""
    live_production_report: WorkflowRunReport | None = None
    issues: tuple[str, ...] = ()


def format_workflow_phase_run_report(report: WorkflowPhaseRunReport, *, title: str = "Workflow phase run") -> str:
    lines = [f"{title}: {report.status}"]
    if report.live_read_run_id:
        lines.append(f"live-read run: {report.live_read_run_id}")
    if report.local_run_id:
        lines.append(f"local run: {report.local_run_id}")
    if report.live_draft_run_id:
        lines.append(f"live-draft run: {report.live_draft_run_id}")
    if report.live_production_run_id:
        lines.append(f"live-production run: {report.live_production_run_id}")
    if report.approval_run_id:
        lines.append(f"approval run: {report.approval_run_id}")
    if report.approval_request_ids:
        lines.append("approval requests:")
        lines.extend(f"- {request_id}" for request_id in report.approval_request_ids)
    if report.phase_statuses:
        lines.append("phases:")
        lines.extend(f"- {phase}" for phase in report.phase_statuses)
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    if report.next_commands:
        lines.append("next commands:")
        lines.extend(f"- {command}" for command in report.next_commands)
    return "\n".join(lines)


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
            operator_env=request.operator_env,
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
        broker_wrapper=lambda fallback: _LiveReadResumeBroker(
            fallback,
            live_read_broker=live_read_broker,
            connector_allowlist=set(request.connector_allowlist),
        ),
    )
    approval_required = _approval_required(local_report.issues)
    status = "approval-required" if approval_required else local_report.status
    approval_run_id = local_report.run_id if approval_required else None
    approval_requests = _approval_requests(request, approval_run_id=approval_run_id)
    approval_request_ids = tuple(approval.approval_request_id for approval in approval_requests)
    return WorkflowPhaseRunReport(
        status=status,
        live_read_run_id=live_report.run_id,
        local_run_id=local_report.run_id,
        approval_run_id=approval_run_id,
        approval_request_ids=approval_request_ids,
        phase_statuses=(
            f"live-read: {live_report.status}",
            f"local-resume: {local_report.status}",
        ),
        next_commands=_next_commands(
            request,
            approval_run_id=approval_run_id,
            approval_request_ids=approval_request_ids,
            approval_requests=approval_requests,
        ),
        issues=local_report.issues,
    )


class _LiveReadResumeBroker(LocalWorkflowBroker):
    """Local-resume broker that delegates connector-owned live-read actions."""

    def __init__(
        self,
        fallback: LocalWorkflowBroker,
        *,
        live_read_broker: LiveWorkflowBroker,
        connector_allowlist: set[str],
    ) -> None:
        super().__init__({})
        self._fallback = fallback
        self._live_read_broker = live_read_broker
        self._connector_allowlist = set(connector_allowlist)
        self._contracts = builtin_connector_contract_catalog()

    def invoke(self, request: BrokerRequest):
        if self._should_delegate(request.connector_action):
            return self._live_read_broker.invoke(
                BrokerRequest(
                    step_id=request.step_id,
                    connector_action=request.connector_action,
                    input_ref=request.input_ref,
                    fixture=request.fixture,
                    allowed_outputs=request.allowed_outputs,
                    dry_run=False,
                )
            )
        return self._fallback.invoke(request)

    def _should_delegate(self, action_ref: str) -> bool:
        if self._live_read_broker.mode != "live-read":
            return False
        if action_ref not in self._connector_allowlist:
            return False
        if action_ref not in self._live_read_broker.configured_actions:
            return False
        contract = self._contracts.get(action_ref)
        return contract is not None and ConnectorLiveMode.LIVE_READ in contract.live_modes


def run_workflow_approved_live_draft(
    request: WorkflowRunRequest,
    *,
    approval_run_id: str,
    live_draft_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
) -> WorkflowPhaseRunReport:
    """Run an approved workflow package through the live-draft phase."""

    live_report = run_live_draft_workflow(
        WorkflowRunRequest(
            package_path=request.package_path,
            fixture_name=request.fixture_name,
            mode=WorkflowRunMode.LIVE_DRAFT,
            run_root=request.run_root,
            approval_run_id=approval_run_id,
            connector_allowlist=request.connector_allowlist,
            activation_id=request.activation_id,
            activation_trigger_type=request.activation_trigger_type,
            operator_env=request.operator_env,
        ),
        broker=live_draft_broker,
        credential_resolver=credential_resolver,
    )
    return WorkflowPhaseRunReport(
        status=live_report.status,
        live_read_run_id="",
        local_run_id="",
        approval_run_id=approval_run_id,
        approval_request_ids=(),
        phase_statuses=(f"live-draft: {live_report.status}",),
        next_commands=(),
        live_draft_run_id=live_report.run_id,
        live_draft_report=live_report,
        issues=live_report.issues,
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
            operator_env=request.operator_env,
        ),
        broker=live_production_broker,
        credential_resolver=credential_resolver,
    )
    return WorkflowPhaseRunReport(
        status=live_report.status,
        live_read_run_id="",
        local_run_id="",
        approval_run_id=approval_run_id,
        approval_request_ids=(),
        phase_statuses=(f"live-production: {live_report.status}",),
        next_commands=(),
        live_production_run_id=live_report.run_id,
        live_production_report=live_report,
        issues=live_report.issues,
    )


def _approval_required(issues: tuple[str, ...]) -> bool:
    return any(issue.startswith("approval required for ") for issue in issues)


def _approval_requests(request: WorkflowRunRequest, *, approval_run_id: str | None) -> tuple[ApprovalRequest, ...]:
    if approval_run_id is None:
        return ()
    package = Path(request.package_path).expanduser().resolve()
    store = WorkflowRunStore(request.run_root or (package / ".workflow-runs"))
    return store.read_approval_requests(approval_run_id)


def _next_commands(
    request: WorkflowRunRequest,
    *,
    approval_run_id: str | None,
    approval_request_ids: tuple[str, ...] = (),
    approval_requests: tuple[ApprovalRequest, ...] = (),
) -> tuple[str, ...]:
    if approval_run_id is None:
        return ()
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    commands = [f"/workflow-approvals {package} {approval_run_id}"]
    commands.extend(f"/workflow-approve {package} {approval_run_id} {request_id}" for request_id in approval_request_ids)
    for mode, actions in _approval_actions_by_live_mode(approval_requests):
        allowlist = ",".join(actions)
        commands.append(f"/workflow-live-ready {mode} {package} {fixture_name} {approval_run_id} --allow {allowlist}")
        commands.append(f"/workflow-run-{mode} {package} {fixture_name} {approval_run_id} --allow {allowlist}")
    commands.append(f"/workflow-live-plan {package} {fixture_name}")
    return tuple(commands)


def _approval_actions_by_live_mode(approval_requests: tuple[ApprovalRequest, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    contracts = builtin_connector_contract_catalog()
    actions_by_mode: dict[str, list[str]] = {"live-draft": [], "live-production": []}
    for request in approval_requests:
        contract = contracts.get(request.connector_action)
        if contract is None:
            continue
        if ConnectorLiveMode.LIVE_DRAFT in contract.live_modes:
            _append_unique(actions_by_mode["live-draft"], request.connector_action)
        if ConnectorLiveMode.LIVE_PRODUCTION in contract.live_modes:
            _append_unique(actions_by_mode["live-production"], request.connector_action)
    return tuple((mode, tuple(actions)) for mode, actions in actions_by_mode.items() if actions)


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
