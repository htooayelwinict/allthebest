"""Decomposer extension for AppV2.1."""

from __future__ import annotations

from appv21.state.models import RequestEnvelope


class DecomposerExtension:
    def decompose(self, request: RequestEnvelope) -> dict:
        text = request.user_goal.lower()
        return {
            "intent": "file_workspace_cleanup" if any(word in text for word in ("organize", "cleanup", "move", "workspace")) else "general_task",
            "constraints": request.constraints,
            "ambiguity": [],
        }
