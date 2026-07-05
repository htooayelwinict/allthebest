from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport


class FakeTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(status="live-read", provider_id="fake", outputs={"alerts": [], "secret": "blocked"})


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
    assert result.issues == ("live action is not allowlisted: github.list_dependabot_alerts",)


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
