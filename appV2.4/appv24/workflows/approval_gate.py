"""Human approval gate contracts for workflow runs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping
from uuid import uuid4

ApprovalIndexKey = tuple[str, str, str | None]


@dataclass(frozen=True)
class ApprovalRequest:
    approval_request_id: str
    run_id: str
    package_fingerprint: str
    step_id: str
    connector_action: str
    approval_id: str
    allowed_outputs: tuple[str, ...]
    idempotency_key_fields: tuple[str, ...]
    idempotency_key: str | None = None
    payload_hash: str | None = None
    payload_preview_path: str | None = None
    status: str = "pending"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    package_fingerprint: str
    decided_by: str


def create_approval_request(
    *,
    run_id: str,
    package_fingerprint: str,
    step_id: str,
    connector_action: str,
    approval_id: str,
    allowed_outputs: tuple[str, ...],
    idempotency_key_fields: tuple[str, ...],
    idempotency_key: str | None = None,
    payload_hash: str | None = None,
    payload_preview_path: str | None = None,
) -> ApprovalRequest:
    return ApprovalRequest(
        approval_request_id=f"approval-{uuid4().hex}",
        run_id=run_id,
        package_fingerprint=package_fingerprint,
        step_id=step_id,
        connector_action=connector_action,
        approval_id=approval_id,
        allowed_outputs=allowed_outputs,
        idempotency_key_fields=idempotency_key_fields,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        payload_preview_path=payload_preview_path,
    )


def resume_approval(request: ApprovalRequest, decision: ApprovalDecision) -> ApprovalRequest:
    if request.package_fingerprint != decision.package_fingerprint:
        raise ValueError("approval package fingerprint is stale")
    return replace(request, status="approved" if decision.approved else "denied")


def build_payload_bound_approval_index(
    decisions: tuple[Mapping[str, Any], ...],
    *,
    package_fingerprint: str,
    expected_payload_hashes: Mapping[ApprovalIndexKey, str],
) -> tuple[dict[ApprovalIndexKey, bool], tuple[str, ...]]:
    latest_decisions: dict[ApprovalIndexKey, Mapping[str, Any]] = {}
    for decision in decisions:
        if decision.get("package_fingerprint") != package_fingerprint:
            continue
        step_id = decision.get("step_id")
        connector_action = decision.get("connector_action")
        if not isinstance(step_id, str) or not isinstance(connector_action, str):
            continue
        idempotency_key = decision.get("idempotency_key")
        key = (step_id, connector_action, idempotency_key if isinstance(idempotency_key, str) else None)
        latest_decisions[key] = decision

    approved: dict[ApprovalIndexKey, bool] = {}
    issues: list[str] = []
    for key, decision in latest_decisions.items():
        if decision.get("approved") is not True or decision.get("status") != "approved":
            approved[key] = False
            continue
        expected_payload_hash = expected_payload_hashes.get(key)
        if expected_payload_hash is None:
            approved[key] = False
            continue
        payload_hash = decision.get("payload_hash")
        if not isinstance(payload_hash, str):
            approved[key] = False
            issues.append(f"approval payload hash is missing for {key[0]}")
            continue
        if payload_hash != expected_payload_hash:
            approved[key] = False
            issues.append(f"approval payload hash is stale for {key[0]}")
            continue
        approved[key] = True
    return approved, tuple(dict.fromkeys(issues))
