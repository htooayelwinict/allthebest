"""Local-only webhook ingress validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from appv24.workflows.activation import WorkflowActivationRecord
from appv24.workflows.run_store import SECRET_VALUE_RE


@dataclass(frozen=True)
class WebhookIngressDecision:
    status: str
    reason: str
    activation_id: str
    replay_key: str | None
    event: Mapping[str, object]


class WebhookReplayStore:
    def __init__(self, root: Path):
        self.root = root

    def record_once(self, replay_key: str) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{_digest(replay_key)}.json"
        if path.exists():
            return False
        payload = {"replay_key": replay_key}
        encoded = json.dumps(payload, sort_keys=True)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("webhook replay key contains a raw secret-looking value")
        path.write_text(encoded + "\n", encoding="utf-8")
        return True


def handle_local_webhook(
    activation: WorkflowActivationRecord,
    *,
    payload: bytes,
    headers: Mapping[str, str],
    shared_secret: str,
    replay_store: WebhookReplayStore,
) -> WebhookIngressDecision:
    if activation.trigger_type != "webhook":
        return _blocked(activation, "activation is not webhook-triggered", replay_key=None)
    if not activation.enabled:
        return _blocked(activation, "activation disabled", replay_key=None)
    if headers.get("x-appv24-webhook-secret") != shared_secret:
        return _blocked(activation, "webhook secret mismatch", replay_key=None)

    replay_key = headers.get("x-appv24-replay-key") or _digest_bytes(payload)
    if not replay_store.record_once(replay_key):
        return _blocked(activation, "duplicate webhook replay key", replay_key=replay_key)

    event = {
        "activation_id": activation.activation_id,
        "trigger_type": activation.trigger_type,
        "package_fingerprint": activation.package_fingerprint,
        "replay_key": replay_key,
        "payload_sha256": _digest_bytes(payload),
    }
    return WebhookIngressDecision(
        status="accepted",
        reason="webhook accepted",
        activation_id=activation.activation_id,
        replay_key=replay_key,
        event=event,
    )


def _blocked(activation: WorkflowActivationRecord, reason: str, *, replay_key: str | None) -> WebhookIngressDecision:
    return WebhookIngressDecision(
        status="blocked",
        reason=reason,
        activation_id=activation.activation_id,
        replay_key=replay_key,
        event={},
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
