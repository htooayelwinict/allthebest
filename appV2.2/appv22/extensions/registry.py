from __future__ import annotations

from dataclasses import dataclass

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.state.models import AgentState


@dataclass(frozen=True)
class ResolvedExtensions:
    extension_ids: list[str]
    skill_cards: list[SkillCard]
    tool_ids: list[str]
    planner_ids: list[str]
    mutation_policy_ids: list[str]
    mutation_executor_ids: list[str]
    verifier_ids: list[str]
    artifact_schema_ids: list[str]


class ExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, RuntimeExtension] = {}

    def register(self, extension: RuntimeExtension) -> None:
        self._extensions[extension.extension_id] = extension

    def resolve_active(self, state: AgentState) -> ResolvedExtensions:
        cards = [
            card
            for extension in self._extensions.values()
            for card in extension.skill_cards()
            if card.activates_for(state)
        ]
        return ResolvedExtensions(
            extension_ids=sorted({card.extension_id for card in cards}),
            skill_cards=cards,
            tool_ids=sorted({tool_id for card in cards for tool_id in card.tool_ids}),
            planner_ids=sorted({card.planner_id for card in cards}),
            mutation_policy_ids=sorted({card.mutation_policy_id for card in cards}),
            mutation_executor_ids=sorted({card.mutation_executor_id for card in cards}),
            verifier_ids=sorted({card.verifier_id for card in cards}),
            artifact_schema_ids=sorted(
                {schema_id for card in cards for schema_id in card.artifact_schema_ids}
            ),
        )
