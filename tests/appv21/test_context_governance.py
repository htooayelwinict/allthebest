from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.context.budget import ContextBudgetManager, DEFAULT_SECTION_BUDGETS


def test_context_budget_estimates_section_sizes() -> None:
    payload = {
        "system": {"role": "architect"},
        "agent": ["planner", "executor"],
        "untracked": "ignored for section budgets",
    }

    estimate = ContextBudgetManager().estimate(payload)

    expected_system_chars = len(json.dumps(payload["system"], sort_keys=True, default=str))
    expected_agent_chars = len(json.dumps(payload["agent"], sort_keys=True, default=str))
    expected_total_chars = len(json.dumps(payload, sort_keys=True, default=str))

    assert estimate["total_chars"] == expected_total_chars
    assert estimate["sections"]["system"] == {
        "chars": expected_system_chars,
        "budget": DEFAULT_SECTION_BUDGETS["system"],
        "over_budget": False,
    }
    assert estimate["sections"]["agent"] == {
        "chars": expected_agent_chars,
        "budget": DEFAULT_SECTION_BUDGETS["agent"],
        "over_budget": False,
    }
    assert estimate["sections"]["skills"]["chars"] == 0
    assert estimate["over_budget_sections"] == []


def test_context_budget_marks_over_budget_sections() -> None:
    manager = ContextBudgetManager(
        section_budgets={
            **DEFAULT_SECTION_BUDGETS,
            "system": 2,
            "tools": 3,
        }
    )
    payload = {
        "system": "abcd",
        "tools": {"names": ["search"]},
        "world": "within",
    }

    estimate = manager.estimate(payload)

    assert estimate["sections"]["system"]["over_budget"] is True
    assert estimate["sections"]["tools"]["over_budget"] is True
    assert estimate["sections"]["world"]["over_budget"] is False
    assert estimate["over_budget_sections"] == ["system", "tools"]
