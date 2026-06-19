from __future__ import annotations

from appv22.app import CodingApp
from appv22.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from appv22.ai.stream import register_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def test_coding_app_plain_mode_does_not_render_live_tui(tmp_path, capsys) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "plain reply")))
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), enable_tui=False)
    app.run_turn("hi")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert any(getattr(message, "role", None) == "assistant" for message in app.messages)
