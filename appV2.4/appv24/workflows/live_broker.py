"""Live broker contracts for workflow runs.

Tests use injected fake transports. Real provider transports remain manual-gated.
"""

from __future__ import annotations

from typing import Protocol

from appv24.workflows.broker import BrokerRequest, BrokerResult, filter_broker_outputs

LIVE_WORKFLOW_BROKER_MODES = frozenset(("live-read", "live-draft", "live-production"))


class LiveWorkflowTransport(Protocol):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        raise NotImplementedError


class LiveWorkflowBroker:
    def __init__(self, transports: dict[str, LiveWorkflowTransport], *, mode: str, production_enabled: bool = False):
        if mode not in LIVE_WORKFLOW_BROKER_MODES:
            raise ValueError(f"unknown live workflow broker mode: {mode}")
        if production_enabled and mode != "live-production":
            raise ValueError("production_enabled is only valid for live-production broker mode")
        self._transports = dict(transports)
        self._mode = mode
        self._production_enabled = production_enabled

    @property
    def mode(self) -> str:
        return self._mode

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if self._mode == "live-production" and not self._production_enabled:
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
                issues=(f"live transport is not configured for {request.connector_action}",),
            )
        result = transport.invoke(request)
        if result.status == "blocked":
            return BrokerResult(
                status="blocked",
                provider_id=result.provider_id,
                outputs={},
                issues=result.issues,
            )
        if result.status != "blocked" and result.status != self._mode:
            return BrokerResult(
                status="blocked",
                provider_id=result.provider_id,
                outputs={},
                issues=(
                    *result.issues,
                    f"live transport for {request.connector_action} returned status {result.status}; expected {self._mode}",
                ),
            )
        return filter_broker_outputs(request, result)
