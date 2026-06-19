"""Model registry + cost. Port of pi/packages/ai/src/models.ts (minimal subset)."""

from __future__ import annotations

from appv22.ai.types import Cost, Model

_MODELS: dict[str, dict[str, Model]] = {}


def register_model(model: Model) -> None:
    _MODELS.setdefault(model.provider, {})[model.id] = model


def get_model(provider: str, model_id: str) -> Model | None:
    return _MODELS.get(provider, {}).get(model_id)


def get_models(provider: str) -> list[Model]:
    return list(_MODELS.get(provider, {}).values())


def get_providers() -> list[str]:
    return list(_MODELS.keys())


def reset_models() -> None:
    _MODELS.clear()


def calculate_cost(model: Model, usage_tokens: dict[str, int]) -> Cost:
    """Cost from per-million-token pricing on the model."""

    def per_million(tokens: int, rate: float) -> float:
        return (tokens / 1_000_000.0) * rate

    cost = Cost(
        input=per_million(usage_tokens.get("input", 0), model.cost.input),
        output=per_million(usage_tokens.get("output", 0), model.cost.output),
        cache_read=per_million(usage_tokens.get("cache_read", 0), model.cost.cache_read),
        cache_write=per_million(usage_tokens.get("cache_write", 0), model.cost.cache_write),
    )
    cost.total = cost.input + cost.output + cost.cache_read + cost.cache_write
    return cost
