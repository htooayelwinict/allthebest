"""appv2-env provider: OpenAI/OpenRouter-compatible streaming over httpx SSE."""

from __future__ import annotations

import json
import threading
from typing import Iterable, Iterator

import httpx

from appv22.ai.env_config import ModelConfig, load_model_config
from appv22.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from appv22.ai.stream import ApiProvider
from appv22.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    Message,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)

PROVIDER_API = "openai-completions"

_FINISH_REASON_MAP = {"stop": "stop", "length": "length", "tool_calls": "toolUse"}


def convert_messages(context: Context) -> "tuple[list[dict], list[dict] | None]":
    messages: list[dict] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": context.system_prompt})
    for message in context.messages:
        messages.append(_convert_message(message))
    tools = None
    if context.tools:
        tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in context.tools
        ]
    return messages, tools


def _convert_message(message: Message) -> dict:
    if message.role == "user":
        content = message.content if isinstance(message.content, str) else _text_of(message.content)
        return {"role": "user", "content": content}
    if message.role == "toolResult":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.tool_name,
            "content": _text_of(message.content),
        }
    # assistant
    text_parts = [b.text for b in message.content if isinstance(b, TextContent)]
    tool_calls = [
        {"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.arguments)}}
        for b in message.content
        if isinstance(b, ToolCall)
    ]
    out: dict = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.text for b in content if isinstance(b, TextContent))


def _blank(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[], api=model.api, provider=model.provider, model=model.id,
        usage=empty_usage(), stop_reason="stop", timestamp=now_ms(),
    )


def _iter_sse_data(lines: Iterable[str]) -> Iterator[str]:
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        yield payload


def parse_sse_chunks(lines: Iterable[str], model: Model) -> Iterator:
    """Pure transform: decoded SSE lines -> AssistantMessageEvent stream."""
    message = _blank(model)
    started = False
    text_index: int | None = None
    text_buf = ""
    thinking_index: int | None = None
    tool_index: int | None = None
    tool_arg_buf = ""
    tool_call: ToolCall | None = None
    finish_reason = "stop"
    usage = empty_usage()

    def ensure_start():
        nonlocal started
        if not started:
            started = True
            return StartEvent(partial=message)
        return None

    for payload in _iter_sse_data(lines):
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = _merge_usage(usage, chunk.get("usage"))
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}

        reasoning = delta.get("reasoning")
        if reasoning:
            start = ensure_start()
            if start:
                yield start
            if thinking_index is None:
                thinking_index = len(message.content)
                message.content.append(ThinkingContent(thinking=""))
                yield ThinkingStartEvent(content_index=thinking_index, partial=message)
            message.content[thinking_index].thinking += reasoning
            yield ThinkingDeltaEvent(content_index=thinking_index, delta=reasoning, partial=message)

        content_piece = delta.get("content")
        if content_piece:
            start = ensure_start()
            if start:
                yield start
            if text_index is None:
                text_index = len(message.content)
                message.content.append(TextContent(text=""))
                yield TextStartEvent(content_index=text_index, partial=message)
            text_buf += content_piece
            message.content[text_index].text = text_buf
            yield TextDeltaEvent(content_index=text_index, delta=content_piece, partial=message)

        for tc in delta.get("tool_calls") or []:
            start = ensure_start()
            if start:
                yield start
            if tool_index is None:
                tool_index = len(message.content)
                tool_call = ToolCall(
                    id=tc.get("id") or "call_1",
                    name=(tc.get("function") or {}).get("name") or "",
                    arguments={},
                )
                message.content.append(tool_call)
                yield ToolcallStartEvent(content_index=tool_index, partial=message)
            fn = tc.get("function") or {}
            if fn.get("name") and tool_call is not None and not tool_call.name:
                tool_call.name = fn["name"]
            arg_fragment = fn.get("arguments") or ""
            if arg_fragment:
                tool_arg_buf += arg_fragment
                yield ToolcallDeltaEvent(content_index=tool_index, delta=arg_fragment, partial=message)

        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    if text_index is not None:
        yield TextEndEvent(content_index=text_index, content=text_buf, partial=message)
    if tool_index is not None and tool_call is not None:
        try:
            tool_call.arguments = json.loads(tool_arg_buf) if tool_arg_buf else {}
        except json.JSONDecodeError:
            tool_call.arguments = {}
        yield ToolcallEndEvent(content_index=tool_index, tool_call=tool_call, partial=message)

    if not started:
        yield StartEvent(partial=message)
    message.usage = usage
    reason = _FINISH_REASON_MAP.get(finish_reason, "stop")
    message.stop_reason = reason
    yield DoneEvent(reason=reason, message=message)


def _merge_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not raw:
        return usage
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    usage.input = prompt or usage.input
    usage.output = completion or usage.output
    usage.total_tokens = int(raw.get("total_tokens") or 0) or usage.total_tokens
    return usage


class AppV2EnvProvider:
    api = PROVIDER_API

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        threading.Thread(target=self._run, args=(s, model, context, options), daemon=True).start()
        return s

    stream_simple = stream

    def _run(self, s: AssistantMessageEventStream, model: Model, context: Context, options) -> None:
        try:
            messages, tools = convert_messages(context)
            body: dict = {
                "model": self.config.model or model.id,
                "messages": messages,
                "stream": True,
                "temperature": self.config.temperature,
            }
            if tools:
                body["tools"] = tools
            if self.config.max_tokens is not None:
                body["max_tokens"] = self.config.max_tokens
            if self.config.provider_sort:
                body["provider"] = {"sort": self.config.provider_sort, "allow_fallbacks": True}
            headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
            url = self.config.base_url.rstrip("/") + "/chat/completions"
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    response.raise_for_status()
                    for event in parse_sse_chunks(response.iter_lines(), model):
                        s.push(event)
        except Exception as exc:  # encode failure as an error event, never raise
            err = _blank(model)
            err.stop_reason = "error"
            err.error_message = str(exc)
            s.push(ErrorEvent(reason="error", error=err))


class NullProvider:
    api = PROVIDER_API

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        err = _blank(model)
        err.stop_reason = "error"
        err.error_message = "model transport not configured"
        s.push(ErrorEvent(reason="error", error=err))
        return s

    stream_simple = stream


def create_appv2_env_provider(prefix: str = "APPV2_WORKER_LLM", dotenv_path: "str" = ".env") -> ApiProvider:
    config = load_model_config(prefix, dotenv_path)
    impl = AppV2EnvProvider(config) if (config.enabled and config.api_key) else NullProvider()
    return ApiProvider(api=PROVIDER_API, stream=impl.stream, stream_simple=impl.stream_simple)
