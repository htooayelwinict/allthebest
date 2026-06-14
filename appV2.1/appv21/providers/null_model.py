"""Safe null provider for AppV2.1."""

from __future__ import annotations

from appv21.runtime.decisions import RuntimeDecision, pause_decision


class NullModelProvider:
    provider_id = "null-model"

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        return pause_decision(reason="No provider is configured for autonomous decisions.", payload={"pause_type": "missing_context"})
