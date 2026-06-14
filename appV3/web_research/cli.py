from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .constants import DEFAULT_MARKDOWN, DEFAULT_MODEL, DEFAULT_OUTPUT, DEFAULT_RUNTIME_ROOT
from .runtime import run_agent, write_outputs

try:
    from rich import box
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table
    from rich.theme import Theme

    HAS_RICH = True
except ImportError:  # pragma: no cover - fallback path
    HAS_RICH = False
    Console = None  # type: ignore[assignment]
    Markdown = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Theme = None  # type: ignore[assignment]
    box = None  # type: ignore[assignment]

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style as PromptStyle

    HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover - fallback path
    PromptSession = None  # type: ignore[assignment]
    AutoSuggestFromHistory = None  # type: ignore[assignment]
    WordCompleter = None  # type: ignore[assignment]
    FileHistory = None  # type: ignore[assignment]
    PromptStyle = None  # type: ignore[assignment]
    HAS_PROMPT_TOOLKIT = False


THEME = (
    Theme(
        {
            "app": "bold white on #0f172a",
            "accent": "bold #60a5fa",
            "meta": "#94a3b8",
            "prompt": "bold #7dd3fc",
            "assistant": "bold #93c5fd",
            "tool": "bold #f0abfc",
            "ok": "bold #86efac",
            "warn": "bold #fbbf24",
            "error": "bold red",
        }
    )
    if HAS_RICH
    else None
)
console = Console(theme=THEME, highlight=False) if HAS_RICH else None
PROMPT_STYLE = (
    PromptStyle.from_dict({"user": "bold #7dd3fc", "symbol": "bold #e2e8f0", "hint": "#64748b"})
    if HAS_PROMPT_TOOLKIT
    else None
)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive appV3 web_research agent")
    parser.add_argument("--thread-id", default="default", help="Persistent web_research session id")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT, help="Tool runtime root")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model")
    parser.add_argument("--max-steps", type=positive_int, default=4, help="Model/tool loop steps per turn")
    parser.add_argument("--max-tokens", type=positive_int, default=4096)
    parser.add_argument("--max-source-links", type=positive_int, default=7)
    parser.add_argument("--max-related-links", type=positive_int, default=7)
    parser.add_argument("--model-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--provider-sort", default=os.environ.get("OPENROUTER_PROVIDER_SORT", "latency"))
    parser.add_argument("--skip-npm-install", action="store_true")
    parser.add_argument("--skip-browser-install", action="store_true")
    parser.add_argument("--setup-timeout", type=positive_int, default=180)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    args.max_source_links = min(args.max_source_links, 7)
    args.max_related_links = min(args.max_related_links, 50)
    return args


def make_turn_args(config: argparse.Namespace, user_input: str) -> SimpleNamespace:
    return SimpleNamespace(
        task=[user_input],
        tasks_file=None,
        run_id=config.thread_id,
        runtime_root=config.runtime_root,
        model=config.model,
        max_steps=config.max_steps,
        max_tokens=config.max_tokens,
        max_source_links=config.max_source_links,
        max_related_links=config.max_related_links,
        model_timeout_seconds=config.model_timeout_seconds,
        temperature=config.temperature,
        provider_sort=config.provider_sort,
        skip_npm_install=config.skip_npm_install,
        skip_browser_install=config.skip_browser_install,
        setup_timeout=config.setup_timeout,
    )


def build_prompt_session(thread_id: str) -> Any:
    if not HAS_PROMPT_TOOLKIT:
        return None
    history_path = os.path.join(os.path.expanduser("~"), f".appv3_web_research_{thread_id}_history")
    completer = WordCompleter(["/help", "/clear", "/status", "/exit", "quit", "exit"], ignore_case=True)
    return PromptSession(
        history=FileHistory(history_path),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        complete_while_typing=True,
    )


def read_user_input(session: Any) -> str:
    if not HAS_PROMPT_TOOLKIT or session is None:
        return input("web_research ▸ ")
    return session.prompt(
        [("class:user", "web_research"), ("class:symbol", " ▸ ")],
        style=PROMPT_STYLE,
        bottom_toolbar=[("class:hint", "/help commands · /exit quit · ↑/↓ history")],
    )


def print_welcome(args: argparse.Namespace) -> None:
    workspace = args.runtime_root / args.thread_id
    if HAS_RICH and console is not None:
        table = Table.grid(expand=True)
        table.add_column(style="meta")
        table.add_column(style="meta")
        table.add_row("Thread", args.thread_id)
        table.add_row("Workspace", str(workspace.resolve()))
        table.add_row("Model", args.model)
        table.add_row("Tool", "run_playwright_code")
        console.print(
            Panel(
                table,
                title="[app] appV3 web_research [/app]",
                subtitle="interactive agent",
                border_style="accent",
                box=box.HEAVY_EDGE,
                padding=(0, 1),
            )
        )
        console.print("[meta]Commands:[/meta] [prompt]/help[/prompt]  [prompt]/status[/prompt]  [prompt]/clear[/prompt]  [prompt]/exit[/prompt]")
        return
    print(f"appV3 web_research interactive agent ({args.thread_id})")
    print(f"Workspace: {workspace.resolve()}")
    print("Commands: /help /status /clear /exit")


