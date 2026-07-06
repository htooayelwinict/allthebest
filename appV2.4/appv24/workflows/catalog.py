"""Built-in workflow connector action catalog for appv24 policy checks."""

from __future__ import annotations

from appv24.workflows.connector_packs import builtin_connector_catalog


BUILTIN_WORKFLOW_ACTIONS = frozenset(builtin_connector_catalog().action_refs)


def is_builtin_workflow_action(action_ref: str) -> bool:
    return action_ref in BUILTIN_WORKFLOW_ACTIONS
