from __future__ import annotations

from appv22.ai.models import (
    calculate_cost,
    get_model,
    get_models,
    get_providers,
    register_model,
    reset_models,
)
from appv22.ai.types import Cost, Model


def setup_function() -> None:
    reset_models()


def _model() -> Model:
    return Model(
        id="m1",
        name="M1",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        cost=Cost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
        context_window=128000,
        max_tokens=8192,
    )


def test_register_and_lookup() -> None:
    m = _model()
    register_model(m)
    assert get_model("openrouter", "m1") is m
    assert get_models("openrouter") == [m]
    assert get_providers() == ["openrouter"]


def test_get_unknown_model_returns_none() -> None:
    assert get_model("openrouter", "missing") is None


def test_calculate_cost_per_million_tokens() -> None:
    m = _model()
    cost = calculate_cost(m, {"input": 1_000_000, "output": 500_000, "cache_read": 2_000_000, "cache_write": 0})
    assert cost.input == 1.0
    assert cost.output == 1.0
    assert cost.cache_read == 1.0
    assert cost.total == 3.0
