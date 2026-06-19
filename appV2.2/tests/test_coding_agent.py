from __future__ import annotations

import os
from pathlib import Path

from appv22.agent.types import AgentToolResult
from appv22.coding_agent import (
    AgentSession,
    build_system_prompt,
    create_all_tools,
    create_coding_tools,
    create_read_only_tools,
    create_tool,
    create_tool_definition,
)
from appv22.coding_agent.system_prompt import BuildSystemPromptOptions
from appv22.coding_agent.tools.truncate import truncate_head
from appv22.coding_agent.tools.types import ToolContext, wrap_tool_definition
from appv22.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv22.ai.stream import register_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def test_truncate_head_line_limit() -> None:
    content = "\n".join(str(i) for i in range(5000))
    result = truncate_head(content)
    assert result.truncated is True
    assert result.truncated_by == "lines"
    assert result.output_lines == 2000


def test_read_tool_with_offset_and_truncation(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8")
    tool = create_tool("read", str(tmp_path))
    result = tool.execute("c1", {"path": "f.txt", "offset": 3, "limit": 2})
    assert "line3" in result.content[0].text
    assert "line4" in result.content[0].text
    assert "more lines in file" in result.content[0].text


def test_write_tool_creates_dirs(tmp_path: Path) -> None:
    tool = create_tool("write", str(tmp_path))
    tool.execute("c1", {"path": "sub/dir/new.txt", "content": "hello"})
    assert (tmp_path / "sub" / "dir" / "new.txt").read_text() == "hello"


def test_edit_tool_unique_replace_and_errors(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("alpha beta gamma", encoding="utf-8")
    tool = create_tool("edit", str(tmp_path))
    tool.execute("c1", {"path": "f.txt", "old_string": "beta", "new_string": "BETA"})
    assert target.read_text() == "alpha BETA gamma"
    try:
        tool.execute("c2", {"path": "f.txt", "old_string": "missing", "new_string": "x"})
        assert False, "expected error"
    except ValueError:
        pass


def test_bash_tool_runs_command(tmp_path: Path) -> None:
    tool = create_tool("bash", str(tmp_path))
    result = tool.execute("c1", {"command": "echo hi"})
    assert "hi" in result.content[0].text
    assert "exit code 0" in result.content[0].text


def test_grep_find_ls(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import os\nx = 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing\n", encoding="utf-8")
    grep = create_tool("grep", str(tmp_path))
    assert "a.py" in grep.execute("c1", {"pattern": "import os"}).content[0].text
    find = create_tool("find", str(tmp_path))
    assert "a.py" in find.execute("c2", {"pattern": "*.py"}).content[0].text
    ls = create_tool("ls", str(tmp_path))
    listing = ls.execute("c3", {}).content[0].text
    assert "a.py" in listing and "b.txt" in listing


def test_wrap_tool_definition_injects_ctx(tmp_path: Path) -> None:
    seen = {}

    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        seen["cwd"] = ctx.cwd if ctx else None
        return AgentToolResult(content=[], details={})

    from appv22.coding_agent.tools.types import ToolDefinition

    defn = ToolDefinition(name="t", label="t", description="d", parameters={"type": "object"}, execute=execute)
    tool = wrap_tool_definition(defn, lambda: ToolContext(cwd=str(tmp_path)))
    tool.execute("c1", {})
    assert seen["cwd"] == str(tmp_path)


def test_tool_factory_bundles(tmp_path: Path) -> None:
    assert {t.name for t in create_coding_tools(str(tmp_path))} == {"read", "bash", "edit", "write"}
    assert {t.name for t in create_read_only_tools(str(tmp_path))} == {"read", "grep", "find", "ls"}
    assert len(create_all_tools(str(tmp_path))) == 7


def test_build_system_prompt_includes_tools_and_cwd(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=["read", "bash"],
            tool_snippets={"read": "Read file contents", "bash": "Run shell commands"},
            prompt_guidelines=["Use read to examine files instead of cat or sed."],
        )
    )
    assert "Available tools:" in prompt
    assert "- read: Read file contents" in prompt
    assert "Use read to examine files instead of cat or sed." in prompt
    assert "Be concise in your responses" in prompt
    assert str(tmp_path).replace("\\", "/") in prompt


def test_agent_session_runs_read_tool_call(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("file body here", encoding="utf-8")
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "read", {"path": "hello.txt"})
        return text_response_events(m, "The file says: file body here")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)
    session.prompt("read hello.txt")
    roles = [getattr(msg, "role", None) for msg in session.messages]
    assert "toolResult" in roles
    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert "file body here" in tool_results[0].content[0].text
    assert calls["n"] == 2
