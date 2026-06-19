"""ls tool. Port of pi/packages/coding-agent/src/core/tools/ls.ts."""

from __future__ import annotations

import os

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.path_utils import resolve_to_cwd
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

LS_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Directory to list (default cwd)"},
    },
    "required": [],
}


def _execute_ls(cwd: str, tool_call_id, args, signal=None, on_update=None, ctx: ToolContext | None = None):
    root = resolve_to_cwd(args.get("path", "."), cwd)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"Not a directory: {args.get('path', '.')}")
    entries = []
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        entries.append(f"{name}/" if os.path.isdir(full) else name)
    text = "\n".join(entries) if entries else "(empty directory)"
    return AgentToolResult(content=[TextContent(text=text)], details={"count": len(entries)})


def create_ls_tool_definition(cwd: str) -> ToolDefinition:
    return ToolDefinition(
        name="ls",
        label="ls",
        description="List the entries of a directory.",
        parameters=LS_SCHEMA,
        prompt_snippet="List directory contents",
        prompt_guidelines=["Use ls to list directory contents."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_ls(cwd, tid, args, signal, on_update, ctx),
        render_call=lambda args, ctx=None: f"ls {args.get('path', '.')}",
    )


def create_ls_tool(cwd: str) -> AgentTool:
    return wrap_tool_definition(create_ls_tool_definition(cwd), lambda: ToolContext(cwd=cwd))
