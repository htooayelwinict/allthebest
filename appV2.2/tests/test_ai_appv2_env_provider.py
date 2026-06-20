from __future__ import annotations

import json

from appv22.ai.providers.appv2_env import (
    NullProvider,
    convert_messages,
    parse_sse_chunks,
)
from appv22.ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    Tool,
    ToolResultMessage,
    UserMessage,
    now_ms,
)


def _model() -> Model:
    return Model(id="acme/x", name="X", api="openai-completions", provider="openrouter", base_url="")


def test_convert_messages_maps_roles_and_tools() -> None:
    ctx = Context(
        system_prompt="sys",
        messages=[
            UserMessage(content="hello", timestamp=now_ms()),
            ToolResultMessage(
                tool_call_id="c1", tool_name="read",
                content=[TextContent(text="file body")], is_error=False, timestamp=now_ms(),
            ),
        ],
        tools=[Tool(name="read", description="read", parameters={"type": "object"})],
    )
    messages, tools = convert_messages(ctx)
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "hello"}
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "c1"
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "read"


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj)


def test_parse_sse_text_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    types = [e.type for e in events]
    assert types[0] == "start"
    assert "text_delta" in types
    assert types[-1] == "done"
    final = events[-1].message
    assert final.content[0].text == "Hello"
    assert final.stop_reason == "stop"


def test_parse_sse_tool_call_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "read", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": " \"a.txt\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    assert events[-1].type == "done"
    assert events[-1].reason == "toolUse"
    tool_call = events[-1].message.content[0]
    assert tool_call.type == "toolCall"
    assert tool_call.name == "read"
    assert tool_call.arguments == {"path": "a.txt"}


def test_null_provider_emits_error_event() -> None:
    s = NullProvider().stream(_model(), Context(messages=[]))
    events = list(s)
    assert events[-1].type == "error"
    msg = s.result_sync()
    assert isinstance(msg, AssistantMessage)
    assert msg.stop_reason == "error"
