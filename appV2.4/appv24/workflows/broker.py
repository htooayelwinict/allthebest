"""No-side-effect local broker for appv24 workflow simulation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BrokerRequest:
    step_id: str
    connector_action: str
    input_ref: Mapping[str, Any]
    fixture: Mapping[str, Any]
    allowed_outputs: tuple[str, ...]
    dry_run: bool = True


@dataclass(frozen=True)
class BrokerResult:
    status: str
    provider_id: str
    outputs: Mapping[str, Any]
    issues: tuple[str, ...] = ()


BrokerProvider = Callable[[BrokerRequest], BrokerResult]


class LocalWorkflowBroker:
    """Routes IR action steps to deterministic dry-run providers only."""

    def __init__(self, providers: Mapping[str, BrokerProvider]):
        self._providers = dict(providers)

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if not request.dry_run:
            return BrokerResult(
                status="blocked",
                provider_id="local",
                outputs={},
                issues=("broker only supports dry-run requests",),
            )
        provider = self._providers.get(request.connector_action)
        if provider is None:
            return BrokerResult(
                status="blocked",
                provider_id="local",
                outputs={},
                issues=(f"no dry-run provider registered for {request.connector_action}",),
            )
        result = provider(request)
        allowed = set(request.allowed_outputs)
        filtered = {key: value for key, value in result.outputs.items() if key in allowed}
        return BrokerResult(
            status=result.status,
            provider_id=result.provider_id,
            outputs=filtered,
            issues=result.issues,
        )
