"""edit tool. Port of pi/packages/coding-agent/src/core/tools/edit.ts (string-replace)."""

from __future__ import annotations

import os

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.path_utils import resolve_to_cwd
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the file to edit"},
        "old_string": {"type": "string", "description": "Exact text to replace (must be unique)"},
        "new_string": {"type": "string", "description": "Replacement text"},
    },
    "required": ["path", "old_string", "new_string"],
}


def _execute_edit(cwd: str, tool_call_id, args, signal=None, on_update=None, ctx: ToolContext | None = None):
    path = args["path"]
    old_string = args["old_string"]
    new_string = args["new_string"]
    absolute_path = resolve_to_cwd(path, cwd)
    if not os.path.exists(absolute_path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(absolute_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    occurrences = content.count(old_string)
    if occurrences == 0:
        raise ValueError(f"old_string not found in {path}")
    if occurrences > 1:
        raise ValueError(f"old_string is not unique in {path} ({occurrences} matches); add surrounding context")
    updated = content.replace(old_string, new_string, 1)
    with open(absolute_path, "w", encoding="utf-8") as handle:
        handle.write(updated)
    return AgentToolResult(
        content=[TextContent(text=f"Edited {path}")],
        details={"path": absolute_path},
    )


def create_edit_tool_definition(cwd: str) -> ToolDefinition:
    return ToolDefinition(
        name="edit",
        label="edit",
        description="Replace a unique occurrence of old_string with new_string in a file.",
        parameters=EDIT_SCHEMA,
        prompt_snippet="Edit a file by replacing text",
        prompt_guidelines=["Use edit for targeted changes; old_string must be unique."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_edit(cwd, tid, args, signal, on_update, ctx),
        render_call=lambda args, ctx=None: f"edit {args.get('path', '')}",
    )


def create_edit_tool(cwd: str) -> AgentTool:
    return wrap_tool_definition(create_edit_tool_definition(cwd), lambda: ToolContext(cwd=cwd))
