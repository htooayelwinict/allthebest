"""Stateful Agent wrapper. Port of pi/packages/agent/src/agent.ts (core subset)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from appv22.ai.types import Message, Model, UserMessage, now_ms
from appv22.agent.agent_loop import AgentEventSink, run_agent_loop, run_agent_loop_continue
from appv22.agent.types import (
    AbortSignal,
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    ThinkingLevel,
)

Listener = Callable[[AgentEvent], None]


@dataclass
class AgentState:
    system_prompt: str
    model: Model
    thinking_level: ThinkingLevel = "off"
    tools: list[AgentTool] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    is_streaming: bool = False
    streaming_message: AgentMessage | None = None
    pending_tool_calls: set[str] = field(default_factory=set)
    error_message: str | None = None


class Agent:
    """Owns conversation state and drives the functional agent loop."""

    def __init__(
        self,
        *,
        system_prompt: str,
        model: Model,
        convert_to_llm: Callable[[list[AgentMessage]], list[Message]],
        tools: Optional[list[AgentTool]] = None,
        thinking_level: ThinkingLevel = "off",
        tool_execution: str = "parallel",
        before_tool_call=None,
        after_tool_call=None,
    ) -> None:
        self._state = AgentState(
            system_prompt=system_prompt,
            model=model,
            thinking_level=thinking_level,
            tools=list(tools or []),
        )
        self._convert_to_llm = convert_to_llm
        self._tool_execution = tool_execution
        self._before_tool_call = before_tool_call
        self._after_tool_call = after_tool_call
        self._listeners: list[Listener] = []
        self._signal = AbortSignal()
        self._steering: list[AgentMessage] = []
        self._follow_up: list[AgentMessage] = []

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def signal(self) -> AbortSignal:
        return self._signal

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    def steer(self, message: AgentMessage) -> None:
        self._steering.append(message)

    def follow_up(self, message: AgentMessage) -> None:
        self._follow_up.append(message)

    def abort(self) -> None:
        self._signal.abort()

    def reset(self) -> None:
        self._state.messages = []
        self._state.error_message = None
        self._state.streaming_message = None
        self._state.pending_tool_calls = set()

    def _build_config(self) -> AgentLoopConfig:
        return AgentLoopConfig(
            model=self._state.model,
            convert_to_llm=self._convert_to_llm,
            get_steering_messages=self._drain_steering,
            get_follow_up_messages=self._drain_follow_up,
            tool_execution=self._tool_execution,
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
            reasoning=None if self._state.thinking_level == "off" else self._state.thinking_level,
        )

    def _drain_steering(self) -> list[AgentMessage]:
        drained, self._steering = self._steering, []
        return drained

    def _drain_follow_up(self) -> list[AgentMessage]:
        drained, self._follow_up = self._follow_up, []
        return drained

    def _context(self) -> AgentContext:
        return AgentContext(
            system_prompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=list(self._state.tools),
        )

    def prompt(self, prompt: Union[str, AgentMessage, list[AgentMessage]], stream_fn=None) -> list[AgentMessage]:
        if isinstance(prompt, str):
            messages: list[AgentMessage] = [UserMessage(content=prompt, timestamp=now_ms())]
        elif isinstance(prompt, list):
            messages = list(prompt)
        else:
            messages = [prompt]
        self._state.is_streaming = True
        self._state.error_message = None
        try:
            new_messages = run_agent_loop(
                messages, self._context(), self._build_config(), self._make_sink(), self._signal, stream_fn
            )
        finally:
            self._state.is_streaming = False
            self._state.streaming_message = None
        return new_messages

    def continue_(self, stream_fn=None) -> list[AgentMessage]:
        self._state.is_streaming = True
        try:
            new_messages = run_agent_loop_continue(
                self._context(), self._build_config(), self._make_sink(), self._signal, stream_fn
            )
        finally:
            self._state.is_streaming = False
            self._state.streaming_message = None
        return new_messages

    def _make_sink(self) -> AgentEventSink:
        def _sink(event: AgentEvent) -> None:
            self._process_event(event)
            for listener in list(self._listeners):
                listener(event)

        return _sink

    def _process_event(self, event: AgentEvent) -> None:
        etype = event.type
        if etype == "message_start":
            if getattr(event.message, "role", None) == "assistant":
                self._state.streaming_message = event.message
        elif etype == "message_update":
            self._state.streaming_message = event.message
        elif etype == "message_end":
            self._state.messages.append(event.message)
            if getattr(event.message, "role", None) == "assistant":
                self._state.streaming_message = None
                if getattr(event.message, "stop_reason", None) in ("error", "aborted"):
                    self._state.error_message = getattr(event.message, "error_message", None)
        elif etype == "tool_execution_start":
            self._state.pending_tool_calls.add(event.tool_call_id)
        elif etype == "tool_execution_end":
            self._state.pending_tool_calls.discard(event.tool_call_id)
