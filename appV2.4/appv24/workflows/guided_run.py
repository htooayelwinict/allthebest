"""Guided user-mode orchestration over existing workflow live phases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.connector_contracts import ConnectorContract, ConnectorLiveMode, builtin_connector_contract_catalog, workflow_operator_env_from_env
from appv24.workflows.credential_refs import CredentialRefResolver
from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.extension_handoff import (
    WorkflowExtensionHandoff,
    apply_returned_extension_handoff_results,
    format_workflow_extension_returned_qa_apply_report,
    format_workflow_extension_handoff,
    write_extension_handoff_for_validation_issues,
)
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.loader import load_package_documents
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_plan import build_workflow_live_plan_from_path
from appv24.workflows.live_readiness import inspect_workflow_live_readiness
from appv24.workflows.middleware_runtime import MiddlewareExecutorFactory
from appv24.workflows.phase_runner import WorkflowPhaseRunReport, run_workflow_approved_live_production, run_workflow_live_read_to_approval
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.validator import validate_workflow_documents
from appv24.workflows.workflow_extensions import extension_connector_contract_catalog


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
    extension_prd_prompts: tuple[str, ...] = ()
    extension_handoff_path: Path | None = None
    extension_handoff_report: str = ""
    extension_qa_apply_report: str = ""


ReadinessInspector = Callable[[WorkflowRunRequest, Mapping[str, str]], object]


@dataclass(frozen=True)
class _ApprovalItem:
    approval_request_id: str
    status: str
    connector_action: str


@dataclass(frozen=True)
class _GuidedApprovalSelection:
    approval_run_id: str
    approval_request_id: str


def prepare_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    live_read_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    local_broker: LocalWorkflowBroker | None = None,
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> GuidedWorkflowRunReport:
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or "happy_path.yaml"
    plan = build_workflow_live_plan_from_path(package, fixture_name)
    if plan.status == "blocked":
        extension_handoff = _write_extension_handoff_for_package(package)
        extension_prd_prompts = extension_handoff.prompts if extension_handoff is not None else ()
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            live_plan_status=plan.status,
            issues=plan.issues,
            next_action=(
                "build extension with coding profile and attach QA manifest"
                if extension_prd_prompts
                else "fix workflow package"
            ),
            extension_prd_prompts=extension_prd_prompts,
            extension_handoff_path=extension_handoff.manifest_path if extension_handoff is not None else None,
            extension_handoff_report=(
                format_workflow_extension_handoff(extension_handoff) if extension_handoff is not None else ""
            ),
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
        middleware_factories=middleware_factories,
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
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
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

    documents = load_package_documents(package)
    production_actions = _production_actions_for_approvals(
        approvals,
        contract_catalog=_connector_contracts_for_documents(documents),
    )
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
        middleware_factories=middleware_factories,
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


def approve_and_continue_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    approval_run_id: str,
    approval_request_id: str,
    live_production_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    env: Mapping[str, str],
    approved: bool = True,
    decided_by: str = "operator",
    readiness_inspector: ReadinessInspector | None = None,
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> GuidedWorkflowRunReport:
    package = Path(request.package_path).expanduser().resolve()
    documents = load_package_documents(package)
    package_fingerprint = fingerprint_package_documents(documents)
    store = WorkflowRunStore(request.run_root or (package / ".workflow-runs"))
    store.record_approval_decision(
        approval_run_id,
        approval_request_id,
        ApprovalDecision(
            approved=approved,
            package_fingerprint=package_fingerprint,
            decided_by=decided_by,
        ),
    )
    return continue_guided_workflow_run(
        request,
        approval_run_id=approval_run_id,
        live_production_broker=live_production_broker,
        credential_resolver=credential_resolver,
        env=env,
        readiness_inspector=readiness_inspector,
        middleware_factories=middleware_factories,
    )


def approve_latest_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    live_production_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    env: Mapping[str, str],
    approved: bool = True,
    decided_by: str = "operator",
    readiness_inspector: ReadinessInspector | None = None,
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> GuidedWorkflowRunReport:
    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or "happy_path.yaml"
    documents = load_package_documents(package)
    package_fingerprint = fingerprint_package_documents(documents)
    store = WorkflowRunStore(request.run_root or (package / ".workflow-runs"))
    selection, issues = _latest_pending_guided_approval(
        store,
        package_fingerprint=package_fingerprint,
    )
    if selection is None:
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            issues=issues or ("no pending guided approval found",),
            next_action="run guided workflow to approval",
        )
    return approve_and_continue_guided_workflow_run(
        request,
        approval_run_id=selection.approval_run_id,
        approval_request_id=selection.approval_request_id,
        live_production_broker=live_production_broker,
        credential_resolver=credential_resolver,
        env=env,
        approved=approved,
        decided_by=decided_by,
        readiness_inspector=readiness_inspector,
        middleware_factories=middleware_factories,
    )


def approve_or_prepare_guided_workflow_run(
    request: WorkflowRunRequest,
    *,
    live_read_broker: LiveWorkflowBroker,
    live_production_broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    env: Mapping[str, str],
    approved: bool = True,
    decided_by: str = "operator",
    readiness_inspector: ReadinessInspector | None = None,
    local_broker: LocalWorkflowBroker | None = None,
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> GuidedWorkflowRunReport:
    """Approve the latest guided request, or prepare one when none exists."""

    package = Path(request.package_path).expanduser().resolve()
    fixture_name = request.fixture_name or "happy_path.yaml"
    qa_apply_report = apply_returned_extension_handoff_results(package) if approved else None
    qa_apply_text = (
        format_workflow_extension_returned_qa_apply_report(qa_apply_report)
        if qa_apply_report is not None and qa_apply_report.status != "skipped"
        else ""
    )
    if qa_apply_report is not None and qa_apply_report.status == "blocked":
        return GuidedWorkflowRunReport(
            status="blocked",
            package_path=package,
            fixture_name=fixture_name,
            issues=qa_apply_report.issues,
            next_action="fix returned extension QA",
            extension_qa_apply_report=qa_apply_text,
        )

    documents = load_package_documents(package)
    package_fingerprint = fingerprint_package_documents(documents)
    store = WorkflowRunStore(request.run_root or (package / ".workflow-runs"))
    selection, issues = _latest_pending_guided_approval(
        store,
        package_fingerprint=package_fingerprint,
    )
    if selection is None:
        if not approved:
            return GuidedWorkflowRunReport(
                status="blocked",
                package_path=package,
                fixture_name=fixture_name,
                issues=issues or ("no pending guided approval found",),
                next_action="run guided workflow to approval",
            )
        return _with_extension_qa_apply_report(
            prepare_guided_workflow_run(
                WorkflowRunRequest(
                    package_path=package,
                    fixture_name=fixture_name,
                    mode=WorkflowRunMode.LIVE_READ,
                    run_root=request.run_root,
                    connector_allowlist=request.connector_allowlist,
                    activation_id=request.activation_id,
                    activation_trigger_type=request.activation_trigger_type,
                    operator_env=request.operator_env,
                ),
                live_read_broker=live_read_broker,
                credential_resolver=credential_resolver,
                local_broker=local_broker,
                middleware_factories=middleware_factories,
            ),
            qa_apply_text,
        )
    return _with_extension_qa_apply_report(
        approve_and_continue_guided_workflow_run(
            WorkflowRunRequest(
                package_path=package,
                fixture_name=fixture_name,
                mode=request.mode,
                run_root=request.run_root,
                connector_allowlist=request.connector_allowlist,
                activation_id=request.activation_id,
                activation_trigger_type=request.activation_trigger_type,
                operator_env=request.operator_env,
            ),
            approval_run_id=selection.approval_run_id,
            approval_request_id=selection.approval_request_id,
            live_production_broker=live_production_broker,
            credential_resolver=credential_resolver,
            env=env,
            approved=approved,
            decided_by=decided_by,
            readiness_inspector=readiness_inspector,
            middleware_factories=middleware_factories,
        ),
        qa_apply_text,
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
    if report.extension_qa_apply_report:
        lines.append(report.extension_qa_apply_report)
    if report.extension_prd_prompts:
        if report.extension_handoff_path is not None:
            lines.append(f"extension handoff: {report.extension_handoff_path}")
        if report.extension_handoff_report:
            lines.append(report.extension_handoff_report)
        lines.append("extension PRD prompts:")
        for index, prompt in enumerate(report.extension_prd_prompts, start=1):
            lines.append(f"--- extension PRD prompt {index} ---")
            lines.append(prompt)
    if report.next_action:
        lines.append(f"next action: {report.next_action}")
    next_commands = _guided_next_commands(report)
    if next_commands:
        lines.append("next commands:")
        lines.extend(f"- {command}" for command in next_commands)
    return "\n".join(lines)


def _with_extension_qa_apply_report(report: GuidedWorkflowRunReport, qa_apply_text: str) -> GuidedWorkflowRunReport:
    if not qa_apply_text:
        return report
    return replace(report, extension_qa_apply_report=qa_apply_text)


def _guided_next_commands(report: GuidedWorkflowRunReport) -> tuple[str, ...]:
    if report.status == "approval-required":
        return (f"/workflow-approve {report.package_path} {report.fixture_name}",)
    return ()


def _write_extension_handoff_for_package(package: Path) -> WorkflowExtensionHandoff | None:
    try:
        documents = load_package_documents(package)
    except WorkflowPackageError:
        return None
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)
    return handoff if handoff.status == "ready" else None


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


def _latest_pending_guided_approval(
    store: WorkflowRunStore,
    *,
    package_fingerprint: str,
) -> tuple[_GuidedApprovalSelection | None, tuple[str, ...]]:
    if not store.root.is_dir():
        return None, ("no workflow runs found for this package",)
    for run_id in _run_ids_newest_first(store.root):
        try:
            manifest = store.read_run_manifest(run_id)
        except ValueError:
            continue
        if manifest.get("package_fingerprint") != package_fingerprint:
            continue
        approvals = store.read_approval_requests(run_id)
        if not approvals:
            continue
        decisions = _latest_decisions_by_request_id(store.read_approval_decisions(run_id))
        pending = tuple(
            approval
            for approval in approvals
            if decisions.get(approval.approval_request_id, approval.status) == "pending"
        )
        if len(pending) == 1:
            return (
                _GuidedApprovalSelection(
                    approval_run_id=run_id,
                    approval_request_id=pending[0].approval_request_id,
                ),
                (),
            )
        if len(pending) > 1:
            return (
                None,
                (
                    f"multiple pending approvals found in {run_id}; "
                    "use the explicit approval command with run id and request id",
                ),
            )
    return None, ("no pending guided approval found for this package fingerprint",)


def _run_ids_newest_first(root: Path) -> tuple[str, ...]:
    run_dirs = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "run.json"
        if not manifest_path.is_file():
            continue
        run_dirs.append((manifest_path.stat().st_mtime_ns, child.name))
    return tuple(run_id for _, run_id in sorted(run_dirs, reverse=True))


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


def _production_actions_for_approvals(
    items: tuple[_ApprovalItem, ...],
    *,
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> tuple[str, ...]:
    contracts = contract_catalog or builtin_connector_contract_catalog()
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


def _connector_contracts_for_documents(documents) -> dict[str, ConnectorContract]:
    contracts = builtin_connector_contract_catalog()
    contracts.update(extension_connector_contract_catalog(documents, contract_catalog=contracts))
    return contracts


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
