"""In-memory workflow idempotency store for broker action tests."""

from __future__ import annotations

from typing import Mapping


class IdempotencyStore:
    def __init__(self):
        self._entries: dict[str, tuple[str, Mapping[str, object]]] = {}

    def record_or_reuse(
        self,
        key: str,
        result: Mapping[str, object],
        *,
        payload_hash: str,
    ) -> Mapping[str, object]:
        existing = self._entries.get(key)
        if existing is not None:
            existing_hash, existing_result = existing
            if existing_hash != payload_hash:
                raise ValueError(f"idempotency key conflict for {key}")
            return existing_result
        self._entries[key] = (payload_hash, dict(result))
        return result
