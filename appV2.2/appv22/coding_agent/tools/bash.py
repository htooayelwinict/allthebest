"""bash tool. Port of pi/packages/coding-agent/src/core/tools/bash.ts."""

from __future__ import annotations

import subprocess

from appv22.agent.types import AgentTool, AgentToolResult
from appv22.ai.types import TextContent
from appv22.coding_agent.tools.truncate import truncate_head
from appv22.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

BASH_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Shell command to run"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)"},
    },
    "required": ["command"],
}


def _execute_bash(cwd: str, tool_call_id, args, signal=None, on_update=None, ctx: ToolContext | None = None):
    command = args["command"]
    timeout = args.get("timeout", 120)
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return AgentToolResult(
            content=[TextContent(text=f"Command timed out after {timeout}s")],
            details={"timed_out": True},
            terminate=None,
        )
    output = (completed.stdout or "") + (completed.stderr or "")
    truncation = truncate_head(output)
    text = truncation.content
    if truncation.truncated:
        text += f"\n\n[Output truncated: {truncation.output_lines} of {truncation.total_lines} lines]"
    text += f"\n[exit code {completed.returncode}]"
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"exit_code": completed.returncode},
    )


def create_bash_tool_definition(cwd: str) -> ToolDefinition:
    return ToolDefinition(
        name="bash",
        label="bash",
        description="Run a shell command in the working directory and return its combined output.",
        parameters=BASH_SCHEMA,
        prompt_snippet="Run shell commands",
        prompt_guidelines=["Use bash for commands; prefer rg over grep -r."],
        execute=lambda tid, args, signal=None, on_update=None, ctx=None: _execute_bash(cwd, tid, args, signal, on_update, ctx),
        render_call=lambda args, ctx=None: f"bash {args.get('command', '')}",
    )


def create_bash_tool(cwd: str) -> AgentTool:
    return wrap_tool_definition(create_bash_tool_definition(cwd), lambda: ToolContext(cwd=cwd))
