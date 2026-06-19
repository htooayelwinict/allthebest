from __future__ import annotations

from pathlib import Path

from appv22.ai.env_config import load_dotenv_values, load_model_config


def test_load_dotenv_values_strips_quotes_and_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        'APPV2_WORKER_LLM_API_KEY="secret"  # inline comment\n'
        "APPV2_WORKER_LLM_MODEL=acme/model-x\n"
        "# full comment line\n",
        encoding="utf-8",
    )
    values = load_dotenv_values(env)
    assert values["APPV2_WORKER_LLM_API_KEY"] == "secret"
    assert values["APPV2_WORKER_LLM_MODEL"] == "acme/model-x"


def test_load_model_config_resolves_prefix_then_fallbacks(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "APPV2_WORKER_LLM_ENABLED=true\n"
        "OPENROUTER_API_KEY=fallback-key\n"
        "APPV2_WORKER_LLM_MODEL=acme/model-x\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("APPV2_WORKER_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = load_model_config("APPV2_WORKER_LLM", env)
    assert config.enabled is True
    assert config.api_key == "fallback-key"
    assert config.model == "acme/model-x"
    assert config.base_url == "https://openrouter.ai/api/v1"


def test_disabled_when_flag_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("APPV2_WORKER_LLM_ENABLED", raising=False)
    monkeypatch.delenv("APPV2_WORKER_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")
    config = load_model_config("APPV2_WORKER_LLM", env)
    assert config.enabled is False
    assert config.model == "xiaomi/mimo-v2.5-pro"
