"""Tool factories + registry. Port of pi/packages/coding-agent/src/core/tools/index.ts."""

from __future__ import annotations

from typing import Literal

from appv22.agent.types import AgentTool
from appv22.coding_agent.tools.bash import create_bash_tool, create_bash_tool_definition
from appv22.coding_agent.tools.edit import create_edit_tool, create_edit_tool_definition
from appv22.coding_agent.tools.find import create_find_tool, create_find_tool_definition
from appv22.coding_agent.tools.grep import create_grep_tool, create_grep_tool_definition
from appv22.coding_agent.tools.ls import create_ls_tool, create_ls_tool_definition
from appv22.coding_agent.tools.read import create_read_tool, create_read_tool_definition
from appv22.coding_agent.tools.types import ToolDefinition
from appv22.coding_agent.tools.write import create_write_tool, create_write_tool_definition

ToolName = Literal["read", "bash", "edit", "write", "grep", "find", "ls"]

all_tool_names: list[str] = ["read", "bash", "edit", "write", "grep", "find", "ls"]

_DEFINITION_FACTORIES = {
    "read": create_read_tool_definition,
    "bash": create_bash_tool_definition,
    "edit": create_edit_tool_definition,
    "write": create_write_tool_definition,
    "grep": create_grep_tool_definition,
    "find": create_find_tool_definition,
    "ls": create_ls_tool_definition,
}

_TOOL_FACTORIES = {
    "read": create_read_tool,
    "bash": create_bash_tool,
    "edit": create_edit_tool,
    "write": create_write_tool,
    "grep": create_grep_tool,
    "find": create_find_tool,
    "ls": create_ls_tool,
}


def create_tool_definition(name: str, cwd: str) -> ToolDefinition:
    return _DEFINITION_FACTORIES[name](cwd)


def create_tool(name: str, cwd: str) -> AgentTool:
    return _TOOL_FACTORIES[name](cwd)


def create_coding_tools(cwd: str) -> list[AgentTool]:
    return [create_read_tool(cwd), create_bash_tool(cwd), create_edit_tool(cwd), create_write_tool(cwd)]


def create_read_only_tools(cwd: str) -> list[AgentTool]:
    return [create_read_tool(cwd), create_grep_tool(cwd), create_find_tool(cwd), create_ls_tool(cwd)]


def create_all_tools(cwd: str) -> list[AgentTool]:
    return [_TOOL_FACTORIES[name](cwd) for name in all_tool_names]


def create_coding_tool_definitions(cwd: str) -> list[ToolDefinition]:
    return [create_tool_definition(n, cwd) for n in ("read", "bash", "edit", "write")]


def create_all_tool_definitions(cwd: str) -> list[ToolDefinition]:
    return [create_tool_definition(n, cwd) for n in all_tool_names]
