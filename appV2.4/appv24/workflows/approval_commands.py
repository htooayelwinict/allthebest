"""Operator-facing approval ledger helpers for workflow runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.loader import load_package_documents
from appv24.workflows.run_store import WorkflowRunStore


@dataclass(frozen=True)
class WorkflowApprovalCommandResult:
    run_id: str
    approval_request_id: str
    approved: bool
    status: str
    approval_id: str
    step_id: str
    connector_action: str
    decided_by: str


@dataclass(frozen=True)
class WorkflowApprovalListItem:
    approval_request_id: str
    status: str
    approval_id: str
    step_id: str
    connector_action: str
    idempotency_key: str | None


@dataclass(frozen=True)
class WorkflowApprovalListResult:
    run_id: str
    items: tuple[WorkflowApprovalListItem, ...]


def list_workflow_approval_requests(package_path: str | Path, run_id: str) -> WorkflowApprovalListResult:
    package_path = Path(package_path).expanduser().resolve()
    store = WorkflowRunStore(package_path / ".workflow-runs")
    requests = store.read_approval_requests(run_id)
    decisions = _latest_decisions_by_request_id(store.read_approval_decisions(run_id))
    items = tuple(
        WorkflowApprovalListItem(
            approval_request_id=request.approval_request_id,
            status=decisions.get(request.approval_request_id, request.status),
            approval_id=request.approval_id,
            step_id=request.step_id,
            connector_action=request.connector_action,
            idempotency_key=request.idempotency_key,
        )
        for request in requests
    )
    return WorkflowApprovalListResult(run_id=run_id, items=items)


def format_workflow_approval_list(result: WorkflowApprovalListResult) -> str:
    lines = [f"Workflow approvals: {result.run_id}"]
    if not result.items:
        lines.append("none")
        return "\n".join(lines)
    for item in result.items:
        idempotency = item.idempotency_key or "<none>"
        lines.append(
            "- "
            f"{item.approval_request_id} "
            f"{item.status} "
            f"{item.step_id} "
            f"{item.connector_action} "
            f"approval={item.approval_id} "
            f"idempotency={idempotency}"
        )
    return "\n".join(lines)


def record_workflow_approval_command(
    package_path: str | Path,
    run_id: str,
    approval_request_id: str,
    *,
    approved: bool,
    decided_by: str = "operator",
) -> WorkflowApprovalCommandResult:
    package_path = Path(package_path).expanduser().resolve()
    documents = load_package_documents(package_path)
    package_fingerprint = fingerprint_package_documents(documents)
    record = WorkflowRunStore(package_path / ".workflow-runs").record_approval_decision(
        run_id,
        approval_request_id,
        ApprovalDecision(
            approved=approved,
            package_fingerprint=package_fingerprint,
            decided_by=decided_by,
        ),
    )
    return WorkflowApprovalCommandResult(
        run_id=str(record["run_id"]),
        approval_request_id=str(record["approval_request_id"]),
        approved=bool(record["approved"]),
        status=str(record["status"]),
        approval_id=str(record["approval_id"]),
        step_id=str(record["step_id"]),
        connector_action=str(record["connector_action"]),
        decided_by=str(record["decided_by"]),
    )


def format_workflow_approval_command_result(result: WorkflowApprovalCommandResult) -> str:
    decision = "approved" if result.approved else "denied"
    return "\n".join(
        [
            f"Workflow approval recorded: {decision}",
            f"run: {result.run_id}",
            f"request: {result.approval_request_id}",
            f"approval: {result.approval_id}",
            f"step: {result.step_id}",
            f"action: {result.connector_action}",
            f"status: {result.status}",
            f"decided by: {result.decided_by}",
        ]
    )


def _latest_decisions_by_request_id(decisions: tuple[dict[str, object], ...]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for decision in decisions:
        request_id = decision.get("approval_request_id")
        status = decision.get("status")
        if isinstance(request_id, str) and isinstance(status, str):
            latest[request_id] = status
    return latest
