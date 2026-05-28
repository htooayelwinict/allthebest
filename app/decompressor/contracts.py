"""Internal contracts for optional model-backed decompression.

The decompressor depends on this small protocol instead of provider SDKs. Unit
tests should satisfy it with fake clients and canned JSON; live provider setup
belongs outside this package.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class PromptChainModelClient(Protocol):
    """Minimal JSON-completion client accepted by the LLM prompt chain."""

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        """Return a JSON string matching the supplied stage schema."""


class NormalizedRequest(BaseModel):
    normalized_input: str
    user_goal: str | None = None
    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ArtifactExtraction(BaseModel):
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class RequestClassification(BaseModel):
    input_type: str
    intents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    budget_hint: str = "medium"
    confidence: float = 0.0


class RiskContextInference(BaseModel):
    risks: list[str] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    execution_hints: list[str] = Field(default_factory=list)
    ambiguity: list[str] = Field(default_factory=list)


class PlannerRecommendation(BaseModel):
    planner_hint: str | None = None
    planner_confidence: float = 0.0
    planner_alternatives: list[str] = Field(default_factory=list)
