from __future__ import annotations

from pathlib import Path

from appv24.workflows.broker import BrokerRequest
from appv24.workflows.local_artifacts import LocalArtifactProviders


def test_ticket_draft_provider_writes_only_under_artifact_root(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.ticket_create_draft(
        BrokerRequest(
            step_id="create_ticket_draft",
            connector_action="ticket.create_draft",
            input_ref={"with": {"title": "Support request"}},
            fixture={},
            allowed_outputs=("ticket_id", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["ticket_id"].startswith("local-ticket-")
    artifact_path = Path(str(result.outputs["artifact_path"]))
    assert artifact_path.is_file()
    assert artifact_path.is_relative_to(tmp_path)


def test_local_artifact_provider_filters_unallowed_outputs(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.gmail_create_draft(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={},
            fixture={},
            allowed_outputs=("draft_id",),
        )
    )

    assert result.outputs == {"draft_id": "local-draft-create_gmail_draft"}


def test_local_artifact_provider_blocks_when_outputs_do_not_match_allowed_fields(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.ticket_create_draft(
        BrokerRequest(
            step_id="create_ticket_draft",
            connector_action="ticket.create_draft",
            input_ref={},
            fixture={},
            allowed_outputs=("draft_id",),
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == (
        "provider outputs for ticket.create_draft do not match declared allowed_outputs: "
        "produced artifact_path, ticket_id; allowed draft_id",
    )


def test_telegram_send_message_provider_writes_local_message_artifact(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.telegram_send_message(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={"with": {"text": "Ticket created"}},
            fixture={},
            allowed_outputs=("message_id", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["message_id"] == "local-message-notify_admin"
    artifact_path = Path(str(result.outputs["artifact_path"]))
    assert artifact_path.name == "local-telegram-notify_admin.json"
    assert artifact_path.is_file()
    assert artifact_path.is_relative_to(tmp_path)


def test_telegram_create_message_draft_provider_writes_local_draft_artifact(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)
    result = providers.telegram_create_message_draft(
        BrokerRequest(
            step_id="draft_admin_message",
            connector_action="telegram.create_message_draft",
            input_ref={"with": {"text": "Ticket ready for review"}},
            fixture={},
            allowed_outputs=("draft_id", "artifact_path"),
        )
    )

    assert result.status == "simulated"
    assert result.outputs["draft_id"] == "local-telegram-draft-draft_admin_message"
    artifact_path = Path(str(result.outputs["artifact_path"]))
    assert artifact_path.name == "local-telegram-draft-draft_admin_message.json"
    assert artifact_path.is_file()
    assert artifact_path.is_relative_to(tmp_path)


def test_local_artifact_provider_map_exposes_supported_actions(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)

    assert sorted(providers.provider_map()) == [
        "gmail.create_draft",
        "llm.classify",
        "llm.extract_structured",
        "llm.summarize",
        "notification.create_draft",
        "policy.check",
        "source.fetch_document",
        "source.fetch_items",
        "telegram.create_message_draft",
        "telegram.send_message",
        "ticket.create_draft",
    ]


def test_llm_extract_provider_honors_event_alert_contract_outputs(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)

    result = providers.llm_extract_structured(
        BrokerRequest(
            step_id="extract_catalysts",
            connector_action="llm.extract_structured",
            input_ref={"with": {"output_contract": "event_alerts_v1"}},
            fixture={},
            allowed_outputs=("alerts", "confidence_score", "raw_response"),
        )
    )

    assert result.status == "simulated"
    assert sorted(result.outputs) == ["alerts", "confidence_score", "raw_response"]
    assert result.outputs["confidence_score"] == "medium"
    assert result.outputs["alerts"]


def test_llm_extract_provider_blocks_event_alert_contract_with_incompatible_outputs(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)

    result = providers.llm_extract_structured(
        BrokerRequest(
            step_id="extract_catalysts",
            connector_action="llm.extract_structured",
            input_ref={"with": {"output_contract": "event_alerts_v1"}},
            fixture={},
            allowed_outputs=("items", "confidence"),
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == (
        "provider outputs for llm.extract_structured do not match declared allowed_outputs: "
        "produced alerts, confidence_score, raw_response; allowed confidence, items",
    )


def test_policy_check_provider_blocks_rejected_fixture(tmp_path: Path) -> None:
    providers = LocalArtifactProviders(tmp_path)

    result = providers.policy_check(
        BrokerRequest(
            step_id="check_policy",
            connector_action="policy.check",
            input_ref={"with": {"checks": {"disallow_financial_advice": True}}},
            fixture={"policy": {"check_policy": "rejected"}},
            allowed_outputs=("approved", "reason"),
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {"approved": False, "reason": "policy rejected"}
    assert result.issues == ("policy rejected for check_policy",)
