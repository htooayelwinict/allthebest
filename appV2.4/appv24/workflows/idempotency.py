"""Workflow idempotency helpers and in-memory broker action store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
from typing import Mapping

SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9_])(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")


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


class PersistentIdempotencyStore:
    """Append-only local idempotency ledger for brokered workflow actions."""

    def __init__(self, path: Path):
        self.path = path

    def record_or_reuse(
        self,
        key: str,
        result: Mapping[str, object],
        *,
        payload_hash: str,
    ) -> Mapping[str, object]:
        existing = self.reuse_committed(key, payload_hash=payload_hash)
        if existing is not None:
            return existing

        payload = {"key": key, "payload_hash": payload_hash, "result": dict(result), "status": "committed"}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("idempotency result contains a raw secret-looking value")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        return result

    def reuse_committed(self, key: str, *, payload_hash: str) -> Mapping[str, object] | None:
        for entry in self._read_entries():
            if entry.get("key") != key:
                continue
            if entry.get("payload_hash") != payload_hash:
                raise ValueError(f"idempotency key conflict for {key}")
            if entry.get("status") != "committed":
                continue
            stored_result = entry.get("result")
            return stored_result if isinstance(stored_result, Mapping) else {}
        return None

    def _read_entries(self) -> tuple[Mapping[str, object], ...]:
        if not self.path.is_file():
            return ()
        entries: list[Mapping[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, Mapping):
                entries.append(parsed)
        return tuple(entries)


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


def build_idempotency_payload_hash(
    *,
    input_ref: Mapping[str, Any],
    fixture: Mapping[str, Any] | None = None,
) -> str:
    fixture_input = fixture.get("input") if isinstance(fixture, Mapping) else None
    material = {
        "input_ref": input_ref,
        "fixture_input": fixture_input if isinstance(fixture_input, Mapping) else {},
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return f"payload:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _resolved_key_value(value: Any, fixture: Mapping[str, Any] | None) -> Any:
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        if isinstance(input_ref, str):
            fixture_input = fixture.get("input") if fixture is not None else None
            if isinstance(fixture_input, Mapping):
                return fixture_input.get(input_ref)
            return None
    return value
