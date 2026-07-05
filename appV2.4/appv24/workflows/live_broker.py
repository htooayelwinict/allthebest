"""Live broker contracts for workflow runs.

Tests use injected fake transports. Real provider transports remain manual-gated.
"""

from __future__ import annotations

from typing import Protocol

from appv24.workflows.broker import BrokerRequest, BrokerResult


class LiveWorkflowTransport(Protocol):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        raise NotImplementedError


class LiveWorkflowBroker:
    def __init__(self, transports: dict[str, LiveWorkflowTransport], *, mode: str):
        self._transports = dict(transports)
        self._mode = mode

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if self._mode == "live-production":
            return BrokerResult(
                status="blocked",
                provider_id="live",
                outputs={},
                issues=("live-production broker is disabled by default",),
            )
        transport = self._transports.get(request.connector_action)
        if transport is None:
            return BrokerResult(
                status="blocked",
                provider_id="live",
                outputs={},
                issues=(f"live action is not allowlisted: {request.connector_action}",),
            )
        result = transport.invoke(request)
        allowed = set(request.allowed_outputs)
        return BrokerResult(
            status=result.status,
            provider_id=result.provider_id,
            outputs={key: value for key, value in result.outputs.items() if key in allowed},
            issues=result.issues,
        )
