"""Contracts used by the AppV2 decomposer prompt chain."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from appV2.schemas import ExactLiteral


class PromptChainModelClient(Protocol):
    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        """Return a JSON string for the requested prompt-chain stage."""


class EnvelopePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    literal_contract: list[ExactLiteral] = Field(default_factory=list)
