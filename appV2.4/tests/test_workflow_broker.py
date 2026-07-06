from __future__ import annotations

from pathlib import Path

from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.ir import (
    ActionCapability,
    ApprovalRequirement,
    ConnectorCapability,
    ConnectorRef,
    StepKind,
    WorkflowDesignContract,
    WorkflowIR,
    WorkflowStep,
)
from appv24.workflows.simulator import simulate_workflow_fixture


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


def test_local_broker_blocks_when_outputs_do_not_match_allowed_fields() -> None:
    broker = LocalWorkflowBroker(
        {
            "ticket.create_draft": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"ticket_id": "t-1"},
            )
        }
    )

    result = broker.invoke(
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
        "produced ticket_id; allowed draft_id",
    )


def test_local_broker_allows_provider_without_outputs() -> None:
    broker = LocalWorkflowBroker(
        {
            "noop.inspect": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={},
            )
        }
    )

    result = broker.invoke(
        BrokerRequest(
            step_id="inspect",
            connector_action="noop.inspect",
            input_ref={},
            fixture={},
            allowed_outputs=("result",),
        )
    )

    assert result.status == "simulated"
    assert result.outputs == {}
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


def test_simulator_uses_broker_outputs_for_action_steps() -> None:
    broker = LocalWorkflowBroker(
        {
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="fixture",
                outputs={"message_id": "m-1", "extra": "blocked"},
            )
        }
    )

    ledger = simulate_workflow_fixture(
        _minimal_ir(),
        "happy_path.yaml",
        {},
        broker=broker,
    )

    assert ledger.status == "passed"
    assert ledger.trace[0].broker_status == "simulated"
    assert ledger.trace[0].broker_provider_id == "fixture"
    assert ledger.trace[0].simulated_outputs == {"message_id": "m-1"}


def _minimal_ir() -> WorkflowIR:
    action = ActionCapability(
        connector_id="telegram",
        action_id="send_message",
        adapter="direct_api",
        side_effect=True,
        side_effect_class="send",
        execution_mode="brokered",
        approval_required=True,
        dry_run_supported=True,
        idempotency_key_fields=("message_key",),
        allowed_outputs=("message_id",),
        required_scopes=(),
        granted_scopes=(),
        raw={},
    )
    return WorkflowIR(
        root=Path("."),
        name="test",
        version="1.0.0",
        entrypoint="notify_admin",
        triggers=(),
        steps={
            "notify_admin": WorkflowStep(
                step_id="notify_admin",
                kind=StepKind.ACTION,
                uses=ConnectorRef("telegram", "send_message"),
                side_effect_class="send",
                approval="admin_approval",
                next_step="done",
                branches={},
                outbound=("done",),
                raw={},
            ),
            "done": WorkflowStep(
                step_id="done",
                kind=StepKind.TERMINAL,
                uses=None,
                side_effect_class=None,
                approval=None,
                next_step=None,
                branches={},
                outbound=(),
                raw={},
            ),
        },
        connectors={
            "telegram": ConnectorCapability(
                connector_id="telegram",
                adapter="direct_api",
                actions={"send_message": action},
                raw={},
            )
        },
        approvals={
            "admin_approval": ApprovalRequirement(
                approval_id="admin_approval",
                required_for=("notify_admin",),
                mode="explicit",
                raw={},
            )
        },
        design=WorkflowDesignContract(
            intent="test",
            trigger="manual",
            inputs=(),
            outputs=(),
            assumptions=(),
            side_effects=("send",),
            approval_gates=("admin_approval",),
            failure_modes=(),
            rollback=(),
        ),
    )
