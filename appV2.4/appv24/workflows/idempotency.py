"""Workflow idempotency helpers and in-memory broker action store."""

from __future__ import annotations

import hashlib
import json
from typing import Any
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


def build_idempotency_key(
    *,
    package_fingerprint: str,
    step_id: str,
    connector_action: str,
    key_fields: tuple[str, ...],
    input_ref: Mapping[str, Any],
    fixture: Mapping[str, Any] | None = None,
) -> str | None:
    if not key_fields:
        return None
    with_values = input_ref.get("with")
    with_map = with_values if isinstance(with_values, Mapping) else {}
    material = {
        "package_fingerprint": package_fingerprint,
        "step_id": step_id,
        "connector_action": connector_action,
        "key_fields": [
            {"field": field, "value": _resolved_key_value(with_map.get(field), fixture)}
            for field in key_fields
        ],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return f"idemp:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _resolved_key_value(value: Any, fixture: Mapping[str, Any] | None) -> Any:
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        if isinstance(input_ref, str):
            fixture_input = fixture.get("input") if fixture is not None else None
            if isinstance(fixture_input, Mapping):
                return fixture_input.get(input_ref)
            return None
    return value
