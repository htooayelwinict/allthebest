"""Planner selection logic."""

from __future__ import annotations

from app.planner.base import BasePlanner
from app.planner.planners import (
    CodePlanner,
    DirectPlanner,
    FallbackPlanner,
    InfraPlanner,
    ResearchPlanner,
)
from app.schemas import Envelope


class PlannerSelector:
    """Deterministically selects a planner implementation."""

    def __init__(self) -> None:
        self._direct = DirectPlanner()
        self._code = CodePlanner()
        self._research = ResearchPlanner()
        self._infra = InfraPlanner()
        self._fallback = FallbackPlanner()
        self._registry: dict[str, BasePlanner] = {
            "direct_planner": self._direct,
            "direct": self._direct,
            "code_planner": self._code,
            "code": self._code,
            "research_planner": self._research,
            "research": self._research,
            "infra_planner": self._infra,
            "infra": self._infra,
            "fallback_planner": self._fallback,
            "fallback": self._fallback,
        }

    def select(self, envelope: Envelope) -> BasePlanner:
        if envelope.planner_hint and envelope.planner_confidence >= 0.70:
            planner = self._registry.get(envelope.planner_hint)
            if planner is not None:
                return planner

        if "observe_first" in envelope.intents or "observe_first_required" in envelope.execution_hints:
            return self._fallback

        if envelope.input_type == "question" and not envelope.artifacts:
            return self._direct

        if envelope.input_type == "ambiguous_request" or envelope.confidence < 0.55:
            return self._fallback

        if (
            "code.fix" in envelope.intents
            or ("code" in envelope.domains and "file_mutation" in envelope.risks)
        ):
            return self._code

        if any(intent.startswith("research.") for intent in envelope.intents) or "research" in envelope.domains:
            return self._research

        if any(intent.startswith("infra.") for intent in envelope.intents) or "infra" in envelope.domains:
            return self._infra

        return self._fallback
