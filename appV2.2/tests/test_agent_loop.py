from __future__ import annotations

from appv22.agent import (
    Agent,
    AgentContext,
    AgentTool,
    AgentToolResult,
    BeforeToolCallResult,
    ShouldStopAfterTurnContext,
    run_agent_loop,
)
from appv22.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from appv22.ai.stream import register_api_provider, reset_api_providers
from appv22.ai.types import Message, TextContent, UserMessage, now_ms


def _convert(messages):
    out: list[Message] = []
    for m in messages:
        if getattr(m, "role", None) in ("user", "assistant", "toolResult"):
            out.append(m)
    return out


def _ctx(tools=None) -> AgentContext:
    return AgentContext(system_prompt="sys", messages=[], tools=tools)


def setup_function() -> None:
    reset_api_providers()


def test_single_text_turn_event_sequence() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "hello")))
    events: list[str] = []
    msgs = run_agent_loop(
        [UserMessage(content="hi", timestamp=now_ms())],
        _ctx(),
        _config(model),
        lambda e: events.append(e.type),
    )
    assert events[0] == "agent_start"
    assert events[1] == "turn_start"
    assert "message_update" in events
    assert events[-1] == "agent_end"
    assert any(getattr(m, "role", None) == "assistant" for m in msgs)


def _config(model):
    from appv22.agent.types import AgentLoopConfig

    return AgentLoopConfig(model=model, convert_to_llm=_convert)


def test_tool_call_turn_executes_and_continues() -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "echo", {"text": "hi"})
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def echo_execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"echo:{args['text']}")], details={})

    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        label="Echo",
        execute=echo_execute,
    )
    events: list[str] = []
    msgs = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[echo]),
        _config(model),
        lambda e: events.append(e.type),
    )
    assert "tool_execution_start" in events
    assert "tool_execution_end" in events
    assert any(getattr(m, "role", None) == "toolResult" for m in msgs)
    assert calls["n"] == 2


def test_should_stop_after_turn_halts_loop() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "x")))
    cfg = _config(model)
    cfg.should_stop_after_turn = lambda ctx: True
    turn_starts = 0
    events: list[str] = []
    run_agent_loop([UserMessage(content="hi", timestamp=now_ms())], _ctx(), cfg, lambda e: events.append(e.type))
    assert events.count("turn_start") == 1


def test_before_tool_call_block_yields_error_result() -> None:
    model = faux_model()

    def script(m, c):
        return tool_call_response_events(m, "danger", {})

    register_api_provider(create_faux_provider(script))
    danger = AgentTool(
        name="danger", description="d", parameters={"type": "object"}, label="D",
        execute=lambda *a, **k: AgentToolResult(content=[TextContent(text="ran")], details={}),
    )
    cfg = _config(model)
    cfg.before_tool_call = lambda ctx, signal: BeforeToolCallResult(block=True, reason="nope")
    # avoid infinite loop: after the blocked tool, model would be called again; make 2nd call finalize
    calls = {"n": 0}

    def script2(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "danger", {})
        return text_response_events(m, "stopped")

    reset_api_providers()
    register_api_provider(create_faux_provider(script2))
    ends: list = []
    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())], _ctx(tools=[danger]), cfg,
        lambda e: ends.append(e) if e.type == "tool_execution_end" else None,
    )
    end = ends[0]
    assert end.is_error is True
    assert "nope" in end.result.content[0].text


def test_unknown_tool_returns_error_result() -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "missing", {})
        return text_response_events(m, "ok")

    register_api_provider(create_faux_provider(script))
    ends: list = []
    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())], _ctx(tools=[]), _config(model),
        lambda e: ends.append(e) if e.type == "tool_execution_end" else None,
    )
    assert ends[0].is_error is True
    assert "not found" in ends[0].result.content[0].text


def test_agent_class_reduces_state() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    seen: list[str] = []
    agent.subscribe(lambda e: seen.append(e.type))
    agent.prompt("hello")
    assert "agent_end" in seen
    roles = [getattr(m, "role", None) for m in agent.state.messages]
    assert "user" in roles and "assistant" in roles
    assert agent.state.is_streaming is False
