from __future__ import annotations

import pytest

from appv22.ai.event_stream import create_assistant_message_event_stream
from appv22.ai.stream import (
    ApiProvider,
    complete_simple_sync,
    get_api_provider,
    register_api_provider,
    reset_api_providers,
    stream,
    stream_simple,
)
from appv22.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    UserMessage,
    empty_usage,
    now_ms,
)


def _model(api: str = "faux") -> Model:
    return Model(id="m", name="m", api=api, provider="faux", base_url="")


def _provider(api: str = "faux") -> ApiProvider:
    def _stream(model, context, options=None):
        s = create_assistant_message_event_stream()
        msg = AssistantMessage(
            content=[TextContent(text="ok")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        )
        s.push(StartEvent(partial=msg))
        s.push(DoneEvent(reason="stop", message=msg))
        return s

    return ApiProvider(api=api, stream=_stream, stream_simple=_stream)


def setup_function() -> None:
    reset_api_providers()


def test_register_and_get_provider() -> None:
    p = _provider()
    register_api_provider(p)
    assert get_api_provider("faux") is p


def test_get_unknown_provider_raises() -> None:
    with pytest.raises(KeyError):
        get_api_provider("nope")


def test_stream_routes_to_provider_by_model_api() -> None:
    register_api_provider(_provider())
    result = stream(_model(), Context(messages=[UserMessage(content="q", timestamp=now_ms())])).result_sync()
    assert result.content[0].text == "ok"


def test_complete_simple_sync() -> None:
    register_api_provider(_provider())
    msg = complete_simple_sync(_model(), Context(messages=[UserMessage(content="q", timestamp=now_ms())]))
    assert msg.stop_reason == "stop"
    _ = stream_simple  # referenced for import coverage
