from __future__ import annotations

from appv24.workflows.broker import BrokerRequest
from appv24.workflows.connectors.draft_gmail import gmail_create_draft_provider
from appv24.workflows.connectors.draft_telegram import telegram_send_message_draft_provider
from appv24.workflows.enablement import build_workflow_enablement_report
from appv24.workflows.examples import golden_workflow_examples


def test_gmail_draft_provider_dry_run_never_sends() -> None:
    result = gmail_create_draft_provider(
        BrokerRequest(
            step_id="draft_reply",
            connector_action="gmail.create_draft",
            input_ref={"with": {"to": "admin@example.com", "subject": "Ticket", "body": "Draft only"}},
            fixture={},
            allowed_outputs=("draft_id", "thread_id"),
            dry_run=True,
        )
    )

    assert result.status == "simulated"
    assert result.provider_id == "gmail_draft"
    assert set(result.outputs) == {"draft_id", "thread_id"}
    assert result.issues == ()


def test_gmail_draft_provider_blocks_non_dry_run() -> None:
    result = gmail_create_draft_provider(
        BrokerRequest(
            step_id="draft_reply",
            connector_action="gmail.create_draft",
            input_ref={},
            fixture={},
            allowed_outputs=("draft_id",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("gmail draft provider only supports dry-run in appv24 phase 5",)


def test_telegram_draft_provider_dry_run_never_sends() -> None:
    result = telegram_send_message_draft_provider(
        BrokerRequest(
            step_id="draft_admin_message",
            connector_action="telegram.send_message_draft",
            input_ref={"with": {"chat_id": "admin", "text": "Draft only"}},
            fixture={},
            allowed_outputs=("draft_id",),
            dry_run=True,
        )
    )

    assert result.status == "simulated"
    assert result.provider_id == "telegram_draft"
    assert result.outputs == {"draft_id": "dryrun-draft_admin_message"}


def test_gmail_draft_pilot_passes_enablement_without_network() -> None:
    example = golden_workflow_examples()["gmail_draft_triage"]

    report = build_workflow_enablement_report(example.documents, broker=example.broker)

    assert report.ok is True
    assert report.issues == ()
    assert tuple(item.action for item in report.capability_plan) == ("gmail.create_draft",)
    assert tuple(item.step_id for item in report.approval_plan.items) == ("draft_reply",)
