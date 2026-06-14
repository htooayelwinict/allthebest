"""Prompt/context assembly contract for AppV2.1."""

from __future__ import annotations

from typing import Any

from appv21.state.models import AgentState


class PromptBuilder:
    """Builds layered agent prompt payloads without owning state transitions."""

    def build(
        self,
        *,
        state: AgentState,
        turn_context: dict[str, Any],
        active_skills: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "state": {
                "mode": state.mode,
                "plan": state.plan.__dict__ if state.plan is not None else None,
                "artifacts": list(state.world.artifacts),
                "mutation_receipts": list(state.world.mutation_receipts),
                "verification_receipts": list(state.world.verification_receipts),
                "pauses": [pause.__dict__ for pause in state.pauses],
                "terminal": state.terminal,
            },
            "system": {
                "identity": "AppV2.1 runtime-first coding agent",
                "contract": [
                    "Conversation enters the agent loop before planning.",
                    "Planner may only plan from runtime-observed world refs.",
                    "All writes require a runtime-issued mutation lease.",
                    "Runtime-verified artifacts require evidence references.",
                ],
            },
            "agent": {
                "mode": state.mode,
                "request": state.request.user_goal,
                "constraints": state.request.constraints,
            },
            "skills": active_skills,
            "world": turn_context,
            "decomposition": turn_context.get("decomposition", {}),
            "tools": tool_specs,
            "output_contract": {
                "allowed_decisions": ["observe", "read_file", "plan", "tool_call", "mutation_intent", "verify", "compact", "pause", "finalize"],
                "write_boundary": "MutationLease",
                "artifact_boundary": "ArtifactValidator",
            },
        }
