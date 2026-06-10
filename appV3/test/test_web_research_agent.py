from __future__ import annotations

from pathlib import Path

import pytest

from appV3.web_research.cli import make_turn_args, parse_args
from appV3.web_research.constants import RESULT_MARKER
from appV3.web_research.openrouter_client import parse_decision
from appV3.web_research.playwright_runner import PlaywrightToolRunner, build_runtime_paths, collect_warnings, extract_marked_json
from appV3.web_research.prompts import WEB_RESEARCH_SYSTEM_PROMPT
from appV3.web_research.runtime import build_client, loop_guard_message, render_markdown, run_agent
from appV3.web_research.schemas import AgentDecision, PlaywrightResult, ToolArgs, ToolRunRecord
from appV3.web_research.tool_broker import ToolBroker, broker_advice
from appV3.web_research.validators import validate_typescript


SAFE_TS = f"""
import {{ test, expect }} from '@playwright/test';

test('collect example data', async ({{ page }}) => {{
  await page.goto('https://example.com', {{ waitUntil: 'domcontentloaded' }});
  const title = await page.title();
  const text = await page.locator('body').innerText();
  expect(title.length).toBeGreaterThan(0);
  console.log('{RESULT_MARKER}' + JSON.stringify({{
    sourceLinks: [{{ title, url: page.url(), note: 'example source' }}],
    sourcePages: [{{ title, url: page.url(), text }}],
    relatedLinks: [],
    relatedPages: [],
    extractiveSummary: text.slice(0, 200)
  }}));
}});
"""


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, messages: list[dict[str, str]]) -> AgentDecision:
        self.calls += 1
        if self.calls == 1:
            return parse_decision(
                {
                    "status": "tool_calls",
                    "tool_calls": [
                        {
                            "id": "search_batch",
                            "name": "run_playwright_code",
                            "args": {"code": SAFE_TS, "save_as": "search_batch", "timeout": 30},
                        }
                    ],
                    "final_result": None,
                    "notes": "run one batch",
                }
            )
        return parse_decision(
            {
                "status": "final",
                "tool_calls": [],
                "final_result": {
                    "summary": "Example Domain is a placeholder page.",
                    "key_findings": ["The tool returned one source."],
                    "sources": [{"title": "Example Domain", "url": "https://example.com", "note": "tool evidence"}],
                    "related_sources": [],
                    "limitations": [],
                },
                "notes": "done",
            }
        )


class FakeRunner:
    def run_playwright_code(self, args: ToolArgs) -> PlaywrightResult:
        return PlaywrightResult(
            ok=True,
            execution_ok=True,
            data_ok=True,
            warnings=[],
            exit_code=0,
            stdout=f'{RESULT_MARKER}{{"sourceLinks":[{{"title":"Example Domain","url":"https://example.com"}}],"extractiveSummary":"Example text"}}',
            stderr="",
            timed_out=False,
            test_file="tests/search_batch.spec.ts",
            saved=True,
            workspace="/tmp/web_search_ts/unit",
            parsed_json={
                "sourceLinks": [{"title": "Example Domain", "url": "https://example.com"}],
                "extractiveSummary": "Example text",
            },
        )


class EmptyDataRunner:
    def run_playwright_code(self, args: ToolArgs) -> PlaywrightResult:
        return PlaywrightResult(
            ok=False,
            execution_ok=True,
            data_ok=False,
            warnings=["empty_collection_detected"],
            exit_code=0,
            stdout=f"{RESULT_MARKER}{'{}'}",
            stderr="",
            timed_out=False,
            test_file="tests/empty.spec.ts",
            saved=False,
            workspace="/tmp/web_search_ts/unit",
            parsed_json={},
        )


def test_system_prompt_describes_tool_broker_and_five_step_workflow() -> None:
    assert "run_playwright_code" in WEB_RESEARCH_SYSTEM_PROMPT
    assert "1. Convert the user task" in WEB_RESEARCH_SYSTEM_PROMPT
    assert "2. Use Playwright browser automation" in WEB_RESEARCH_SYSTEM_PROMPT
    assert "3. Scrape useful visible contents" in WEB_RESEARCH_SYSTEM_PROMPT
    assert "4. Extract related links" in WEB_RESEARCH_SYSTEM_PROMPT
    assert "5. Scrape useful visible contents" in WEB_RESEARCH_SYSTEM_PROMPT
    assert RESULT_MARKER in WEB_RESEARCH_SYSTEM_PROMPT


def test_validator_accepts_contract_compliant_playwright_test() -> None:
    assert validate_typescript(SAFE_TS) == []


def test_validator_assets_are_appv3_owned() -> None:
    validator_dir = Path("appV3/web_research/validators")

    assert (validator_dir / "typescript_validator.js").exists()
    assert (validator_dir / "package.json").exists()


def test_validator_blocks_escape_hatches() -> None:
    bad_ts = """
    import { test } from '@playwright/test';
    import { execSync } from 'node:child_process';
    test('bad', async () => {
      console.log(process.env.OPENROUTER_API_KEY);
      execSync('whoami');
      console.log('WEB_RESEARCH_RESULT:{}');
    });
    """

    issues = validate_typescript(bad_ts)

    assert any(issue.rule == "child_process" for issue in issues)
    assert any(issue.rule == "process_env" for issue in issues)
    assert any(issue.rule == "shell_exec" for issue in issues)


