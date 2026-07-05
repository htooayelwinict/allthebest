from __future__ import annotations

from appv24.workflows.approval_gate import (
    ApprovalDecision,
    ApprovalRequest,
    create_approval_request,
    resume_approval,
)


def test_create_approval_request_records_package_fingerprint_and_action() -> None:
    request = create_approval_request(
        run_id="run-1",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id", "admin_chat_id"),
    )

    assert request.status == "pending"
    assert request.package_fingerprint == "sha256:abc"
    assert request.connector_action == "telegram.send_message"


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
