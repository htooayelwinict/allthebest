"""Human approval gate contracts for workflow runs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4


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
    )


def resume_approval(request: ApprovalRequest, decision: ApprovalDecision) -> ApprovalRequest:
    if request.package_fingerprint != decision.package_fingerprint:
        raise ValueError("approval package fingerprint is stale")
    return replace(request, status="approved" if decision.approved else "denied")