def test_extract_marked_json_and_warning_detection() -> None:
    stdout = f'noise\n{RESULT_MARKER}{{"items":[1]}}\n'

    assert extract_marked_json(stdout) == {"items": [1]}
    assert collect_warnings(stdout, "", exit_code=0, parsed_json={"items": [1]}) == []
    assert "missing_result_marker" in collect_warnings("no marker", "", exit_code=0, parsed_json=None)


def test_playwright_runner_accepts_relative_workspace_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    paths = build_runtime_paths(Path("relative_runtime"), "unit")
    paths.tests_dir.mkdir(parents=True)
    runner = PlaywrightToolRunner(paths=paths, install_dependencies=False, install_browsers=False)
    runner.ensure_project = lambda: None  # type: ignore[method-assign]

    class FakeCompleted:
        returncode = 0
        stdout = f'{RESULT_MARKER}{{"sourceLinks":[{{"url":"https://example.com"}}]}}'
        stderr = ""

    monkeypatch.setattr("appV3.web_research.playwright_runner.subprocess.run", lambda *args, **kwargs: FakeCompleted())

    result = runner.run_playwright_code(ToolArgs(code=SAFE_TS, timeout=5))

    assert result.ok is True
    assert result.test_file is not None
    assert result.test_file.startswith("tests/")


def test_tool_broker_dispatches_playwright_runner_and_compacts_result() -> None:
    broker = ToolBroker(playwright_runner=FakeRunner(), max_result_chars=20)  # type: ignore[arg-type]
    decision = parse_decision(
        {
            "status": "tool_calls",
            "tool_calls": [
                {
                    "id": "call1",
                    "name": "run_playwright_code",
                    "args": {"code": SAFE_TS, "save_as": "safe", "timeout": 30},
                }
            ],
            "final_result": None,
        }
    )

    records = broker.run_batch(decision.tool_calls, step=1)
    compact = broker.compact_for_model(records)

    assert records[0].id == "call1"
    assert records[0].result["ok"] is True
    assert compact[0]["result"]["parsed_json"]


def test_broker_advice_for_empty_collection_forces_strategy_change() -> None:
    advice = broker_advice({"warnings": ["empty_collection_detected"], "parsed_json": []})

    assert any("zero usable web evidence" in item for item in advice)
    assert any("change source class" in item for item in advice)
    assert any("sourceLinks/sourcePages/extractiveSummary" in item for item in advice)


def test_loop_guard_message_blocks_repeated_empty_search_strategy() -> None:
    message = loop_guard_message(
        [
            ToolRunRecord(
                id="T001_search",
                name="run_playwright_code",
                args={"code_chars": 500},
                result={"warnings": ["empty_collection_detected"], "parsed_json": []},
            )
        ]
    )

    assert "BROKER_LOOP_GUARD" in message
    assert "Do not retry the same Google" in message
    assert "different source class" in message


def test_runtime_loop_uses_broker_and_finalizes(tmp_path: Path) -> None:
    config = parse_args(
        [
            "--runtime-root",
            str(tmp_path / "web_search_ts"),
            "--thread-id",
            "unit",
            "--skip-npm-install",
            "--skip-browser-install",
        ]
    )
    args = make_turn_args(config, "example research")
    broker = ToolBroker(playwright_runner=FakeRunner())  # type: ignore[arg-type]

    payload = run_agent(args, client=FakeClient(), broker=broker)  # type: ignore[arg-type]

    assert payload["stats"]["tool_run_count"] == 1
    assert payload["stats"]["successful_tool_run_count"] == 1
    assert payload["final_result"]["summary"] == "Example Domain is a placeholder page."
    assert (tmp_path / "web_search_ts" / "unit" / "system_prompt.txt").exists()
    assert (tmp_path / "web_search_ts" / "unit" / "agent_transcript.json").exists()


def test_runtime_rejects_final_answer_when_no_tool_data_ok(tmp_path: Path) -> None:
    config = parse_args(
        [
            "--runtime-root",
            str(tmp_path / "web_search_ts"),
            "--thread-id",
            "empty",
            "--max-steps",
            "1",
            "--skip-npm-install",
            "--skip-browser-install",
        ]
    )
    args = make_turn_args(config, "example research")
    broker = ToolBroker(playwright_runner=EmptyDataRunner())  # type: ignore[arg-type]

    payload = run_agent(args, client=FakeClient(), broker=broker)  # type: ignore[arg-type]

    assert payload["stats"]["successful_tool_run_count"] == 0
    assert payload["final_result"]["summary"] == "No usable research summary was produced."
    assert "extractive fallback" in payload["final_result"]["limitations"][0]


def test_render_markdown_includes_sources() -> None:
    payload = {
        "run_id": "r1",
        "runtime_dir": "/tmp/runtime",
        "model": "google/gemini-3.1-flash-lite",
        "final_result": {
            "summary": "Useful summary",
            "key_findings": ["Finding one"],
            "sources": [{"title": "Source", "url": "https://example.com", "note": "evidence"}],
            "related_sources": [],
            "limitations": [],
        },
        "stats": {"tool_run_count": 1},
    }

    markdown = render_markdown(payload)

    assert "Useful summary" in markdown
    assert "[Source](https://example.com) - evidence" in markdown


def test_build_client_fails_clearly_without_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = parse_args([])
    args = make_turn_args(config, "x")

    with pytest.raises(SystemExit, match="OPENROUTER_API_KEY is required"):
        build_client(args)
