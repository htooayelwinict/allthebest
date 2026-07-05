from __future__ import annotations

from appv24.workflows.idempotency import IdempotencyStore


def test_idempotency_store_returns_existing_result_for_same_payload() -> None:
    store = IdempotencyStore()

    first = store.record_or_reuse("key-1", {"ticket_id": "ticket-1"}, payload_hash="payload-a")
    second = store.record_or_reuse("key-1", {"ticket_id": "ticket-2"}, payload_hash="payload-a")

    assert first == {"ticket_id": "ticket-1"}
    assert second == {"ticket_id": "ticket-1"}


def test_idempotency_store_blocks_conflicting_payload() -> None:
    store = IdempotencyStore()
    store.record_or_reuse("key-1", {"ticket_id": "ticket-1"}, payload_hash="payload-a")

    try:
        store.record_or_reuse("key-1", {"ticket_id": "ticket-2"}, payload_hash="payload-b")
    except ValueError as error:
        assert str(error) == "idempotency key conflict for key-1"
    else:
        raise AssertionError("expected idempotency conflict")
