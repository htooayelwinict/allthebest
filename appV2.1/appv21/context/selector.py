"""Mode-aware prompt context selection for AppV2.1."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from appv21.state.models import AgentState, WorldRef


READ_ONLY_TOOL_NAMES = frozenset({"repo_snapshot", "read_file"})
NO_DEFAULT_TOOL_MODES = frozenset({"PLAN", "ACT", "FINALIZE"})
READ_TOOL_MODES = frozenset({"START", "THINK", "OBSERVE", "VERIFY"})


@dataclass(frozen=True)
class ContextSelector:
    """Selects compact, mode-scoped context for a model turn."""

    max_world_refs: int = 6

    def select(
        self,
        state: AgentState,
        active_skills: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        world_refs = self._select_world_refs(state)
        selected_tools = self._select_tool_specs(state.mode, tool_specs)
        selected_skills = self._select_skills(state.mode, active_skills)

        return {
            "state": self._state_card(state),
            "world": {"world_refs": world_refs},
            "skills": selected_skills,
            "tools": selected_tools,
            "selection": {
                "mode": state.mode,
                "selected_world_refs": [ref["ref_id"] for ref in world_refs],
                "selected_tools": [tool["name"] for tool in selected_tools if "name" in tool],
                "selected_skills": [self._skill_name(skill) for skill in selected_skills],
            },
        }

    def _state_card(self, state: AgentState) -> dict[str, Any]:
        return {
            "mode": state.mode,
            "request": {
                "request_id": state.request.request_id,
                "user_goal": state.request.user_goal,
                "root_path": state.request.root_path,
                "constraints": list(state.request.constraints),
            },
            "plan": deepcopy(state.plan.__dict__) if state.plan is not None else None,
            "artifacts": {artifact_id: asdict(artifact) for artifact_id, artifact in state.world.artifacts.items()},
            "mutation_leases": {lease_id: asdict(lease) for lease_id, lease in state.world.mutation_leases.items()},
            "mutation_receipts": {receipt_id: asdict(receipt) for receipt_id, receipt in state.world.mutation_receipts.items()},
            "verification_receipts": deepcopy(state.world.verification_receipts),
            "pauses": [asdict(pause) for pause in state.pauses],
            "terminal": state.terminal,
        }

    def _select_world_refs(self, state: AgentState) -> list[dict[str, str]]:
        refs = list(state.world.refs.values())
        repo_snapshot_refs = [ref for ref in refs if self._world_ref_kind(ref) == "repo_snapshot"]
        selected_by_id = {ref.ref_id: ref for ref in repo_snapshot_refs}

        latest_refs = [ref for ref in refs[-self.max_world_refs :] if ref.ref_id not in selected_by_id]
        selected = [*repo_snapshot_refs, *latest_refs]

        return [self._world_ref_card(ref) for ref in selected]

    def _world_ref_card(self, ref: WorldRef) -> dict[str, str]:
        return {
            "ref_id": ref.ref_id,
            "kind": self._world_ref_kind(ref),
            "summary": ref.summary,
            "trust": ref.trust,
        }

    def _world_ref_kind(self, ref: WorldRef) -> str:
        if ref.kind == "tool_result" and ref.payload.get("tool_name") == "repo_snapshot":
            return "repo_snapshot"
        return ref.kind

    def _select_tool_specs(self, mode: str, tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed_names = self._allowed_tool_names(mode)
        return [deepcopy(spec) for spec in tool_specs if spec.get("name") in allowed_names]

    def _allowed_tool_names(self, mode: str) -> frozenset[str]:
        if mode in NO_DEFAULT_TOOL_MODES:
            return frozenset()
        if mode in READ_TOOL_MODES:
            return READ_ONLY_TOOL_NAMES
        return frozenset()

    def _select_skills(self, mode: str, active_skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            deepcopy(skill)
            for skill in active_skills
            if not skill.get("modes") or mode in set(skill.get("modes") or [])
        ]

    def _skill_name(self, skill: dict[str, Any]) -> str:
        return str(skill.get("skill_id") or skill.get("name") or skill.get("id") or "")
