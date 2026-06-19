"""System prompt construction. Port of pi/packages/coding-agent/src/core/system-prompt.ts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional


@dataclass
class BuildSystemPromptOptions:
    cwd: str
    custom_prompt: str | None = None
    selected_tools: Optional[list[str]] = None
    tool_snippets: dict[str, str] = field(default_factory=dict)
    prompt_guidelines: list[str] = field(default_factory=list)
    append_system_prompt: str | None = None
    context_files: list[tuple[str, str]] = field(default_factory=list)  # (path, content)


_PREAMBLE = (
    "You are an expert coding assistant operating inside pi, a coding agent harness. "
    "You help users by reading files, executing commands, editing code, and writing new files."
)


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    prompt_cwd = options.cwd.replace("\\", "/")
    today = _date.today().strftime("%Y-%m-%d")
    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""

    if options.custom_prompt:
        prompt = options.custom_prompt + append_section
        prompt += _context_section(options.context_files)
        prompt += f"\nCurrent date: {today}"
        prompt += f"\nCurrent working directory: {prompt_cwd}"
        return prompt

    tools = options.selected_tools or ["read", "bash", "edit", "write"]
    visible_tools = [name for name in tools if options.tool_snippets.get(name)]
    if visible_tools:
        tools_list = "\n".join(f"- {name}: {options.tool_snippets[name]}" for name in visible_tools)
    else:
        tools_list = "(none)"

    guidelines: list[str] = []
    seen: set[str] = set()

    def add(guideline: str) -> None:
        normalized = guideline.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            guidelines.append(normalized)

    has_bash = "bash" in tools
    has_grep = "grep" in tools
    has_find = "find" in tools
    has_ls = "ls" in tools
    if has_bash and not has_grep and not has_find and not has_ls:
        add("Use bash for file operations like ls, rg, find")
    for guideline in options.prompt_guidelines:
        add(guideline)
    add("Be concise in your responses")
    add("Show file paths clearly when working with files")

    guidelines_text = "\n".join(f"- {g}" for g in guidelines)

    prompt = (
        f"{_PREAMBLE}\n\n"
        f"Available tools:\n{tools_list}\n\n"
        "In addition to the tools above, you may have access to other custom tools depending on the project.\n\n"
        f"Guidelines:\n{guidelines_text}"
    )
    prompt += append_section
    prompt += _context_section(options.context_files)
    prompt += f"\nCurrent date: {today}"
    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def _context_section(context_files: list[tuple[str, str]]) -> str:
    if not context_files:
        return ""
    section = "\n\n<project_context>\n\nProject-specific instructions and guidelines:\n\n"
    for file_path, content in context_files:
        section += f'<project_instructions path="{file_path}">\n{content}\n</project_instructions>\n\n'
    section += "</project_context>\n"
    return section
