"""Local workflow runner for validated appv24 workflow packages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Any, Mapping

from appv24.workflows.approval_gate import ApprovalDecision, ApprovalRequest, create_approval_request, resume_approval
from appv24.workflows.broker import BrokerRequest, LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_sandbox import ConnectorSandboxProviders
from appv24.workflows.execution import WorkflowRunEvent, WorkflowRunMode, WorkflowRunReport, WorkflowRunRequest
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.idempotency import build_idempotency_key, build_idempotency_payload_hash
from appv24.workflows.ir import ActionCapability, StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.ledger import StepTrace
from appv24.workflows.loader import load_package_documents
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS, evaluate_policy
from appv24.workflows.run_preview import WorkflowApprovalPreview, WorkflowBrokeredActionPreview
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.validator import validate_workflow_documents


DEFAULT_FIXTURE_NAME = "happy_path.yaml"
MAX_TRANSITIONS = 200
SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9_])(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")


def run_workflow(
    request: WorkflowRunRequest,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowRunReport:
    """Run a workflow package in local artifact mode.

    Live modes intentionally remain blocked here. External side effects belong behind
    explicit broker implementations and approval-ledger wiring, not this local runner.
    """

    if request.mode is not WorkflowRunMode.LOCAL:
        return _blocked_report(
            request,
            workflow_name="",
            package_fingerprint="",
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(request.package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in policy.issues),
        )

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (Path(request.package_path) / ".workflow-runs"))
    run_id = store.create_run(
        workflow_name,
        package_fingerprint,
        metadata=build_workflow_run_metadata(request, package_path=documents.root, fixture_name=fixture_name),
    )
    events: list[WorkflowRunEvent] = []
    event_counter = _EventCounter()

    def append_event(event_type: str, status: str, **kwargs: Any) -> None:
        event = WorkflowRunEvent(
            event_id=event_counter.next(),
            run_id=run_id,
            event_type=event_type,
            package_fingerprint=package_fingerprint,
            step_id=kwargs.pop("step_id", None),
            connector_action=kwargs.pop("connector_action", None),
            status=status,
            details=kwargs,
        )
        store.append_event(event)
        events.append(event)

    append_event(
        "run_created",
        "started",
        fixture_name=fixture_name,
        mode=request.mode.value,
        run_dir=str(store.run_dir(run_id)),
    )

    local_broker = broker
    if local_broker is None:
        providers = ConnectorSandboxProviders(store.artifact_root(run_id))
        local_broker = LocalWorkflowBroker(providers.provider_map())

    result = _execute_local(
        compiled.ir,
        fixture,
        broker=local_broker,
        run_id=run_id,
        run_dir=store.run_dir(run_id),
        package_fingerprint=package_fingerprint,
        approve_all=request.approve_all,
        append_event=append_event,
    )
    append_event("run_completed", result.status, terminal_step=result.terminal_step, issues=list(result.issues))

    report = WorkflowRunReport(
        run_id=run_id,
        workflow_name=workflow_name,
        mode=request.mode,
        status=result.status,
        package_fingerprint=package_fingerprint,
        terminal_step=result.terminal_step,
        step_trace=tuple(result.trace),
        brokered_actions=tuple(result.brokered_actions),
        approvals=tuple(result.approvals),
        events=tuple(events),
        issues=tuple(result.issues),
    )
    store.write_trace(run_id, report.step_trace)
    store.write_approvals(run_id, report.approvals, approval_requests=tuple(result.approval_requests))
    store.write_report(run_id, report.to_dict())
    return report


def run_workflow_from_handoff(
    request: WorkflowRunRequest,
    *,
    handoff_run_id: str,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowRunReport:
    """Resume local workflow execution from a paused live-read handoff."""

    if request.mode is not WorkflowRunMode.LOCAL:
        return _blocked_report(
            request,
            workflow_name="",
            package_fingerprint="",
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(request.package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in policy.issues),
        )

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (Path(request.package_path) / ".workflow-runs"))
    handoff, handoff_issues = _load_handoff(store, handoff_run_id, package_fingerprint=package_fingerprint)
    if handoff_issues:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=handoff_issues,
        )
    metadata = build_workflow_run_metadata(request, package_path=documents.root, fixture_name=fixture_name)
    metadata.update(
        {
            "handoff_run_id": handoff_run_id,
            "handoff_from_mode": handoff["from_mode"],
            "handoff_next_step": handoff["next_step"],
        }
    )
    run_id = store.create_run(workflow_name, package_fingerprint, metadata=metadata)
    events: list[WorkflowRunEvent] = []
    event_counter = _EventCounter()

    def append_event(event_type: str, status: str, **kwargs: Any) -> None:
        event = WorkflowRunEvent(
            event_id=event_counter.next(),
            run_id=run_id,
            event_type=event_type,
            package_fingerprint=package_fingerprint,
            step_id=kwargs.pop("step_id", None),
            connector_action=kwargs.pop("connector_action", None),
            status=status,
            details=kwargs,
        )
        store.append_event(event)
        events.append(event)

    append_event(
        "run_created",
        "started",
        fixture_name=fixture_name,
        mode=request.mode.value,
        run_dir=str(store.run_dir(run_id)),
        handoff_run_id=handoff_run_id,
        handoff_next_step=handoff["next_step"],
    )
    append_event(
        "handoff_loaded",
        "ready",
        handoff_run_id=handoff_run_id,
        handoff_from_mode=handoff["from_mode"],
        handoff_next_step=handoff["next_step"],
    )

    local_broker = broker
    if local_broker is None:
        providers = ConnectorSandboxProviders(store.artifact_root(run_id))
        local_broker = LocalWorkflowBroker(providers.provider_map())

    result = _execute_local(
        compiled.ir,
        _fixture_with_handoff(fixture, handoff),
        broker=local_broker,
        run_id=run_id,
        run_dir=store.run_dir(run_id),
        package_fingerprint=package_fingerprint,
        approve_all=request.approve_all,
        append_event=append_event,
        start_step=str(handoff["next_step"]),
    )
    append_event("run_completed", result.status, terminal_step=result.terminal_step, issues=list(result.issues))

    report = WorkflowRunReport(
        run_id=run_id,
        workflow_name=workflow_name,
        mode=request.mode,
        status=result.status,
        package_fingerprint=package_fingerprint,
        terminal_step=result.terminal_step,
        step_trace=tuple(result.trace),
        brokered_actions=tuple(result.brokered_actions),
        approvals=tuple(result.approvals),
        events=tuple(events),
        issues=tuple(result.issues),
    )
    store.write_trace(run_id, report.step_trace)
    store.write_approvals(run_id, report.approvals, approval_requests=tuple(result.approval_requests))
    store.write_report(run_id, report.to_dict())
    return report


def format_workflow_run_report(report: WorkflowRunReport) -> str:
    lines = [f"Workflow local run: {report.status}"]
    if report.run_id:
        lines.append(f"run id: {report.run_id}")
    if report.terminal_step:
        lines.append(f"terminal: {report.terminal_step}")
    if report.brokered_actions:
        lines.append(f"brokered actions: {len(report.brokered_actions)}")
        for action in report.brokered_actions:
            status = action.broker_status or "planned"
            lines.append(f"- {action.step_id}: {action.connector_action} -> {status}")
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)


def build_workflow_run_metadata(
    request: WorkflowRunRequest,
    *,
    package_path: Path,
    fixture_name: str,
) -> dict[str, Any]:
    return {
        "mode": request.mode.value,
        "fixture_name": fixture_name,
        "package_path": str(package_path),
        "approval_run_id": request.approval_run_id,
        "approve_all": request.approve_all,
        "approval_mode": "auto-approved" if request.approve_all else "explicit",
        "connector_allowlist": list(request.connector_allowlist),
        "activation_id": request.activation_id,
        "activation_trigger_type": request.activation_trigger_type,
        "operator_env_names": sorted(str(key) for key in request.operator_env),
    }


class _EventCounter:
    def __init__(self) -> None:
        self._value = 0

    def next(self) -> str:
        self._value += 1
        return f"event-{self._value}"


class _ExecutionResult:
    def __init__(
        self,
        *,
        status: str,
        terminal_step: str | None,
        trace: list[StepTrace],
        brokered_actions: list[WorkflowBrokeredActionPreview],
        approvals: list[WorkflowApprovalPreview],
        approval_requests: list[ApprovalRequest],
        issues: list[str],
    ) -> None:
        self.status = status
        self.terminal_step = terminal_step
        self.trace = trace
        self.brokered_actions = brokered_actions
        self.approvals = approvals
        self.approval_requests = approval_requests
        self.issues = issues


def _execute_local(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    *,
    broker: LocalWorkflowBroker,
    run_id: str,
    run_dir: Path,
    package_fingerprint: str,
    approve_all: bool,
    append_event: Callable[..., None],
    start_step: str | None = None,
) -> _ExecutionResult:
    trace: list[StepTrace] = []
    brokered_actions: list[WorkflowBrokeredActionPreview] = []
    approvals: list[WorkflowApprovalPreview] = []
    approval_requests: list[ApprovalRequest] = []
    issues: list[str] = []
    current = start_step or ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append("transition budget exceeded")
            return _result("blocked", None, trace, brokered_actions, approvals, approval_requests, issues)

        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            return _result("blocked", None, trace, brokered_actions, approvals, approval_requests, issues)

        append_event("step_started", "started", step_id=step.step_id, connector_action=_connector_action(step))
        if step.kind is StepKind.TERMINAL:
            trace.append(_trace(step, "terminal"))
            append_event("step_completed", "terminal", step_id=step.step_id)
            return _result("passed", step.step_id, trace, brokered_actions, approvals, approval_requests, issues)

        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                issues.append(f"no branch matched for {step.step_id}")
                trace.append(_trace(step, "blocked", selected_branch=selected_branch, issues=("no branch matched",)))
                append_event("step_blocked", "blocked", step_id=step.step_id, selected_branch=selected_branch)
                return _result("blocked", None, trace, brokered_actions, approvals, approval_requests, issues)
            trace.append(_trace(step, "completed", selected_branch=selected_branch))
            append_event("step_completed", "completed", step_id=step.step_id, selected_branch=selected_branch)
            current = next_step
            continue

        action = _action_for_step(ir, step)
        idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
        approval_required = _approval_required(step, action)
        approval_id = step.approval
        if approval_required and approval_id is None:
            issue = f"approval id missing for {step.step_id}"
            issues.append(issue)
            trace.append(_trace(step, "blocked", issues=(issue,)))
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issue=issue,
            )
            return _result("blocked", None, trace, brokered_actions, approvals, approval_requests, issues)
        if approval_required and approval_id is not None:
            approval_request = create_approval_request(
                run_id=run_id,
                package_fingerprint=package_fingerprint,
                step_id=step.step_id,
                connector_action=_connector_action(step) or "",
                approval_id=approval_id,
                allowed_outputs=action.allowed_outputs if action is not None else (),
                idempotency_key_fields=action.idempotency_key_fields if action is not None else (),
                idempotency_key=idempotency_key,
                payload_hash=build_idempotency_payload_hash(input_ref=step.raw, fixture=fixture),
            )
            approval_request = replace(
                approval_request,
                payload_preview_path=_write_approval_payload_preview(
                    run_dir,
                    approval_request,
                    step,
                    fixture,
                ),
            )
            approvals.append(
                WorkflowApprovalPreview(
                    approval_id=approval_id,
                    step_ids=(step.step_id,),
                    mode=ir.approvals[approval_id].mode if approval_id in ir.approvals else "explicit",
                    status="approved" if approve_all else "pending",
                )
            )
            append_event(
                "approval_requested",
                "requested",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                approval_id=approval_id,
                approval_request_id=approval_request.approval_request_id,
                idempotency_key=idempotency_key,
                payload_hash=approval_request.payload_hash,
                payload_preview_path=approval_request.payload_preview_path,
            )
            if not approve_all:
                approval_requests.append(approval_request)
                issue = f"approval required for {step.step_id}"
                issues.append(issue)
                trace.append(_trace(step, "blocked", issues=(issue,)))
                append_event(
                    "step_blocked",
                    "blocked",
                    step_id=step.step_id,
                    connector_action=_connector_action(step),
                    approval_id=approval_id,
                    approval_request_id=approval_request.approval_request_id,
                    issue=issue,
                )
                return _result("blocked", None, trace, brokered_actions, approvals, approval_requests, issues)
            approval_request = resume_approval(
                approval_request,
                ApprovalDecision(approved=True, package_fingerprint=package_fingerprint, decided_by="local-runner"),
            )
            approval_requests.append(approval_request)
            append_event(
                "approval_granted",
                "approved",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                approval_id=approval_id,
                approval_request_id=approval_request.approval_request_id,
                idempotency_key=idempotency_key,
            )

        outputs, broker_status, broker_provider_id, broker_issues = _invoke_broker(
            ir,
            step,
            fixture,
            broker,
            append_event,
            idempotency_key=idempotency_key,
        )
        if action is not None and step.uses is not None:
            brokered_actions.append(
                WorkflowBrokeredActionPreview(
                    step_id=step.step_id,
                    connector_action=step.uses.dotted,
                    approval_id=approval_id,
                    side_effect_class=step.side_effect_class,
                    allowed_outputs=action.allowed_outputs,
                    idempotency_key_fields=action.idempotency_key_fields,
                    idempotency_key=idempotency_key,
                    broker_status=broker_status,
                    broker_provider_id=broker_provider_id,
                    simulated_outputs=dict(outputs),
                )
            )
        if broker_issues:
            issues.extend(broker_issues)
            trace.append(_trace(step, "blocked", outputs=outputs, issues=broker_issues, broker_status=broker_status, broker_provider_id=broker_provider_id))
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issues=list(broker_issues),
            )
            return _result("blocked", None, trace, brokered_actions, approvals, approval_requests, issues)

        trace.append(_trace(step, "completed", outputs=outputs, broker_status=broker_status, broker_provider_id=broker_provider_id))
        append_event(
            "step_completed",
            "completed",
            step_id=step.step_id,
            connector_action=_connector_action(step),
            outputs=dict(outputs),
        )
        current = step.next_step

    issues.append("workflow stopped without terminal step")
    return _result("blocked", None, trace, brokered_actions, approvals, approval_requests, issues)


def _result(
    status: str,
    terminal_step: str | None,
    trace: list[StepTrace],
    brokered_actions: list[WorkflowBrokeredActionPreview],
    approvals: list[WorkflowApprovalPreview],
    approval_requests: list[ApprovalRequest],
    issues: list[str],
) -> _ExecutionResult:
    return _ExecutionResult(
        status=status,
        terminal_step=terminal_step,
        trace=trace,
        brokered_actions=brokered_actions,
        approvals=approvals,
        approval_requests=approval_requests,
        issues=issues,
    )


def _write_approval_payload_preview(
    run_dir: Path,
    request: ApprovalRequest,
    step: WorkflowStep,
    fixture: Mapping[str, Any],
) -> str:
    relative_path = Path("artifacts") / "approvals" / f"{request.approval_request_id}.json"
    preview_path = run_dir / relative_path
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "appv24.approval-payload-preview/v1",
        "approval_request_id": request.approval_request_id,
        "run_id": request.run_id,
        "package_fingerprint": request.package_fingerprint,
        "step_id": request.step_id,
        "connector_action": request.connector_action,
        "approval_id": request.approval_id,
        "allowed_outputs": list(request.allowed_outputs),
        "idempotency_key_fields": list(request.idempotency_key_fields),
        "idempotency_key": request.idempotency_key,
        "payload_hash": request.payload_hash,
        "payload_shape": _approval_payload_shape(step, fixture),
    }
    encoded = json.dumps(payload, sort_keys=True, indent=2)
    if SECRET_VALUE_RE.search(encoded):
        raise ValueError("workflow approval payload preview contains a raw secret-looking value")
    preview_path.write_text(encoded + "\n", encoding="utf-8")
    return relative_path.as_posix()


def _approval_payload_shape(step: WorkflowStep, fixture: Mapping[str, Any]) -> dict[str, object]:
    with_values = step.raw.get("with")
    with_map = with_values if isinstance(with_values, Mapping) else {}
    fixture_input = fixture.get("input")
    return {
        "step_with_fields": sorted(str(key) for key in with_map),
        "input_refs": _input_refs(with_map),
        "fixture_input_fields": sorted(str(key) for key in fixture_input) if isinstance(fixture_input, Mapping) else [],
    }


def _input_refs(with_map: Mapping[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for key, value in with_map.items():
        if isinstance(value, Mapping) and isinstance(value.get("inputRef"), str):
            refs[str(key)] = str(value["inputRef"])
    return refs


def _load_handoff(
    store: WorkflowRunStore,
    handoff_run_id: str,
    *,
    package_fingerprint: str,
) -> tuple[dict[str, object], tuple[str, ...]]:
    try:
        run_dir = store.run_dir(handoff_run_id)
        handoff = _read_json(run_dir / "handoff.json")
        trace_payload = _read_json(run_dir / "trace.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {}, (f"failed to load workflow handoff: {error}",)

    if handoff.get("version") != "appv24.workflow-handoff/v1":
        return {}, ("workflow handoff has unsupported version",)
    if handoff.get("status") != "ready":
        return {}, ("workflow handoff is not ready",)
    if handoff.get("package_fingerprint") != package_fingerprint:
        return {}, ("workflow handoff package fingerprint does not match current package",)
    next_step = handoff.get("next_step")
    if not isinstance(next_step, str) or not next_step:
        return {}, ("workflow handoff is missing next_step",)
    from_mode = handoff.get("from_mode")
    if not isinstance(from_mode, str) or not from_mode:
        return {}, ("workflow handoff is missing from_mode",)

    return {
        "run_id": handoff_run_id,
        "from_mode": from_mode,
        "next_step": next_step,
        "outputs": _handoff_trace_outputs(trace_payload),
        "available_outputs": handoff.get("available_outputs") if isinstance(handoff.get("available_outputs"), list) else [],
    }, ()


def _read_json(path: Path) -> Mapping[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, Mapping) else {}


def _handoff_trace_outputs(trace_payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    trace = trace_payload.get("trace")
    if not isinstance(trace, list):
        return outputs
    for item in trace:
        if not isinstance(item, Mapping):
            continue
        step_id = item.get("step_id")
        simulated_outputs = item.get("simulated_outputs")
        if isinstance(step_id, str) and isinstance(simulated_outputs, Mapping) and simulated_outputs:
            outputs[step_id] = {str(key): value for key, value in simulated_outputs.items()}
    return outputs


def _fixture_with_handoff(fixture: Mapping[str, Any], handoff: Mapping[str, object]) -> dict[str, Any]:
    merged = dict(fixture)
    merged["handoff"] = {
        "run_id": handoff["run_id"],
        "from_mode": handoff["from_mode"],
        "next_step": handoff["next_step"],
        "outputs": handoff["outputs"],
        "available_outputs": handoff["available_outputs"],
    }
    return merged


def _invoke_broker(
    ir: WorkflowIR,
    step: WorkflowStep,
    fixture: Mapping[str, Any],
    broker: LocalWorkflowBroker,
    append_event: Callable[..., None],
    *,
    idempotency_key: str | None,
) -> tuple[Mapping[str, Any], str | None, str | None, tuple[str, ...]]:
    if step.uses is None:
        return {}, None, None, ()
    action = _action_for_step(ir, step)
    allowed_outputs = action.allowed_outputs if action is not None else ()
    append_event(
        "broker_action_started",
        "started",
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        allowed_outputs=list(allowed_outputs),
        idempotency_key=idempotency_key,
    )
    result = broker.invoke(
        BrokerRequest(
            step_id=step.step_id,
            connector_action=step.uses.dotted,
            input_ref=step.raw,
            fixture=fixture,
            allowed_outputs=allowed_outputs,
        )
    )
    status = "blocked" if result.issues or result.status == "blocked" else "completed"
    append_event(
        "broker_action_completed" if status == "completed" else "broker_action_blocked",
        status,
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        broker_provider_id=result.provider_id,
        broker_status=result.status,
        outputs=dict(result.outputs),
        issues=list(result.issues),
        idempotency_key=idempotency_key,
    )
    if result.status == "blocked" and not result.issues:
        return result.outputs, result.status, result.provider_id, ("broker blocked step",)
    return result.outputs, result.status, result.provider_id, result.issues


def _select_branch(step: WorkflowStep, fixture: Mapping[str, Any]) -> tuple[str | None, str | None]:
    decisions = fixture.get("decision", {})
    if isinstance(decisions, dict):
        selected = decisions.get(step.step_id)
        if isinstance(selected, str) and selected in step.branches:
            return selected, step.branches[selected]
    if len(step.branches) == 1:
        selected, target = next(iter(step.branches.items()))
        return selected, target
    return None, None


def _approval_required(step: WorkflowStep, action: ActionCapability | None) -> bool:
    return (
        step.approval is not None
        or step.side_effect_class in MUTATING_SIDE_EFFECTS
        or bool(action and action.side_effect)
        or bool(action and action.approval_required)
    )


def _action_for_step(ir: WorkflowIR, step: WorkflowStep) -> ActionCapability | None:
    if step.uses is None:
        return None
    connector = ir.connectors.get(step.uses.connector_id)
    if connector is None:
        return None
    return connector.actions.get(step.uses.action_id)


def _connector_action(step: WorkflowStep) -> str | None:
    return step.uses.dotted if step.uses is not None else None


def _idempotency_key(
    package_fingerprint: str,
    step: WorkflowStep,
    action: ActionCapability | None,
    fixture: Mapping[str, Any],
) -> str | None:
    if action is None or step.uses is None:
        return None
    return build_idempotency_key(
        package_fingerprint=package_fingerprint,
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        key_fields=action.idempotency_key_fields,
        input_ref=step.raw,
        fixture=fixture,
    )


def _trace(
    step: WorkflowStep,
    status: str,
    *,
    selected_branch: str | None = None,
    outputs: Mapping[str, Any] | None = None,
    issues: tuple[str, ...] = (),
    broker_status: str | None = None,
    broker_provider_id: str | None = None,
) -> StepTrace:
    return StepTrace(
        step_id=step.step_id,
        kind=step.kind.value,
        status=status,
        selected_branch=selected_branch,
        connector_action=_connector_action(step),
        side_effect_class=step.side_effect_class,
        approval_id=step.approval,
        broker_status=broker_status,
        broker_provider_id=broker_provider_id,
        simulated_outputs=outputs or {},
        issues=issues,
    )


def _blocked_report(
    request: WorkflowRunRequest,
    *,
    workflow_name: str,
    package_fingerprint: str,
    issues: tuple[str, ...],
) -> WorkflowRunReport:
    return WorkflowRunReport(
        run_id="",
        workflow_name=workflow_name,
        mode=request.mode,
        status="blocked",
        package_fingerprint=package_fingerprint,
        terminal_step=None,
        step_trace=(),
        brokered_actions=(),
        approvals=(),
        events=(),
        issues=issues,
    )
