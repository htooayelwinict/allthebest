from __future__ import annotations

from pathlib import Path

from appv24.workflows.approval_gate import ApprovalDecision, ApprovalRequest
from appv24.workflows.ledger import StepTrace
from appv24.workflows.execution import WorkflowRunEvent
from appv24.workflows.run_store import WorkflowRunStore


def test_run_store_creates_run_directory_and_appends_events(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id=run_id,
        event_type="preview_created",
        package_fingerprint="sha256:abc",
        step_id=None,
        connector_action=None,
        status="passed",
        details={"fixture_name": "happy_path.yaml"},
    )

    store.append_event(event)

    assert (tmp_path / run_id / "run.json").is_file()
    assert store.read_events(run_id) == (event,)


def test_run_store_reads_run_manifest(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run(
        "telegram_support_ticket",
        "sha256:abc",
        metadata={"mode": "local", "fixture_name": "happy_path.yaml"},
    )

    assert hasattr(store, "read_run_manifest")
    assert store.read_run_manifest(run_id) == {
        "fixture_name": "happy_path.yaml",
        "mode": "local",
        "package_fingerprint": "sha256:abc",
        "run_id": run_id,
        "workflow_name": "telegram_support_ticket",
    }


def test_run_store_allows_risk_review_metadata_path(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    package_path = tmp_path / "iam-access-risk-review-assistant"

    run_id = store.create_run(
        "iam_access_risk_review",
        "sha256:abc",
        metadata={"package_path": str(package_path)},
    )

    assert store.read_run_manifest(run_id)["package_path"] == str(package_path)


def test_run_store_blocks_event_for_different_run_id(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    store.create_run("telegram_support_ticket", "sha256:abc")
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id="missing",
        event_type="preview_created",
        package_fingerprint="sha256:abc",
        step_id=None,
        connector_action=None,
        status="passed",
        details={},
    )

    try:
        store.append_event(event)
    except ValueError as error:
        assert str(error) == "unknown workflow run: missing"
    else:
        raise AssertionError("expected unknown run to fail")


def test_run_store_rejects_secret_looking_event_details(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id=run_id,
        event_type="preview_created",
        package_fingerprint="sha256:abc",
        step_id=None,
        connector_action=None,
        status="blocked",
        details={"token": "sk-or-v1-1234567890abcdef1234567890abcdef"},
    )

    try:
        store.append_event(event)
    except ValueError as error:
        assert str(error) == "workflow run event contains a raw secret-looking value"
    else:
        raise AssertionError("expected raw secret event to fail")


def test_run_store_writes_report_trace_approvals_and_artifact_root(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")

    artifact_root = store.artifact_root(run_id)
    store.write_trace(run_id, (StepTrace(step_id="done", kind="terminal", status="terminal"),))
    store.write_approvals(run_id, [{"step_id": "notify_admin", "status": "approved"}])
    store.write_report(
        run_id,
        {
            "run_id": run_id,
            "workflow_name": "telegram_support_ticket",
            "status": "passed",
            "terminal_step": "done",
        },
    )

    assert artifact_root == tmp_path / run_id / "artifacts"
    assert artifact_root.is_dir()
    assert (tmp_path / run_id / "trace.json").is_file()
    assert (tmp_path / run_id / "approvals.json").is_file()
    assert (tmp_path / run_id / "report.json").is_file()


def test_run_store_reads_persisted_approval_requests(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        idempotency_key="idemp:abc",
        status="pending",
    )

    store.write_approvals(run_id, [], approval_requests=(request,))

    assert store.read_approval_requests(run_id) == (request,)


def test_run_store_records_approval_decision_by_request_id(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        idempotency_key="idemp:abc",
        status="pending",
    )
    decision = ApprovalDecision(approved=False, package_fingerprint="sha256:abc", decided_by="Lewis")
    store.write_approvals(run_id, [], approval_requests=(request,))

    record = store.record_approval_decision(run_id, "approval-1", decision)

    assert record == {
        "approval_request_id": "approval-1",
        "run_id": run_id,
        "approval_id": "admin_approval",
        "step_id": "notify_admin",
        "connector_action": "telegram.send_message",
        "approved": False,
        "status": "denied",
        "package_fingerprint": "sha256:abc",
        "decided_by": "Lewis",
        "idempotency_key": "idemp:abc",
        "payload_hash": None,
        "payload_preview_path": None,
    }
    assert store.read_approval_decisions(run_id) == (record,)


def test_run_store_records_approval_payload_binding_in_decision(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        idempotency_key="idemp:abc",
        payload_hash="payload:abc",
        payload_preview_path="artifacts/approvals/approval-1.json",
        status="pending",
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:abc", decided_by="Lewis")
    store.write_approvals(run_id, [], approval_requests=(request,))

    record = store.record_approval_decision(run_id, "approval-1", decision)

    assert record["payload_hash"] == "payload:abc"
    assert record["payload_preview_path"] == "artifacts/approvals/approval-1.json"
    assert store.read_approval_decisions(run_id) == (record,)


def test_run_store_rejects_unknown_approval_request_id(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        status="pending",
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:abc", decided_by="Lewis")
    store.write_approvals(run_id, [], approval_requests=(request,))

    try:
        store.record_approval_decision(run_id, "approval-missing", decision)
    except ValueError as error:
        assert str(error) == "unknown workflow approval request: approval-missing"
    else:
        raise AssertionError("expected unknown approval request to fail")

    assert store.read_approval_decisions(run_id) == ()


def test_run_store_appends_and_reads_approval_decisions(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        idempotency_key="idemp:abc",
        status="pending",
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:abc", decided_by="Lewis")

    store.append_approval_decision(request, decision)

    decisions = store.read_approval_decisions(run_id)
    assert decisions == (
        {
            "approval_request_id": "approval-1",
            "run_id": run_id,
            "approval_id": "admin_approval",
            "step_id": "notify_admin",
            "connector_action": "telegram.send_message",
            "approved": True,
            "status": "approved",
            "package_fingerprint": "sha256:abc",
            "decided_by": "Lewis",
            "idempotency_key": "idemp:abc",
            "payload_hash": None,
            "payload_preview_path": None,
        },
    )


def test_run_store_rejects_stale_approval_decision_without_persisting(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run_id = store.create_run("telegram_support_ticket", "sha256:abc")
    request = ApprovalRequest(
        approval_request_id="approval-1",
        run_id=run_id,
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        approval_id="admin_approval",
        allowed_outputs=("message_id",),
        idempotency_key_fields=("ticket_id",),
        idempotency_key="idemp:abc",
        status="pending",
    )
    decision = ApprovalDecision(approved=True, package_fingerprint="sha256:changed", decided_by="Lewis")

    try:
        store.append_approval_decision(request, decision)
    except ValueError as error:
        assert str(error) == "approval package fingerprint is stale"
    else:
        raise AssertionError("expected stale approval decision to fail")

    assert store.read_approval_decisions(run_id) == ()
