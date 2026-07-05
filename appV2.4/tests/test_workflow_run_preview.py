from __future__ import annotations

from pathlib import Path

from appv24.workflows.broker import BrokerResult, LocalWorkflowBroker
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.run_preview import build_run_preview, build_run_preview_from_path


def test_run_preview_reports_trace_actions_approvals_idempotency_and_outputs() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    preview = build_run_preview(
        example.documents,
        "happy_path.yaml",
        broker=example.broker,
    )

    assert preview.workflow_name == "telegram_support_ticket"
    assert preview.fixture_name == "happy_path.yaml"
    assert preview.status == "passed"
    assert preview.terminal_step == "done"
    assert [step.step_id for step in preview.step_trace] == [
        "check_authorized_customer",
        "create_ticket_draft",
        "notify_admin",
        "done",
    ]
    assert [action.connector_action for action in preview.brokered_actions] == [
        "ticket.create_draft",
        "telegram.send_message",
    ]
    assert preview.brokered_actions[0].allowed_outputs == ("ticket_id",)
    assert preview.brokered_actions[0].idempotency_key_fields == ("telegram_user_id", "message_text")
    assert preview.brokered_actions[0].simulated_outputs == {"ticket_id": "ticket-1"}
    assert preview.brokered_actions[1].allowed_outputs == ("message_id",)
    assert preview.brokered_actions[1].simulated_outputs == {"message_id": "msg-1"}
    assert preview.approvals_required[0].approval_id == "admin_approval"
    assert preview.approvals_required[0].step_ids == ("create_ticket_draft", "notify_admin")
    assert [event.event_type for event in preview.ledger_preview] == [
        "approval_requested",
        "approval_decision_placeholder",
        "broker_action_planned",
        "outputs_simulated",
        "approval_requested",
        "approval_decision_placeholder",
        "broker_action_planned",
        "outputs_simulated",
    ]


def test_run_preview_is_path_specific_for_rejection_fixture() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    preview = build_run_preview(
        example.documents,
        "rejection_path.yaml",
        broker=example.broker,
    )

    assert preview.status == "passed"
    assert preview.terminal_step == "rejected"
    assert [step.step_id for step in preview.step_trace] == ["check_authorized_customer", "rejected"]
    assert preview.brokered_actions == ()
    assert preview.approvals_required == ()
    assert preview.ledger_preview == ()


def test_run_preview_loads_saved_package_from_path(tmp_path: Path) -> None:
    from test_workflow_packages import write_minimal_package

    package = tmp_path / "package"
    write_minimal_package(package)
    broker = LocalWorkflowBroker(
        {
            "github.list_dependabot_alerts": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"alerts": []},
            ),
            "github.create_issue": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"issue_number": 12, "issue_url": "https://example.invalid/12"},
            ),
        }
    )

    preview = build_run_preview_from_path(package, "happy_path.yaml", broker=broker)

    assert preview.workflow_name == "dependency-security-review"
    assert preview.status == "passed"
    assert preview.terminal_step == "done"
    assert [action.connector_action for action in preview.brokered_actions] == [
        "github.list_dependabot_alerts",
        "github.create_issue",
    ]


def test_run_preview_includes_package_fingerprint() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    preview = build_run_preview(example.documents, "happy_path.yaml", broker=example.broker)

    assert preview.package_fingerprint.startswith("sha256:")
