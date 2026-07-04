from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker


def test_local_broker_filters_outputs_to_allowed_fields() -> None:
    broker = LocalWorkflowBroker(
        {
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"message_id": "m-1", "secret": "blocked"},
            )
        }
    )

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
        )
    )

    assert result.status == "simulated"
    assert result.outputs == {"message_id": "m-1"}
    assert result.issues == ()


def test_local_broker_blocks_unknown_connector_action() -> None:
    broker = LocalWorkflowBroker({})

    result = broker.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={},
            fixture={},
            allowed_outputs=("message_id",),
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("no dry-run provider registered for telegram.send_message",)


def test_local_broker_blocks_non_dry_run_request() -> None:
    broker = LocalWorkflowBroker(
        {
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"message_id": "m-1"},
            )
        }
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
    assert result.issues == ("broker only supports dry-run requests",)
