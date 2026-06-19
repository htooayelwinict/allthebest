"""find tool. Port of pi/packages/coding-agent/src/core/tools/find.ts."""

from __future__ import annotations

import fnmatch
import os

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.path_utils import resolve_to_cwd
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Glob pattern to match file names (e.g. *.py)"},
        "path": {"type": "string", "description": "Directory to search (default cwd)"},
        "max_results": {"type": "integer", "description": "Maximum files to return (default 200)"},
    },
    "required": ["pattern"],
}

_EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".pytest_cache"}


def _execute_find(cwd: str, tool_call_id, args, signal=None, on_update=None, ctx: ToolContext | None = None):
    pattern = args["pattern"]
    root = resolve_to_cwd(args.get("path", "."), cwd)
    max_results = args.get("max_results", 200)
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for filename in filenames:
            if fnmatch.fnmatch(filename, pattern):
                results.append(os.path.relpath(os.path.join(dirpath, filename), cwd))
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break
    text = "\n".join(sorted(results)) if results else "(no files found)"
    return AgentToolResult(content=[TextContent(text=text)], details={"count": len(results)})


def create_find_tool_definition(cwd: str) -> ToolDefinition:
    return ToolDefinition(
        name="find",
        label="find",
        description="Find files by glob pattern under a directory.",
        parameters=FIND_SCHEMA,
        prompt_snippet="Find files by name pattern",
        prompt_guidelines=["Use find to locate files by name."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_find(cwd, tid, args, signal, on_update, ctx),
        render_call=lambda args, ctx=None: f"find {args.get('pattern', '')}",
    )


def create_find_tool(cwd: str) -> AgentTool:
    return wrap_tool_definition(create_find_tool_definition(cwd), lambda: ToolContext(cwd=cwd))
