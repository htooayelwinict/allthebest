"""Stream entrypoints + api-registry. Port of stream.ts + api-registry.ts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from appv22.ai.event_stream import AssistantMessageEventStream
from appv22.ai.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)

StreamFn = Callable[[Model, Context, "StreamOptions | None"], AssistantMessageEventStream]
SimpleStreamFn = Callable[[Model, Context, "SimpleStreamOptions | None"], AssistantMessageEventStream]


@dataclass
class ApiProvider:
    api: str
    stream: StreamFn
    stream_simple: SimpleStreamFn


_API_PROVIDERS: dict[str, ApiProvider] = {}


def register_api_provider(provider: ApiProvider) -> None:
    _API_PROVIDERS[provider.api] = provider


def get_api_provider(api: str) -> ApiProvider:
    provider = _API_PROVIDERS.get(api)
    if provider is None:
        raise KeyError(f"No api provider registered for api '{api}'")
    return provider


def reset_api_providers() -> None:
    _API_PROVIDERS.clear()


def stream(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessageEventStream:
    return get_api_provider(model.api).stream(model, context, options)


def stream_simple(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessageEventStream:
    return get_api_provider(model.api).stream_simple(model, context, options)


async def complete(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
    return await stream(model, context, options).result()


async def complete_simple(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessage:
    return await stream_simple(model, context, options).result()


def complete_sync(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
    return stream(model, context, options).result_sync()


def complete_simple_sync(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessage:
    return stream_simple(model, context, options).result_sync()
