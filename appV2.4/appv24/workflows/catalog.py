"""Built-in workflow connector action catalog for appv24 policy checks."""

from __future__ import annotations

BUILTIN_WORKFLOW_ACTIONS = frozenset(
    {
        "github.create_issue",
        "github.list_dependabot_alerts",
        "gmail.create_draft",
        "telegram.send_message",
        "ticket.create_draft",
    }
)


def is_builtin_workflow_action(action_ref: str) -> bool:
    return action_ref in BUILTIN_WORKFLOW_ACTIONS
