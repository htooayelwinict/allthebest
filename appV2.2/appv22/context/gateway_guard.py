from __future__ import annotations

from copy import deepcopy
from typing import Any

from appv22.context.budget import estimate_chars


def _middle_compaction_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    roles: dict[str, int] = {}
    evidence_refs: list[Any] = []
    preserved_notes: list[str] = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        roles[role] = roles.get(role, 0) + 1
        if message.get("role") == "tool" and message.get("tool_result_id"):
            evidence_refs.append(message["tool_result_id"])
        content = str(message.get("content", "")).strip()
        lowered = content.lower()
        if content and (
            message.get("role") == "user"
            and any(marker in lowered for marker in ("constraint", "instruction", "must", "should", "preserve", "never", "only"))
            or message.get("role") == "assistant"
            and ("decision:" in lowered or "rationale:" in lowered)
        ):
            preserved_notes.append(content[:160])

    return {
        "role": "system",
        "name": "context_guard_compaction",
        "content": "Middle context compacted by GatewayContextGuard.",
        "compaction": {
            "messages_compacted": len(messages),
            "roles": roles,
            "evidence_refs": evidence_refs,
            "preserved_notes": preserved_notes[:8],
        },
    }


class GatewayContextGuard:
    def __init__(self, *, max_chars: int, threshold: float = 0.85) -> None:
        self.max_chars = max_chars
        self.threshold = threshold

    def guard(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        guarded = deepcopy(messages)
        if estimate_chars(guarded) <= int(self.max_chars * self.threshold):
            return guarded

        for message in guarded[1:-1]:
            if message.get("role") == "tool" and len(str(message.get("content", ""))) > 1000:
                message["content"] = f"[pruned verbose tool result:{message.get('tool_result_id', 'unknown')}]"
        if estimate_chars(guarded) <= min(self.max_chars, int(self.max_chars * self.threshold)):
            return guarded
        if len(guarded) <= 2:
            return guarded

        compacted = [guarded[0], _middle_compaction_message(guarded[1:-1]), guarded[-1]]
        if estimate_chars(compacted) <= self.max_chars:
            return compacted

        compacted[1] = {
            "role": "system",
            "name": "context_guard_compaction",
            "content": f"Middle context compacted by GatewayContextGuard: {len(guarded[1:-1])} messages.",
        }
        return guarded
