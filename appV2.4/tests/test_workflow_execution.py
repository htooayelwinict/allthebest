from __future__ import annotations

from pathlib import Path

from appv24.workflows.execution import (
    WorkflowRunEvent,
    WorkflowRunMode,
    WorkflowRunReport,
    WorkflowRunRequest,
)


def test_workflow_run_request_defaults_to_preview_without_allowlist() -> None:
    request = WorkflowRunRequest(package_path=Path("pkg"), fixture_name="happy_path.yaml")

    assert request.mode is WorkflowRunMode.PREVIEW
    assert request.connector_allowlist == ()
    assert request.operator_env == {}
    assert request.run_root is None


def test_workflow_run_request_operator_env_is_available_for_live_gates() -> None:
    request = WorkflowRunRequest(
        package_path=Path("pkg"),
        fixture_name="happy_path.yaml",
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
    )

    assert request.operator_env == {"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"}


def test_workflow_run_event_round_trips_through_json_dict() -> None:
    event = WorkflowRunEvent(
        event_id="event-1",
        run_id="run-1",
        event_type="broker_action_planned",
        package_fingerprint="sha256:abc",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        status="planned",
        details={"allowed_outputs": ["message_id"]},
    )

    assert WorkflowRunEvent.from_dict(event.to_dict()) == event


def test_workflow_run_report_serializes_core_fields() -> None:
    report = WorkflowRunReport(
        run_id="run-1",
        workflow_name="telegram_support_ticket",
        mode=WorkflowRunMode.PREVIEW,
        status="passed",
        package_fingerprint="sha256:abc",
        terminal_step="done",
        step_trace=(),
        brokered_actions=(),
        approvals=(),
        events=(),
        issues=(),
    )

    assert report.to_dict()["mode"] == "preview"
    assert report.to_dict()["package_fingerprint"] == "sha256:abc"


def test_workflow_run_request_can_carry_local_approval_policy() -> None:
    request = WorkflowRunRequest(
        package_path=Path("pkg"),
        fixture_name="happy_path.yaml",
        mode=WorkflowRunMode.LOCAL,
        approve_all=True,
    )

    assert request.approve_all is True
