"""Provider protocol for AppV2.1 model turns."""

from __future__ import annotations

from typing import Protocol

from appv21.runtime.decisions import RuntimeDecision


class AgentProvider(Protocol):
    provider_id: str

    def decide(self, prompt_payload: dict) -> RuntimeDecision: ...
