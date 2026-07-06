"""Fixture-backed workflow connector sandbox providers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from appv24.workflows.broker import BrokerProvider, BrokerRequest, BrokerResult, filter_broker_outputs
from appv24.workflows.local_artifacts import LocalArtifactProviders


class ConnectorSandboxProviders:
    """Wrap local artifact providers with fixture-controlled connector behavior.

    The sandbox never calls external services. Fixtures may override a step by
    step id or connector action to simulate connector timeouts, malformed
    provider output, and other broker-level failures.
    """

    def __init__(self, artifact_root: Path):
        self._local = LocalArtifactProviders(artifact_root)

    def provider_map(self) -> dict[str, BrokerProvider]:
        return {
            action_ref: self._sandbox_provider(action_ref, provider)
            for action_ref, provider in self._local.provider_map().items()
        }

    def _sandbox_provider(self, action_ref: str, provider: BrokerProvider) -> BrokerProvider:
        def invoke(request: BrokerRequest) -> BrokerResult:
            failure = _sandbox_entry(request, "failures", action_ref)
            if failure is not None:
                return BrokerResult(
                    status=_string(failure.get("status")) or "blocked",
                    provider_id="connector_sandbox",
                    outputs={},
                    issues=(_string(failure.get("message")) or f"connector sandbox blocked {request.step_id}",),
                )

            outputs = _sandbox_entry(request, "outputs", action_ref)
            if outputs is not None:
                if not isinstance(outputs, Mapping):
                    return BrokerResult(
                        status="blocked",
                        provider_id="connector_sandbox",
                        outputs={},
                        issues=(f"connector sandbox outputs for {request.step_id} must be a mapping",),
                    )
                return filter_broker_outputs(
                    request,
                    BrokerResult(status="simulated", provider_id="connector_sandbox", outputs=dict(outputs)),
                )

            missing = _sandbox_entry(request, "missing_fields", action_ref)
            if missing is not None:
                fields = _string_list(missing)
                suffix = f": {', '.join(fields)}" if fields else ""
                return BrokerResult(
                    status="blocked",
                    provider_id="connector_sandbox",
                    outputs={},
                    issues=(f"connector sandbox missing required provider fields for {request.step_id}{suffix}",),
                )

            return provider(request)

        return invoke


def _sandbox_entry(request: BrokerRequest, section: str, action_ref: str) -> object | None:
    sandbox = request.fixture.get("connector_sandbox")
    if not isinstance(sandbox, Mapping):
        return None
    entries = sandbox.get(section)
    if not isinstance(entries, Mapping):
        return None
    if request.step_id in entries:
        return entries[request.step_id]
    return entries.get(action_ref)


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()
