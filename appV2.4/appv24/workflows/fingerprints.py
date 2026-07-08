"""Deterministic workflow package fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from appv24.workflows.loader import PackageDocuments


def fingerprint_package_documents(documents: PackageDocuments) -> str:
    payload = {
        "workflow": _normalize(documents.workflow),
        "connectors": _normalize(documents.connectors),
        "permissions": _normalize(documents.permissions),
        "middlewares": _normalize(documents.middlewares),
        "dry_run_fixtures": _normalize(documents.dry_run_fixtures),
        "rollback_text": documents.rollback_text or "",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    return value
