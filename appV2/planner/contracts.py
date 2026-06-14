"""Planner prompt-chain contracts."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from appV2.schemas import ArtifactContract, PhaseName


class PlannerModelClient(Protocol):
    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        """Return JSON for a planner stage."""


class PhaseSkeleton(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str
    strategy: str
    phases: list[PhaseName]


class ArtifactContractBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_contracts: list[ArtifactContract] = Field(default_factory=list)
    global_invariants: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
