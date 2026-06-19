"""read tool. Port of pi/packages/coding-agent/src/core/tools/read.ts."""

from __future__ import annotations

import os

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.path_utils import resolve_to_cwd
from appv22.coding_agent.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size, truncate_head
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the file to read (relative or absolute)"},
        "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
        "limit": {"type": "integer", "description": "Maximum number of lines to read"},
    },
    "required": ["path"],
}


def _execute_read(cwd: str, tool_call_id, args, signal=None, on_update=None, ctx: ToolContext | None = None):
    path = args["path"]
    offset = args.get("offset")
    limit = args.get("limit")
    absolute_path = resolve_to_cwd(path, cwd)
    if not os.path.exists(absolute_path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(absolute_path, "r", encoding="utf-8", errors="replace") as handle:
        text_content = handle.read()
    all_lines = text_content.split("\n")
    total_file_lines = len(all_lines)
    start_line = max(0, offset - 1) if offset else 0
    start_line_display = start_line + 1
    if start_line >= len(all_lines):
        raise ValueError(f"Offset {offset} is beyond end of file ({len(all_lines)} lines total)")

    user_limited_lines = None
    if limit is not None:
        end_line = min(start_line + limit, len(all_lines))
        selected = "\n".join(all_lines[start_line:end_line])
        user_limited_lines = end_line - start_line
    else:
        selected = "\n".join(all_lines[start_line:])

    truncation = truncate_head(selected)
    if truncation.first_line_exceeds_limit:
        first_size = format_size(len(all_lines[start_line].encode("utf-8")))
        output = (
            f"[Line {start_line_display} is {first_size}, exceeds {format_size(DEFAULT_MAX_BYTES)} limit. "
            f"Use bash: sed -n '{start_line_display}p' {path} | head -c {DEFAULT_MAX_BYTES}]"
        )
    elif truncation.truncated:
        end_line_display = start_line_display + truncation.output_lines - 1
        next_offset = end_line_display + 1
        output = truncation.content
        if truncation.truncated_by == "lines":
            output += f"\n\n[Showing lines {start_line_display}-{end_line_display} of {total_file_lines}. Use offset={next_offset} to continue.]"
        else:
            output += (
                f"\n\n[Showing lines {start_line_display}-{end_line_display} of {total_file_lines} "
                f"({format_size(DEFAULT_MAX_BYTES)} limit). Use offset={next_offset} to continue.]"
            )
    elif user_limited_lines is not None and start_line + user_limited_lines < len(all_lines):
        remaining = len(all_lines) - (start_line + user_limited_lines)
        next_offset = start_line + user_limited_lines + 1
        output = f"{truncation.content}\n\n[{remaining} more lines in file. Use offset={next_offset} to continue.]"
    else:
        output = truncation.content

    return AgentToolResult(content=[TextContent(text=output)], details={"truncation": truncation})


def create_read_tool_definition(cwd: str) -> ToolDefinition:
    return ToolDefinition(
        name="read",
        label="read",
        description=(
            f"Read the contents of a file. For text files, output is truncated to {DEFAULT_MAX_LINES} lines or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). Use offset/limit for large files."
        ),
        parameters=READ_SCHEMA,
        prompt_snippet="Read file contents",
        prompt_guidelines=["Use read to examine files instead of cat or sed."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_read(cwd, tid, args, signal, on_update, ctx),
        render_call=lambda args, ctx=None: f"read {args.get('path', '')}",
    )


def create_read_tool(cwd: str) -> AgentTool:
    return wrap_tool_definition(create_read_tool_definition(cwd), lambda: ToolContext(cwd=cwd))
