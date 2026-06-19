from __future__ import annotations

from pathlib import Path

from appv22.app import CodingApp
from appv22.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv22.ai.stream import register_api_provider, reset_api_providers
from appv22.tui.terminal import FakeTerminal


def setup_function() -> None:
    reset_api_providers()


def test_end_to_end_coding_app_read_tool_and_render(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("integration body", encoding="utf-8")
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "read", {"path": "notes.txt"})
        return text_response_events(m, "notes.txt contains integration body")

    register_api_provider(create_faux_provider(script))

    terminal = FakeTerminal(columns=80)
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=terminal)
    app.run_turn("read notes.txt and summarize")

    roles = [getattr(m, "role", None) for m in app.messages]
    assert "user" in roles and "assistant" in roles and "toolResult" in roles
    rendered = "\n".join(app.tui.render(80))
    assert "read" in rendered
    assert "integration body" in rendered
    assert "integration body" in "\n".join(
        b.text for m in app.messages if getattr(m, "role", None) == "toolResult" for b in m.content
    )
    assert calls["n"] == 2


def test_coding_app_wires_compaction_transform(tmp_path: Path) -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "ok")))
    app = CodingApp(cwd=str(tmp_path), model=model, terminal=FakeTerminal(), context_length=1000)
    # transform_context is the hermes preflight phase
    assert app.session.agent._transform_context is not None
    app.run_turn("hello")
    assert any(getattr(m, "role", None) == "assistant" for m in app.messages)
