"""grep tool. Port of pi/packages/coding-agent/src/core/tools/grep.ts."""

from __future__ import annotations

import os
import re

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.path_utils import resolve_to_cwd
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

GREP_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regular expression to search for"},
        "path": {"type": "string", "description": "Directory to search (default cwd)"},
        "max_results": {"type": "integer", "description": "Maximum matches to return (default 200)"},
    },
    "required": ["pattern"],
}

_EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".pytest_cache"}


def _execute_grep(cwd: str, tool_call_id, args, signal=None, on_update=None, ctx: ToolContext | None = None):
    pattern = re.compile(args["pattern"])
    root = resolve_to_cwd(args.get("path", "."), cwd)
    max_results = args.get("max_results", 200)
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                    for line_no, line in enumerate(handle, start=1):
                        if pattern.search(line):
                            rel = os.path.relpath(file_path, cwd)
                            matches.append(f"{rel}:{line_no}: {line.rstrip()}")
                            if len(matches) >= max_results:
                                break
            except OSError:
                continue
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break
    text = "\n".join(matches) if matches else "(no matches)"
    return AgentToolResult(content=[TextContent(text=text)], details={"count": len(matches)})


def create_grep_tool_definition(cwd: str) -> ToolDefinition:
    return ToolDefinition(
        name="grep",
        label="grep",
        description="Search file contents under a directory using a regular expression.",
        parameters=GREP_SCHEMA,
        prompt_snippet="Search file contents",
        prompt_guidelines=["Use grep to find code by pattern."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_grep(cwd, tid, args, signal, on_update, ctx),
        render_call=lambda args, ctx=None: f"grep {args.get('pattern', '')}",
    )


def create_grep_tool(cwd: str) -> AgentTool:
    return wrap_tool_definition(create_grep_tool_definition(cwd), lambda: ToolContext(cwd=cwd))
