from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


DEFAULT_SECTION_BUDGETS = {
    "system": 8000,
    "agent": 12000,
    "skills": 10000,
    "tools": 12000,
    "world": 20000,
    "state": 16000,
    "output_contract": 6000,
    "decomposition": 8000,
}


def _default_section_budgets() -> dict[str, int]:
    return DEFAULT_SECTION_BUDGETS.copy()


def _estimate_chars(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, default=str))


@dataclass(frozen=True)
class ContextBudgetManager:
    section_budgets: dict[str, int] = field(default_factory=_default_section_budgets)

    def estimate(self, payload: dict[str, Any]) -> dict[str, Any]:
        sections: dict[str, dict[str, Any]] = {}
        over_budget_sections: list[str] = []

        for section_name, budget in self.section_budgets.items():
            chars = _estimate_chars(payload[section_name]) if section_name in payload else 0
            over_budget = chars > budget
            sections[section_name] = {
                "chars": chars,
                "budget": budget,
                "over_budget": over_budget,
            }
            if over_budget:
                over_budget_sections.append(section_name)

        return {
            "total_chars": _estimate_chars(payload),
            "sections": sections,
            "over_budget_sections": over_budget_sections,
        }
