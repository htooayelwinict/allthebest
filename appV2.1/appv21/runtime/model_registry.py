"""Model/provider registry seam for AppV2.1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    provider: str
    role: str
    supports_tools: bool = True
    supports_streaming: bool = True


class ModelRegistry:
    """Keeps model choice outside the agent loop.

    The current probe is deterministic, but this seam prevents provider details
    from leaking into planner/verifier/extensions when live model turns are added.
    """

    def __init__(self, profiles: list[ModelProfile] | None = None) -> None:
        self._profiles = {profile.role: profile for profile in profiles or [ModelProfile("deterministic-runtime", "local", "agent")]}

    def for_role(self, role: str) -> ModelProfile:
        return self._profiles.get(role) or self._profiles["agent"]
