from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport


class FakeTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(status="live-read", provider_id="fake", outputs={"alerts": [], "secret": "blocked"})


class StatusTransport(LiveWorkflowTransport):
    def __init__(self, status: str):
        self.status = status

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(status=self.status, provider_id="fake", outputs={"message_id": "msg-1", "secret": "blocked"})


class BlockedOutputTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(
            status="blocked",
            provider_id="fake",
            outputs={"message_id": "msg-1", "secret": "blocked"},
            issues=("provider refused request",),
        )


class RecordingTransport(LiveWorkflowTransport):
    def __init__(self, status: str):
        self.status = status
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(status=self.status, provider_id="fake", outputs={"message_id": "msg-1", "draft_id": "draft-1"})


def test_live_broker_rejects_unknown_mode() -> None:
    try:
        LiveWorkflowBroker({"telegram.send_message": FakeTransport()}, mode="production", production_enabled=True)
    except ValueError as error:
        assert str(error) == "unknown live workflow broker mode: production"
    else:
        raise AssertionError("expected invalid live broker mode to fail")


def test_live_broker_rejects_production_flag_outside_live_production() -> None:
    try:
        LiveWorkflowBroker({"github.list_dependabot_alerts": FakeTransport()}, mode="live-read", production_enabled=True)
    except ValueError as error:
        assert str(error) == "production_enabled is only valid for live-production broker mode"
    else:
        raise AssertionError("expected production flag outside live-production to fail")


def test_live_broker_blocks_without_action_allowlist() -> None:
    broker = LiveWorkflowBroker({}, mode="live-read")
    result = broker.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={},
            fixture={},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.issues == ("live transport is not configured for github.list_dependabot_alerts",)


def test_live_broker_allows_read_only_fake_transport_when_allowlisted() -> None:
    broker = LiveWorkflowBroker({"github.list_dependabot_alerts": FakeTransport()}, mode="live-read")
    result = broker.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={},
            fixture={},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.outputs == {"alerts": []}


def test_live_broker_allows_declared_external_action_transport() -> None:
    broker = LiveWorkflowBroker(
        {"crm.lookup_contact": FakeTransport()},
        mode="live-read",
        external_actions=("crm.lookup_contact",),
    )
    result = broker.invoke(
        BrokerRequest(
            step_id="lookup_contact",
            connector_action="crm.lookup_contact",
            input_ref={"with": {"email": {"inputRef": "email"}}},
            fixture={"input": {"email": "customer@example.com"}},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.outputs == {"alerts": []}


def test_live_broker_blocks_undeclared_external_action_transport() -> None:
    broker = LiveWorkflowBroker({"crm.lookup_contact": FakeTransport()}, mode="live-read")
    result = broker.invoke(
        BrokerRequest(
            step_id="lookup_contact",
            connector_action="crm.lookup_contact",
            input_ref={"with": {"email": {"inputRef": "email"}}},
            fixture={"input": {"email": "customer@example.com"}},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("live connector action is not cataloged: crm.lookup_contact",)


def test_live_broker_blocks_output_mismatch() -> None:
    broker = LiveWorkflowBroker({"github.list_dependabot_alerts": FakeTransport()}, mode="live-read")
    result = broker.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={},
            fixture={},
            allowed_outputs=("items",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == (
        "provider outputs for github.list_dependabot_alerts do not match declared allowed_outputs: "
        "produced alerts, secret; allowed items",
    )


def test_live_broker_drops_outputs_from_blocked_transport_result() -> None:
    broker = LiveWorkflowBroker({"telegram.send_message": BlockedOutputTransport()}, mode="live-draft")

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("provider refused request",)


def test_live_broker_blocks_production_mode_by_default() -> None:
    broker = LiveWorkflowBroker({"github.list_dependabot_alerts": FakeTransport()}, mode="live-production")
    result = broker.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={},
            fixture={},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.issues == ("live-production broker is disabled by default",)


def test_live_broker_routes_production_mode_only_with_explicit_flag() -> None:
    broker = LiveWorkflowBroker(
        {"telegram.send_message": StatusTransport("live-production")},
        mode="live-production",
        production_enabled=True,
    )
    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
            dry_run=False,
        )
    )

    assert result.status == "live-production"
    assert result.outputs == {"message_id": "msg-1"}


def test_live_broker_blocks_transport_status_mismatch() -> None:
    broker = LiveWorkflowBroker(
        {"telegram.send_message": StatusTransport("live-read")},
        mode="live-production",
        production_enabled=True,
    )

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == (
        "live transport for telegram.send_message returned status live-read; expected live-production",
    )


def test_live_broker_blocks_unsupported_telegram_input_before_transport() -> None:
    transport = RecordingTransport("live-production")
    broker = LiveWorkflowBroker(
        {"telegram.send_message": transport},
        mode="live-production",
        production_enabled=True,
    )

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={
                "with": {
                    "chat_id": "admin-chat",
                    "text": "Ticket ready",
                    "disable_web_page_preview": True,
                }
            },
            fixture={},
            allowed_outputs=("message_id",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("telegram.send_message input contains unsupported fields: disable_web_page_preview",)
    assert transport.requests == []


def test_live_broker_blocks_unsupported_gmail_input_before_transport() -> None:
    transport = RecordingTransport("live-draft")
    broker = LiveWorkflowBroker({"gmail.create_draft": transport}, mode="live-draft")

    result = broker.invoke(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={
                "with": {
                    "to": "customer@example.com",
                    "subject": "Ticket update",
                    "body": "We opened your ticket.",
                    "send": True,
                }
            },
            fixture={},
            allowed_outputs=("draft_id",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("gmail.create_draft input contains unsupported fields: send",)
    assert transport.requests == []
