from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ContextEvidence:
    refs: tuple[str, ...]
    kinds: tuple[str, ...]

    @classmethod
    def from_prompt(cls, prompt: dict[str, Any]) -> ContextEvidence:
        refs: list[str] = []
        kinds: list[str] = []

        world = prompt.get("world")
        if isinstance(world, dict):
            world_refs = world.get("world_refs")
            if isinstance(world_refs, dict):
                for key, payload in world_refs.items():
                    if not isinstance(payload, dict):
                        continue

                    ref_id = payload.get("ref_id")
                    if not isinstance(ref_id, str):
                        ref_id = key if isinstance(key, str) else None
                    if isinstance(ref_id, str) and ref_id not in refs:
                        refs.append(ref_id)

                    kind = payload.get("kind")
                    if isinstance(kind, str) and kind not in kinds:
                        kinds.append(kind)

        messages = prompt.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                summary = message.get("summary")
                cls._collect_summary_refs(summary, refs)

        state = prompt.get("state")
        if isinstance(state, dict):
            cls._collect_summary_refs(state.get("context_summary"), refs)

        return cls(refs=tuple(refs), kinds=tuple(kinds))

    def has_ref(self, ref: str) -> bool:
        return ref in self.refs

    def has_kind(self, kind: str) -> bool:
        return kind in self.kinds

    def has_any_ref(self, refs: Iterable[str]) -> bool:
        return any(self.has_ref(ref) for ref in refs)

    def has_any_kind(self, kinds: Iterable[str]) -> bool:
        return any(self.has_kind(kind) for kind in kinds)

    @staticmethod
    def _collect_summary_refs(summary: Any, refs: list[str]) -> None:
        if not isinstance(summary, dict):
            return

        evidence_refs = summary.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            return

        for ref in evidence_refs:
            if isinstance(ref, str) and ref not in refs:
                refs.append(ref)
