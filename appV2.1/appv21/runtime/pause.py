"""Pause/resume helpers for AppV2.1."""

from __future__ import annotations

from uuid import uuid4

from appv21.state.models import PauseState


def create_pause(*, pause_type: str, summary: str, options: list[dict] | None = None) -> PauseState:
    return PauseState(
        pause_id=f"pause_{uuid4().hex}",
        pause_type=pause_type,
        summary=summary,
        options=list(options or []),
    )
