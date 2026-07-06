from __future__ import annotations

from appv24.workflows.approval_ledger import (
    ApprovalLedgerEvent,
    build_approval_plan,
    decode_ledger_event,
    encode_ledger_event,
)
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples


def test_approval_plan_lists_mutating_steps_with_idempotency() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None

    plan = build_approval_plan(compiled.ir)

    assert tuple(item.step_id for item in plan.items) == ("create_ticket_draft", "notify_admin")
    assert all(item.approval_id == "admin_approval" for item in plan.items)
    assert all(item.idempotency_key_fields for item in plan.items)


def test_approval_ledger_event_round_trip_jsonl() -> None:
    event = ApprovalLedgerEvent(
        event_id="evt-1",
        workflow_name="telegram_support_ticket",
        fixture_name="happy_path.yaml",
        step_id="notify_admin",
        action="telegram.send_message",
        status="planned",
        approval_id="admin_approval",
        idempotency_key="telegram_support_ticket:notify_admin:msg-1",
        issues=(),
    )

    line = encode_ledger_event(event)
    decoded = decode_ledger_event(line)

    assert decoded == event
