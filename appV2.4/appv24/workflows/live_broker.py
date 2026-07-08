"""Live broker contracts for workflow runs.

Tests use injected fake transports. Real provider transports remain manual-gated.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Mapping, Protocol

from appv24.workflows.broker import BrokerRequest, BrokerResult, filter_broker_outputs
from appv24.workflows.connector_contracts import builtin_connector_contract_catalog

LIVE_WORKFLOW_BROKER_MODES = frozenset(("live-read", "live-draft", "live-production"))


class LiveWorkflowTransport(Protocol):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        raise NotImplementedError


class LiveWorkflowBroker:
    def __init__(
        self,
        transports: dict[str, LiveWorkflowTransport],
        *,
        mode: str,
        production_enabled: bool = False,
        external_actions: Collection[str] = (),
    ):
        if mode not in LIVE_WORKFLOW_BROKER_MODES:
            raise ValueError(f"unknown live workflow broker mode: {mode}")
        if production_enabled and mode != "live-production":
            raise ValueError("production_enabled is only valid for live-production broker mode")
        self._transports = dict(transports)
        self._mode = mode
        self._production_enabled = production_enabled
        self._external_actions = frozenset(external_actions)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def configured_actions(self) -> tuple[str, ...]:
        return tuple(sorted(self._transports))

    @property
    def external_actions(self) -> tuple[str, ...]:
        return tuple(sorted(self._external_actions))

    def with_external_actions(self, action_refs: Collection[str]) -> "LiveWorkflowBroker":
        return LiveWorkflowBroker(
            self._transports,
            mode=self._mode,
            production_enabled=self._production_enabled,
            external_actions=(*self._external_actions, *action_refs),
        )

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if self._mode == "live-production" and not self._production_enabled:
            return BrokerResult(
                status="blocked",
                provider_id="live",
                outputs={},
                issues=("live-production broker is disabled by default",),
            )
        contract_issue = _contract_input_issue(request, external_actions=self._external_actions)
        if contract_issue is not None:
            return BrokerResult(
                status="blocked",
                provider_id="live",
                outputs={},
                issues=(contract_issue,),
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


def _contract_input_issue(request: BrokerRequest, *, external_actions: Collection[str]) -> str | None:
    contract = builtin_connector_contract_catalog().get(request.connector_action)
    if contract is None:
        if request.connector_action in external_actions:
            return None
        return f"live connector action is not cataloged: {request.connector_action}"
    if contract.input_schema.get("additionalProperties") is not False:
        return None
    properties = contract.input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    with_map = request.input_ref.get("with")
    if not isinstance(with_map, Mapping):
        return None
    allowed_fields = {str(key) for key in properties}
    unsupported = sorted(str(key) for key in with_map if str(key) not in allowed_fields)
    if not unsupported:
        return None
    return f"{request.connector_action} input contains unsupported fields: {', '.join(unsupported)}"
