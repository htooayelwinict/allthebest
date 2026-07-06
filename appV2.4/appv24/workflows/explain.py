"""Read-only workflow package and run explanation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.errors import WorkflowIssue, WorkflowPackageError
from appv24.workflows.loader import load_package_documents
from appv24.workflows.policy import PolicyIssue, evaluate_policy
from appv24.workflows.validator import validate_workflow_documents


@dataclass(frozen=True)
class WorkflowPackageExplanation:
    package_path: Path
    workflow_name: str
    validation_status: str
    compile_status: str
    policy_status: str
    steps: tuple[str, ...]
    connector_actions: tuple[str, ...]
    approvals: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowRunExplanation:
    package_path: Path
    run_id: str
    status: str
    terminal_step: str | None
    provenance: tuple[str, ...]
    gate: tuple[str, ...]
    trace: tuple[str, ...]
    brokered_actions: tuple[str, ...]
    approvals: tuple[str, ...]
    approval_decisions: tuple[str, ...]
    issues: tuple[str, ...]
    handoff: tuple[str, ...]
    next_steps: tuple[str, ...]


def explain_workflow_package(path: str | Path) -> WorkflowPackageExplanation:
    package_path = Path(path).expanduser().resolve()
    try:
        documents = load_package_documents(package_path)
    except WorkflowPackageError as error:
        return WorkflowPackageExplanation(
            package_path=package_path,
            workflow_name="",
            validation_status="failed",
            compile_status="skipped",
            policy_status="skipped",
            steps=(),
            connector_actions=(),
            approvals=(),
            issues=(str(error),),
        )

    workflow_name = str(documents.workflow.get("name", ""))
    steps = _workflow_step_ids(documents.workflow)
    connector_actions = _workflow_connector_actions(documents.workflow)

    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return WorkflowPackageExplanation(
            package_path=documents.root,
            workflow_name=workflow_name,
            validation_status="failed",
            compile_status="skipped",
            policy_status="skipped",
            steps=steps,
            connector_actions=connector_actions,
            approvals=(),
            issues=tuple(_format_validation_issue(issue) for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return WorkflowPackageExplanation(
            package_path=documents.root,
            workflow_name=workflow_name,
            validation_status="passed",
            compile_status="failed",
            policy_status="skipped",
            steps=steps,
            connector_actions=connector_actions,
            approvals=(),
            issues=tuple(_format_validation_issue(issue) for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return WorkflowPackageExplanation(
            package_path=documents.root,
            workflow_name=workflow_name,
            validation_status="passed",
            compile_status="passed",
            policy_status="failed",
            steps=tuple(compiled.ir.steps),
            connector_actions=connector_actions,
            approvals=tuple(compiled.ir.approvals),
            issues=tuple(_format_policy_issue(issue) for issue in policy.issues),
        )

    return WorkflowPackageExplanation(
        package_path=documents.root,
        workflow_name=workflow_name,
        validation_status="passed",
        compile_status="passed",
        policy_status="passed",
        steps=tuple(compiled.ir.steps),
        connector_actions=connector_actions,
        approvals=tuple(compiled.ir.approvals),
        issues=(),
    )


def explain_workflow_run(path: str | Path, run_id: str | None = None) -> WorkflowRunExplanation:
    package_path = Path(path).expanduser().resolve()
    return explain_workflow_run_artifacts(package_path, package_path / ".workflow-runs", run_id)


def explain_workflow_activation_run(store: Any, activation_id: str, run_id: str | None = None) -> WorkflowRunExplanation:
    activation = store.get_activation(activation_id)
    return explain_workflow_run_artifacts(
        activation.package_path,
        store.root / activation.activation_id / "runs",
        run_id,
    )


def explain_workflow_run_artifacts(
    package_path: str | Path,
    run_root: str | Path,
    run_id: str | None = None,
) -> WorkflowRunExplanation:
    package_path = Path(package_path).expanduser().resolve()
    run_root = Path(run_root).expanduser().resolve()
    selected_run_id, issues = _select_run_id(run_root, run_id)
    if selected_run_id is None:
        return WorkflowRunExplanation(
            package_path=package_path,
            run_id="",
            status="unavailable",
            terminal_step=None,
            provenance=(),
            gate=(),
            trace=(),
            brokered_actions=(),
            approvals=(),
            approval_decisions=(),
            issues=issues,
            handoff=(),
            next_steps=(),
        )

    run_dir = run_root / selected_run_id
    try:
        report = _read_json(run_dir / "report.json")
        trace_payload = _read_json(run_dir / "trace.json")
        approvals_payload = _read_json(run_dir / "approvals.json")
    except (OSError, json.JSONDecodeError) as error:
        return WorkflowRunExplanation(
            package_path=package_path,
            run_id=selected_run_id,
            status="unavailable",
            terminal_step=None,
            provenance=(),
            gate=(),
            trace=(),
            brokered_actions=(),
            approvals=(),
            approval_decisions=(),
            issues=(f"failed to read workflow run artifacts: {error}",),
            handoff=(),
            next_steps=(),
        )

    run_manifest = _read_optional_json(run_dir / "run.json")
    handoff_payload = _read_optional_json(run_dir / "handoff.json")
    events_payload = _read_optional_jsonl(run_dir / "events.jsonl")
    approval_decisions = _read_optional_jsonl(run_dir / "approval_decisions.jsonl")
    issues = tuple(str(issue) for issue in report.get("issues", []) if isinstance(issue, str))
    return WorkflowRunExplanation(
        package_path=package_path,
        run_id=selected_run_id,
        status=str(report.get("status", "unavailable")),
        terminal_step=report.get("terminal_step") if isinstance(report.get("terminal_step"), str) else None,
        provenance=_provenance(run_manifest),
        gate=_gate_details(events_payload),
        trace=_trace_steps(trace_payload),
        brokered_actions=_brokered_actions(report),
        approvals=_approvals(approvals_payload),
        approval_decisions=_approval_decisions(approval_decisions),
        issues=issues,
        handoff=_handoff_details(handoff_payload),
        next_steps=_next_steps(
            package_path=package_path,
            run_id=selected_run_id,
            run_manifest=run_manifest,
            approvals_payload=approvals_payload,
            report=report,
            handoff_payload=handoff_payload,
            issues=issues,
        ),
    )


def format_workflow_package_explanation(explanation: WorkflowPackageExplanation) -> str:
    lines = [
        f"Package: {explanation.workflow_name or '<unknown>'}",
        f"Validation: {explanation.validation_status}",
        f"Compile: {explanation.compile_status}",
        f"Policy: {explanation.policy_status}",
    ]
    if explanation.steps:
        lines.append("Steps:")
        lines.extend(f"- {step}" for step in explanation.steps)
    if explanation.connector_actions:
        lines.append("Connector actions:")
        lines.extend(f"- {action}" for action in explanation.connector_actions)
    if explanation.approvals:
        lines.append("Approvals:")
        lines.extend(f"- {approval}" for approval in explanation.approvals)
    if explanation.issues:
        lines.append("Issues:")
        lines.extend(f"- {issue}" for issue in explanation.issues)
    return "\n".join(lines)


def format_workflow_run_explanation(explanation: WorkflowRunExplanation) -> str:
    lines = [f"Workflow run: {explanation.status}"]
    if explanation.run_id:
        lines.append(f"run id: {explanation.run_id}")
    if explanation.terminal_step:
        lines.append(f"terminal: {explanation.terminal_step}")
    if explanation.brokered_actions:
        lines.append("Brokered actions:")
        lines.extend(f"- {action}" for action in explanation.brokered_actions)
    if explanation.provenance:
        lines.append("Provenance:")
        lines.extend(f"- {item}" for item in explanation.provenance)
    if explanation.gate:
        lines.append("Gate:")
        lines.extend(f"- {item}" for item in explanation.gate)
    if explanation.trace:
        lines.append("Trace:")
        lines.extend(f"- {step}" for step in explanation.trace)
    if explanation.approvals:
        lines.append("Approvals:")
        lines.extend(f"- {approval}" for approval in explanation.approvals)
    if explanation.approval_decisions:
        lines.append("Approval decisions:")
        lines.extend(f"- {decision}" for decision in explanation.approval_decisions)
    if explanation.issues:
        lines.append("Issues:")
        lines.extend(f"- {issue}" for issue in explanation.issues)
    if explanation.handoff:
        lines.append("Handoff:")
        lines.extend(f"- {item}" for item in explanation.handoff)
    if explanation.next_steps:
        lines.append("Next safe commands:")
        lines.extend(f"- {command}" for command in explanation.next_steps)
    return "\n".join(lines)


def _workflow_step_ids(workflow: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(step["id"])
        for step in workflow.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("id"), str)
    )


def _workflow_connector_actions(workflow: Mapping[str, Any]) -> tuple[str, ...]:
    actions: list[str] = []
    seen: set[str] = set()
    for step in workflow.get("steps", []):
        if not isinstance(step, dict) or not isinstance(step.get("uses"), str):
            continue
        action = str(step["uses"])
        if action not in seen:
            seen.add(action)
            actions.append(action)
    return tuple(actions)


def _format_validation_issue(issue: WorkflowIssue) -> str:
    return f"{issue.code} {issue.location}: {issue.message}"


def _format_policy_issue(issue: PolicyIssue) -> str:
    location = f" step={issue.step_id}" if issue.step_id else ""
    return f"{issue.code}{location}: {issue.message}"


def _select_run_id(run_root: Path, run_id: str | None) -> tuple[str | None, tuple[str, ...]]:
    if run_id is not None:
        if (run_root / run_id).is_dir():
            return run_id, ()
        return None, (f"workflow run not found: {run_id}",)
    if not run_root.is_dir():
        return None, ("no workflow runs found",)
    run_ids = tuple(sorted(path.name for path in run_root.iterdir() if path.is_dir() and path.name.startswith("run-")))
    if not run_ids:
        return None, ("no workflow runs found",)
    if len(run_ids) > 1:
        return None, (f"multiple workflow runs found: {', '.join(run_ids)}",)
    return run_ids[0], ()


def _read_json(path: Path) -> Mapping[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _read_optional_json(path: Path) -> Mapping[str, Any]:
    try:
        return _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _read_optional_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    values: list[Mapping[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            values.append(parsed)
    return tuple(values)


def _provenance(run_manifest: Mapping[str, Any]) -> tuple[str, ...]:
    items: list[str] = []
    mode = run_manifest.get("mode")
    if isinstance(mode, str):
        items.append(f"mode: {mode}")
    fixture_name = run_manifest.get("fixture_name")
    if isinstance(fixture_name, str):
        items.append(f"fixture: {fixture_name}")
    package_path = run_manifest.get("package_path")
    if isinstance(package_path, str):
        items.append(f"package: {package_path}")
    approval_mode = run_manifest.get("approval_mode")
    if isinstance(approval_mode, str):
        items.append(f"approval: {approval_mode}")
    approval_run_id = run_manifest.get("approval_run_id")
    if isinstance(approval_run_id, str) and approval_run_id:
        items.append(f"approval run: {approval_run_id}")
    connector_allowlist = run_manifest.get("connector_allowlist")
    if isinstance(connector_allowlist, list) and connector_allowlist:
        actions = tuple(str(item) for item in connector_allowlist if isinstance(item, str))
        if actions:
            items.append(f"allowlist: {', '.join(actions)}")
    activation_id = run_manifest.get("activation_id")
    if isinstance(activation_id, str):
        items.append(f"activation: {activation_id}")
    activation_trigger_type = run_manifest.get("activation_trigger_type")
    if isinstance(activation_trigger_type, str):
        items.append(f"trigger: {activation_trigger_type}")
    return tuple(items)


def _gate_details(events: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    details: list[str] = []
    for event in events:
        event_type = event.get("event_type")
        if event_type not in {"live_gate_passed", "live_gate_blocked"}:
            continue
        status = event.get("status") if isinstance(event.get("status"), str) else "unknown"
        event_details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
        checked_actions = event_details.get("checked_actions")
        actions = _unique_strings(checked_actions) if isinstance(checked_actions, list | tuple) else ()
        suffix = f" actions={','.join(actions)}" if actions else ""
        details.append(f"live_gate: {status}{suffix}")
    return tuple(details)


def _trace_steps(payload: Mapping[str, Any]) -> tuple[str, ...]:
    trace = payload.get("trace", [])
    return tuple(str(item["step_id"]) for item in trace if isinstance(item, dict) and isinstance(item.get("step_id"), str))


def _brokered_actions(report: Mapping[str, Any]) -> tuple[str, ...]:
    actions = []
    for action in report.get("brokered_actions", []):
        if not isinstance(action, dict):
            continue
        step_id = action.get("step_id")
        connector_action = action.get("connector_action")
        if not isinstance(step_id, str) or not isinstance(connector_action, str):
            continue
        status = action.get("broker_status") if isinstance(action.get("broker_status"), str) else "planned"
        idempotency_key = action.get("idempotency_key")
        suffix = f" idempotency={_short_idempotency_key(idempotency_key)}" if isinstance(idempotency_key, str) else ""
        actions.append(f"{step_id}: {connector_action} -> {status}{suffix}")
    return tuple(actions)


def _short_idempotency_key(key: str) -> str:
    if len(key) <= 24:
        return key
    return f"{key[:24]}..."


def _approvals(payload: Mapping[str, Any]) -> tuple[str, ...]:
    approvals = []
    seen: set[str] = set()
    requests = _approval_request_details(payload)
    for approval in payload.get("approvals", []):
        if not isinstance(approval, dict):
            continue
        approval_id = approval.get("approval_id")
        if not isinstance(approval_id, str):
            continue
        status = approval.get("status") if isinstance(approval.get("status"), str) else "pending"
        rendered = f"{approval_id}: {status}{requests.get(approval_id, '')}"
        if rendered in seen:
            continue
        seen.add(rendered)
        approvals.append(rendered)
    return tuple(approvals)


def _approval_request_details(payload: Mapping[str, Any]) -> dict[str, str]:
    details: dict[str, str] = {}
    for request in payload.get("approval_requests", []):
        if not isinstance(request, dict):
            continue
        approval_id = request.get("approval_id")
        if not isinstance(approval_id, str):
            continue
        status = request.get("status")
        if status != "pending":
            continue
        parts = []
        request_id = request.get("approval_request_id")
        if isinstance(request_id, str):
            parts.append(f"request={request_id}")
        step_id = request.get("step_id")
        if isinstance(step_id, str):
            parts.append(f"step={step_id}")
        connector_action = request.get("connector_action")
        if isinstance(connector_action, str):
            parts.append(f"action={connector_action}")
        idempotency_key = request.get("idempotency_key")
        if isinstance(idempotency_key, str):
            parts.append(f"idempotency={_short_idempotency_key(idempotency_key)}")
        if parts:
            details[approval_id] = " " + " ".join(parts)
    return details


def _approval_decisions(values: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    decisions: list[str] = []
    for value in values:
        request_id = value.get("approval_request_id")
        if not isinstance(request_id, str):
            continue
        status = value.get("status") if isinstance(value.get("status"), str) else "unknown"
        decided_by = value.get("decided_by") if isinstance(value.get("decided_by"), str) else "unknown"
        parts = [f"{request_id}: {status} by {decided_by}"]
        connector_action = value.get("connector_action")
        if isinstance(connector_action, str):
            parts.append(f"action={connector_action}")
        idempotency_key = value.get("idempotency_key")
        if isinstance(idempotency_key, str):
            parts.append(f"idempotency={_short_idempotency_key(idempotency_key)}")
        decisions.append(" ".join(parts))
    return tuple(decisions)


def _handoff_details(payload: Mapping[str, Any]) -> tuple[str, ...]:
    if not payload:
        return ()
    from_mode = payload.get("from_mode")
    next_phase = payload.get("next_phase")
    next_step = payload.get("next_step")
    details: list[str] = []
    if isinstance(from_mode, str) and isinstance(next_phase, str) and isinstance(next_step, str):
        details.append(f"{from_mode} -> {next_phase} at {next_step}")
    output_refs = _handoff_output_refs(payload)
    if output_refs:
        details.append(f"available outputs: {', '.join(output_refs)}")
    return tuple(details)


def _handoff_output_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    available_outputs = payload.get("available_outputs")
    if not isinstance(available_outputs, list):
        return ()
    for item in available_outputs:
        if not isinstance(item, Mapping):
            continue
        step_id = item.get("step_id")
        output_keys = item.get("output_keys")
        if not isinstance(step_id, str) or not isinstance(output_keys, list):
            continue
        for key in output_keys:
            if isinstance(key, str):
                refs.append(f"{step_id}.{key}")
    return tuple(refs)


_NOT_ALLOWLISTED_RE = re.compile(r"action is not allowlisted: ([A-Za-z0-9_.-]+)")


def _next_steps(
    *,
    package_path: Path,
    run_id: str,
    run_manifest: Mapping[str, Any],
    approvals_payload: Mapping[str, Any],
    report: Mapping[str, Any],
    handoff_payload: Mapping[str, Any],
    issues: tuple[str, ...],
) -> tuple[str, ...]:
    status = report.get("status")
    if status == "passed":
        return ()

    package_arg = str(package_path)
    mode = run_manifest.get("mode") if isinstance(run_manifest.get("mode"), str) else ""
    fixture_name = run_manifest.get("fixture_name") if isinstance(run_manifest.get("fixture_name"), str) else "happy_path.yaml"
    allowlist = _allowlist_for_next_steps(run_manifest, approvals_payload, report, issues)
    commands: list[str] = []

    if status == "paused" and handoff_payload:
        commands.append(f"/workflow-live-plan {package_arg} {fixture_name}")
        commands.append(f"/workflow-run-approval {package_arg} {fixture_name}")
        return tuple(commands)

    pending_request_ids = _pending_approval_request_ids(approvals_payload)
    if pending_request_ids:
        commands.append(f"/workflow-approvals {package_arg} {run_id}")
        commands.append(f"/workflow-approve {package_arg} {run_id} {pending_request_ids[0]}")
        commands.append(f"/workflow-live-ready live-draft {package_arg} {fixture_name} {run_id} --allow {allowlist}")
        return tuple(commands)

    if mode == "live-read":
        commands.append(f"/workflow-live-env-template live-read {package_arg} --allow {allowlist}")
        commands.append(f"/workflow-live-ready live-read {package_arg} {fixture_name} --allow {allowlist}")
        commands.append(f"/workflow-run-live-read {package_arg} {fixture_name} --allow {allowlist}")
        return tuple(commands)

    if mode in {"live-draft", "live-production"}:
        approval_run_id = run_manifest.get("approval_run_id") if isinstance(run_manifest.get("approval_run_id"), str) else ""
        approval_arg = approval_run_id or "<approval-run-id>"
        commands.append(f"/workflow-live-env-template {mode} {package_arg} --allow {allowlist}")
        commands.append(f"/workflow-live-ready {mode} {package_arg} {fixture_name} {approval_arg} --allow {allowlist}")
        commands.append(f"/workflow-run-{mode} {package_arg} {fixture_name} {approval_arg} --allow {allowlist}")
        return tuple(commands)

    if issues:
        commands.append(f"/workflow-explain {package_arg}")
    return tuple(commands)


def _allowlist_for_next_steps(
    run_manifest: Mapping[str, Any],
    approvals_payload: Mapping[str, Any],
    report: Mapping[str, Any],
    issues: tuple[str, ...],
) -> str:
    actions = _manifest_allowlist(run_manifest)
    if not actions:
        actions = _issue_allowlisted_actions(issues)
    if not actions:
        actions = _pending_approval_actions(approvals_payload)
    if not actions:
        actions = _brokered_action_ids(report)
    return ",".join(actions) if actions else "<action>[,<action>]"


def _manifest_allowlist(run_manifest: Mapping[str, Any]) -> tuple[str, ...]:
    raw = run_manifest.get("connector_allowlist")
    if not isinstance(raw, list | tuple):
        return ()
    return _unique_strings(raw)


def _issue_allowlisted_actions(issues: tuple[str, ...]) -> tuple[str, ...]:
    actions: list[str] = []
    for issue in issues:
        match = _NOT_ALLOWLISTED_RE.search(issue)
        if match:
            actions.append(match.group(1))
    return _unique_strings(actions)


def _pending_approval_request_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for request in payload.get("approval_requests", []):
        if not isinstance(request, dict) or request.get("status") != "pending":
            continue
        request_id = request.get("approval_request_id")
        if isinstance(request_id, str):
            ids.append(request_id)
    return _unique_strings(ids)


def _pending_approval_actions(payload: Mapping[str, Any]) -> tuple[str, ...]:
    actions: list[str] = []
    for request in payload.get("approval_requests", []):
        if not isinstance(request, dict) or request.get("status") != "pending":
            continue
        connector_action = request.get("connector_action")
        if isinstance(connector_action, str):
            actions.append(connector_action)
    return _unique_strings(actions)


def _brokered_action_ids(report: Mapping[str, Any]) -> tuple[str, ...]:
    actions: list[str] = []
    for action in report.get("brokered_actions", []):
        if not isinstance(action, dict):
            continue
        connector_action = action.get("connector_action")
        if isinstance(connector_action, str):
            actions.append(connector_action)
    return _unique_strings(actions)


def _unique_strings(values: Any) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)
