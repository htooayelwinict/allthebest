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
        return filter_broker_outputs(request, result)


def filter_broker_outputs(request: BrokerRequest, result: BrokerResult) -> BrokerResult:
    """Apply the declared output allowlist and block full output mismatches."""

    allowed = set(request.allowed_outputs)
    filtered = {key: value for key, value in result.outputs.items() if key in allowed}
    if result.status != "blocked" and result.outputs and request.allowed_outputs and not filtered:
        return BrokerResult(
            status="blocked",
            provider_id=result.provider_id,
            outputs={},
            issues=(
                *result.issues,
                _output_mismatch_issue(
                    request.connector_action,
                    produced=result.outputs.keys(),
                    allowed=request.allowed_outputs,
                ),
            ),
        )
    return BrokerResult(
        status=result.status,
        provider_id=result.provider_id,
        outputs=filtered,
        issues=result.issues,
    )


def _output_mismatch_issue(connector_action: str, *, produced: object, allowed: object) -> str:
    return (
        f"provider outputs for {connector_action} do not match declared allowed_outputs: "
        f"produced {_format_items(produced)}; allowed {_format_items(allowed)}"
    )


def _format_items(items: object) -> str:
    values = sorted(str(item) for item in items)
    return ", ".join(values) if values else "(none)"
