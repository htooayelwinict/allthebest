"""CLI entrypoint for the pi+hermes-compliant appv22 stack."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from appv22.ai.env_config import load_model_config
from appv22.ai.register_builtins import register_builtin_providers
from appv22.ai.types import Model
from appv22.app import CodingApp


def _model_from_env(dotenv_path: str | Path) -> Model:
    config = load_model_config("APPV2_WORKER_LLM", dotenv_path)
    model_id = config.model or "xiaomi/mimo-v2.5-pro"
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="openrouter",
        base_url=config.base_url,
        reasoning=False,
        context_window=128000,
        max_tokens=config.max_tokens or 8192,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the appv22 pi+hermes coding app")
    parser.add_argument("prompt", nargs="*", help="Prompt to run. If omitted, starts a small stdin loop.")
    parser.add_argument("--cwd", default=".", help="Working directory for tools")
    parser.add_argument("--dotenv", default=".env", help="Dotenv file for APPV2_WORKER_LLM/OpenRouter settings")
    args = parser.parse_args(argv)

    register_builtin_providers(dotenv_path=args.dotenv)
    app = CodingApp(cwd=str(Path(args.cwd).resolve()), model=_model_from_env(args.dotenv))

    prompt = " ".join(args.prompt).strip()
    if prompt:
        app.run_turn(prompt)
        _print_last_assistant(app)
        return 0

    while True:
        try:
            prompt = input("appv22> ").strip()
        except EOFError:
            return 0
        if prompt in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if not prompt:
            continue
        app.run_turn(prompt)
        _print_last_assistant(app)


def _print_last_assistant(app: CodingApp) -> None:
    for message in reversed(app.messages):
        if getattr(message, "role", None) != "assistant":
            continue
        texts = [block.text for block in getattr(message, "content", []) if getattr(block, "type", None) == "text"]
        if texts:
            print("".join(texts))
        return


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