def print_help() -> None:
    rows = [
        ("/help", "Show this help"),
        ("/status", "Show runtime workspace and current config"),
        ("/clear", "Clear terminal and redraw header"),
        ("/exit", "Exit the session"),
        ("any other text", "Run one web_research turn with Playwright tool calls"),
    ]
    if HAS_RICH and console is not None:
        table = Table(box=box.MINIMAL_DOUBLE_HEAD, header_style="accent")
        table.add_column("Command", style="prompt")
        table.add_column("Description", style="meta")
        for command, description in rows:
            table.add_row(command, description)
        console.print(Panel(table, title="Help", border_style="accent", box=box.ROUNDED))
        return
    for command, description in rows:
        print(f"{command:18} {description}")


def print_status(args: argparse.Namespace) -> None:
    workspace = args.runtime_root / args.thread_id
    data = {
        "thread_id": args.thread_id,
        "workspace": str(workspace.resolve()),
        "model": args.model,
        "max_steps": args.max_steps,
        "max_source_links": args.max_source_links,
        "max_related_links": args.max_related_links,
    }
    if HAS_RICH and console is not None:
        table = Table.grid(expand=True)
        table.add_column(style="meta")
        table.add_column(style="meta")
        for key, value in data.items():
            table.add_row(key, str(value))
        console.print(Panel(table, title="Status", border_style="accent", box=box.ROUNDED))
        return
    for key, value in data.items():
        print(f"{key}: {value}")


def render_turn(payload: dict[str, Any], *, output_path: Path, markdown_path: Path) -> None:
    final = payload.get("final_result") or {}
    summary = str(final.get("summary") or "No summary produced.")
    if HAS_RICH and console is not None:
        console.print(
            Panel(
                Markdown(summary),
                title="[assistant]web_research[/assistant]",
                border_style="assistant",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
        table = Table(box=box.MINIMAL_HEAVY_HEAD, show_header=True, header_style="tool")
        table.add_column("Tool runs", justify="right")
        table.add_column("OK", justify="right")
        table.add_column("Warnings", justify="right")
        stats = payload.get("stats") or {}
        table.add_row(
            str(stats.get("tool_run_count", 0)),
            str(stats.get("successful_tool_run_count", 0)),
            str(stats.get("warning_count", 0)),
        )
        console.print(Panel(table, title="[tool]Tool Broker[/tool]", border_style="tool", box=box.ROUNDED))
        console.print(f"[meta]Saved:[/meta] {output_path}  {markdown_path}")
        return
    print(summary)
    print(f"Saved: {output_path} {markdown_path}")


def handle_command(command: str, args: argparse.Namespace) -> str:
    lowered = command.lower()
    if lowered in {"/exit", "exit", "quit"}:
        return "exit"
    if lowered == "/help":
        print_help()
        return "handled"
    if lowered == "/status":
        print_status(args)
        return "handled"
    if lowered == "/clear":
        if HAS_RICH and console is not None:
            console.clear()
        else:
            print("\033c", end="")
        print_welcome(args)
        return "handled"
    return "unhandled"


def repl(args: argparse.Namespace) -> None:
    print_welcome(args)
    session = build_prompt_session(args.thread_id)
    turn = 1
    while True:
        try:
            user_input = read_user_input(session).strip()
        except EOFError:
            print_plain("Session ended.")
            return
        except KeyboardInterrupt:
            print_plain("Interrupted. Use /exit to quit.")
            continue
        if not user_input:
            continue
        command_result = handle_command(user_input, args)
        if command_result == "exit":
            print_plain("Session ended.")
            return
        if command_result == "handled":
            continue

        turn_args = make_turn_args(args, user_input)
        if HAS_RICH and console is not None:
            with console.status("[accent]Researching with Playwright tool broker...[/accent]", spinner="dots"):
                payload = run_agent(turn_args)
        else:
            payload = run_agent(turn_args)
        workspace = args.runtime_root / args.thread_id
        output_path = workspace / f"turn_{turn:03d}.json"
        markdown_path = workspace / f"turn_{turn:03d}.md"
        write_outputs(payload, output=output_path, markdown=markdown_path)
        render_turn(payload, output_path=output_path, markdown_path=markdown_path)
        turn += 1


def print_plain(message: str) -> None:
    if HAS_RICH and console is not None:
        console.print(f"[meta]{message}[/meta]")
    else:
        print(message)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repl(args)
    except SystemExit:
        raise
    except Exception as exc:
        if HAS_RICH and console is not None:
            console.print(f"[error]Error:[/error] {exc}")
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
