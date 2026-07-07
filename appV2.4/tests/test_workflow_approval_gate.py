from __future__ import annotations

from pathlib import Path

from appv24.workflows.approval_commands import format_workflow_approval_list, list_workflow_approval_requests
from appv24.workflows.approval_gate import (
    ApprovalDecision,
    ApprovalRequest,
    create_approval_request,
    resume_approval,
)
from appv24.workflows.run_store import WorkflowRunStore


def test_create_approval_request_records_package_fingerprint_and_action() -> None:
    request = create_approval_request(
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id", "admin_chat_id"),
        idempotency_key="idemp:abc",
    )

    assert request.status == "pending"
    assert request.package_fingerprint == "sha256:abc"
    assert request.connector_action == "telegram.send_message"
    assert request.idempotency_key == "idemp:abc"


def test_create_approval_request_records_payload_binding() -> None:
    request = create_approval_request(
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id", "admin_chat_id"),
        idempotency_key="idemp:abc",
        payload_hash="payload:abc",
        payload_preview_path="artifacts/approvals/approval-1.json",
    )

    assert request.payload_hash == "payload:abc"
    assert request.payload_preview_path == "artifacts/approvals/approval-1.json"


def test_resume_approval_blocks_stale_package_fingerprint() -> None:
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        status="pending",
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:changed", decided_by="Lewis")

    try:
        resume_approval(request, decision)
    except ValueError as error:
        assert str(error) == "approval package fingerprint is stale"
    else:
        raise AssertionError("expected stale approval to fail")


def test_resume_approval_returns_approved_status() -> None:
    request = create_approval_request(
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:abc", decided_by="Lewis")

    assert resume_approval(request, decision).status == "approved"


def test_list_workflow_approval_requests_formats_pending_request(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    store = WorkflowRunStore(package / ".workflow-runs")
    run_id = store.create_run("gmail_draft_reply", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="create_gmail_draft",
        connector_action="gmail.create_draft",
        approval_id="human-review",
        allowed_outputs=("draft_id", "thread_id"),
        idempotency_key_fields=("thread_id", "body"),
        idempotency_key="idemp:abc",
        payload_hash="payload:abc",
        payload_preview_path="artifacts/approvals/approval-1.json",
    )
    store.write_approvals(run_id, [], approval_requests=(request,))

    result = list_workflow_approval_requests(package, run_id)

    assert result.run_id == run_id
    assert len(result.items) == 1
    assert result.items[0].approval_request_id == "approval-1"
    assert result.items[0].status == "pending"
    assert result.items[0].step_id == "create_gmail_draft"
    assert result.items[0].connector_action == "gmail.create_draft"
    assert result.items[0].idempotency_key == "idemp:abc"
    assert result.items[0].payload_preview_path == "artifacts/approvals/approval-1.json"
    formatted = format_workflow_approval_list(result)
    assert f"Workflow approvals: {run_id}" in formatted
    assert (
        "- approval-1 pending create_gmail_draft gmail.create_draft approval=human-review "
        "idempotency=idemp:abc preview=artifacts/approvals/approval-1.json"
    ) in formatted


def test_list_workflow_approval_requests_reflects_latest_decision(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    store = WorkflowRunStore(package / ".workflow-runs")
    run_id = store.create_run("gmail_draft_reply", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="create_gmail_draft",
        connector_action="gmail.create_draft",
        approval_id="human-review",
        allowed_outputs=("draft_id", "thread_id"),
        idempotency_key_fields=("thread_id", "body"),
        idempotency_key="idemp:abc",
    )
    store.write_approvals(run_id, [], approval_requests=(request,))
    store.record_approval_decision(
        run_id,
        "approval-1",
        ApprovalDecision(approved=True, package_fingerprint="sha256:abc", decided_by="test"),
    )
    store.record_approval_decision(
        run_id,
        "approval-1",
        ApprovalDecision(approved=False, package_fingerprint="sha256:abc", decided_by="test"),
    )

    result = list_workflow_approval_requests(package, run_id)

    assert result.items[0].status == "denied"
    formatted = format_workflow_approval_list(result)
    assert "- approval-1 denied create_gmail_draft gmail.create_draft approval=human-review idempotency=idemp:abc" in formatted
