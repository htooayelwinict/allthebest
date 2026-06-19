from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # appV2.2/

_TOKENS = ("appv21", "appV2.1")


def test_no_appv21_references_in_source() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "test_no_appv21_coupling.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(token in text for token in _TOKENS):
            offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == [], f"appv21 references remain: {offenders}"


def test_legacy_provider_returns_null_when_disabled(tmp_path: Path, monkeypatch) -> None:
    from appv22.providers import create_appv22_provider_from_appv2_env

    for key in ("APPV2_WORKER_LLM_ENABLED", "APPV2_WORKER_LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")  # not enabled
    provider = create_appv22_provider_from_appv2_env(str(env))
    decision = provider.decide({"selection": {}, "state": {}})
    assert decision.kind == "pause"
