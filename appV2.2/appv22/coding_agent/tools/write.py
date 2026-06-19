"""write tool. Port of pi/packages/coding-agent/src/core/tools/write.ts."""

from __future__ import annotations

import os

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.path_utils import resolve_to_cwd
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the file to write (relative or absolute)"},
        "content": {"type": "string", "description": "Full file content to write"},
    },
    "required": ["path", "content"],
}


def _execute_write(cwd: str, tool_call_id, args, signal=None, on_update=None, ctx: ToolContext | None = None):
    path = args["path"]
    content = args["content"]
    absolute_path = resolve_to_cwd(path, cwd)
    parent = os.path.dirname(absolute_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(absolute_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    byte_count = len(content.encode("utf-8"))
    return AgentToolResult(
        content=[TextContent(text=f"Wrote {byte_count} bytes to {path}")],
        details={"path": absolute_path, "bytes": byte_count},
    )


def create_write_tool_definition(cwd: str) -> ToolDefinition:
    return ToolDefinition(
        name="write",
        label="write",
        description="Write a file with the given content, creating parent directories as needed.",
        parameters=WRITE_SCHEMA,
        prompt_snippet="Write a new file",
        prompt_guidelines=["Use write to create or overwrite files."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_write(cwd, tid, args, signal, on_update, ctx),
        render_call=lambda args, ctx=None: f"write {args.get('path', '')}",
    )


def create_write_tool(cwd: str) -> AgentTool:
    return wrap_tool_definition(create_write_tool_definition(cwd), lambda: ToolContext(cwd=cwd))
