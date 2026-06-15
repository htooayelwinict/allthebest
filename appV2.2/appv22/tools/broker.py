from __future__ import annotations

from copy import deepcopy
from itertools import count
from pathlib import Path
from typing import Any

from appv22.tools.registry import ToolRegistry


class ToolBroker:
    def __init__(self, *, registry: ToolRegistry, root_path: str | Path) -> None:
        self.registry = registry
        self.root_path = Path(root_path).resolve()
        self._result_counter = count(1)

    def execute(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        active_tool_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str],
    ) -> dict[str, Any]:
        if tool_id not in set(active_tool_ids):
            return self._envelope(
                tool_id,
                "denied",
                {"errors": [f"inactive_tool:{tool_id}"]},
                create_ref=False,
            )

        definition = self.registry.definition(tool_id)
        handler = self.registry.handler(tool_id)
        if definition is None or handler is None:
            return self._envelope(
                tool_id,
                "denied",
                {"errors": [f"unknown_tool:{tool_id}"]},
                create_ref=False,
            )

        required_args = definition.argument_schema.get("required", ())
        errors = [f"missing_argument:{key}" for key in required_args if key not in arguments]
        if errors:
            return self._envelope(tool_id, "denied", {"errors": errors}, create_ref=False)

        handler_result = handler(deepcopy(arguments), {"root_path": self.root_path})
        status = str(handler_result.get("status", "completed"))
        payload = {key: deepcopy(value) for key, value in handler_result.items() if key != "status"}
        return self._envelope(tool_id, status, payload, create_ref=status == "completed")

    def _envelope(
        self,
        tool_id: str,
        status: str,
        payload: dict[str, Any],
        *,
        create_ref: bool,
    ) -> dict[str, Any]:
        result_id = f"toolres_{next(self._result_counter):06d}"
        return {
            "tool_result_id": result_id,
            "tool_id": tool_id,
            "status": status,
            "payload": deepcopy(payload),
            "payload_ref": f"world://tool_payload/{result_id}" if create_ref else "",
            "evidence_refs": [],
        }
