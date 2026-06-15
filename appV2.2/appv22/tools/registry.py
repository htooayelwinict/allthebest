from __future__ import annotations

from collections.abc import Callable
from typing import Any

from appv22.tools.definitions import ToolDefinition

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.tool_id in self._definitions:
            raise ValueError(f"duplicate tool_id: {definition.tool_id}")
        self._definitions[definition.tool_id] = definition
        self._handlers[definition.tool_id] = handler

    def definition(self, tool_id: str) -> ToolDefinition | None:
        return self._definitions.get(tool_id)

    def handler(self, tool_id: str) -> ToolHandler | None:
        return self._handlers.get(tool_id)
