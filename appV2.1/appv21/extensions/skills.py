"""Skill router for AppV2.1."""

from __future__ import annotations

from appv21.state.models import AgentState


class SkillRouter:
    def active_skills(self, state: AgentState) -> list[dict]:
        text = state.request.user_goal.lower()
        if any(word in text for word in ("cleanup", "organize", "move", "workspace")):
            return [
                {
                    "skill_id": "workspace_cleanup",
                    "tool_preferences": ["repo_snapshot"],
                    "artifact_templates": ["workspace_manifest"],
                    "prompt_patch": "Organize observed workspace files without moving held/test files.",
                }
            ]
        return []
