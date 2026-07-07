from __future__ import annotations

from appv24.workflows import idempotency as idempotency_module
from appv24.workflows.idempotency import IdempotencyStore
from appv24.workflows.idempotency import build_idempotency_key


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


def test_idempotency_key_resolves_input_refs_from_selected_fixture() -> None:
    raw_step = {
        "with": {
            "chat_id": {"inputRef": "chat_id"},
            "text": {"inputRef": "text"},
        }
    }

    first = build_idempotency_key(
        package_fingerprint="pkg-1",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        key_fields=("chat_id", "text"),
        input_ref=raw_step,
        fixture={"input": {"chat_id": "admin-a", "text": "Production alert A."}},
    )
    second = build_idempotency_key(
        package_fingerprint="pkg-1",
        step_id="notify_admin",
        connector_action="telegram.send_message",
        key_fields=("chat_id", "text"),
        input_ref=raw_step,
        fixture={"input": {"chat_id": "admin-b", "text": "Production alert B."}},
    )

    assert first is not None
    assert second is not None
    assert first.startswith("idemp:")
    assert second.startswith("idemp:")
    assert first != second


def test_persistent_idempotency_store_reuses_committed_result_across_instances(tmp_path) -> None:
    assert hasattr(idempotency_module, "PersistentIdempotencyStore")
    PersistentIdempotencyStore = idempotency_module.PersistentIdempotencyStore
    path = tmp_path / "idempotency.jsonl"
    first_store = PersistentIdempotencyStore(path)
    second_store = PersistentIdempotencyStore(path)

    first = first_store.record_or_reuse("key-1", {"message_id": 10}, payload_hash="payload-a")
    second = second_store.record_or_reuse("key-1", {"message_id": 11}, payload_hash="payload-a")

    assert first == {"message_id": 10}
    assert second == {"message_id": 10}


def test_persistent_idempotency_store_blocks_conflicting_payload_across_instances(tmp_path) -> None:
    assert hasattr(idempotency_module, "PersistentIdempotencyStore")
    PersistentIdempotencyStore = idempotency_module.PersistentIdempotencyStore
    path = tmp_path / "idempotency.jsonl"
    PersistentIdempotencyStore(path).record_or_reuse("key-1", {"message_id": 10}, payload_hash="payload-a")

    try:
        PersistentIdempotencyStore(path).record_or_reuse("key-1", {"message_id": 11}, payload_hash="payload-b")
    except ValueError as error:
        assert str(error) == "idempotency key conflict for key-1"
    else:
        raise AssertionError("expected idempotency conflict")


def test_persistent_idempotency_store_rejects_raw_secret_looking_result(tmp_path) -> None:
    assert hasattr(idempotency_module, "PersistentIdempotencyStore")
    PersistentIdempotencyStore = idempotency_module.PersistentIdempotencyStore
    store = PersistentIdempotencyStore(tmp_path / "idempotency.jsonl")

    try:
        store.record_or_reuse("key-1", {"token": "sk-or-v1-secret-looking-value"}, payload_hash="payload-a")
    except ValueError as error:
        assert str(error) == "idempotency result contains a raw secret-looking value"
    else:
        raise AssertionError("expected raw secret-looking result to be rejected")
