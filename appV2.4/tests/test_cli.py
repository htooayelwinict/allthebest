from __future__ import annotations

import logging
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import appv24.cli as cli
from appv24.ai.env_config import ModelConfig
from appv24.app import CodingApp
from appv24.coding_agent.config import ENV_AGENT_DIR, get_agent_dir
from appv24.ai.models import get_api_key_for_provider, register_model, reset_models
from appv24.ai.types import Model
from appv24.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from appv24.ai.stream import register_api_provider, reset_api_providers
from appv24.workflows import (
    WorkflowActivationStore,
    WorkflowPhaseRunReport,
    WorkflowRunMode,
    WorkflowRunRequest,
    WorkflowRunStore,
    run_workflow,
)
from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import BrokerResult
from appv24.workflows.examples import golden_workflow_examples


def setup_function() -> None:
    reset_api_providers()
    reset_models()


def _assistant_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(role="assistant", content=[SimpleNamespace(type="text", text=text)])


def _write_golden_workflow_package(root: Path, example_name: str) -> Path:
    example = golden_workflow_examples()[example_name]
    package = root / example_name
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(yaml.safe_dump(dict(example.documents.workflow), sort_keys=False), encoding="utf-8")
    (package / "connectors.yaml").write_text(yaml.safe_dump(dict(example.documents.connectors), sort_keys=False), encoding="utf-8")
    (package / "permissions.yaml").write_text(yaml.safe_dump(dict(example.documents.permissions), sort_keys=False), encoding="utf-8")
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    for name, fixture in example.documents.dry_run_fixtures.items():
        (fixtures / name).write_text(yaml.safe_dump(dict(fixture), sort_keys=False), encoding="utf-8")
    if example.documents.rollback_text is not None:
        (package / "rollback.md").write_text(example.documents.rollback_text, encoding="utf-8")
    return package


def _write_live_read_workflow_package(root: Path) -> Path:
    package = root / "dependency-alert-read"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "dependency-alert-read",
                "description": "Read GitHub dependency alerts through a live-read connector gate.",
                "design": {
                    "intent": "Read dependency alerts for operator review.",
                    "trigger": "Manual operator run.",
                    "inputs": ["repo"],
                    "outputs": ["alerts"],
                    "assumptions": ["GitHub credentials are resolved outside the package."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["GitHub API unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "inspect_alerts",
                "steps": [
                    {"id": "inspect_alerts", "uses": "github.list_dependabot_alerts", "next": "done"},
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "github",
                        "adapter": "openapi",
                        "actions": [
                            {
                                "id": "list_dependabot_alerts",
                                "operationId": "dependabot_list_alerts",
                                "side_effect": False,
                                "outputs": {"allowed": ["alerts"]},
                                "required_scopes": ["dependabot:read"],
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [
                    {
                        "connector": "github",
                        "actions": ["list_dependabot_alerts"],
                        "scopes": ["dependabot:read"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  repo: owner/project\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_live_production_workflow_package(root: Path) -> Path:
    package = root / "telegram-production-alert"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "telegram-production-alert",
                "description": "Send one approved Telegram alert.",
                "design": {
                    "intent": "Send one approved Telegram alert to an admin chat.",
                    "trigger": "Manual operator run.",
                    "inputs": ["chat_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["Telegram bot token is resolved outside the package."],
                    "side_effects": ["send Telegram message"],
                    "approval_gates": ["admin_approval"],
                    "failure_modes": ["Telegram API unavailable."],
                    "rollback": ["Delete the Telegram message manually if required."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "notify_admin",
                "steps": [
                    {
                        "id": "notify_admin",
                        "uses": "telegram.send_message",
                        "side_effect_class": "send",
                        "approval": "admin_approval",
                        "with": {"chat_id": {"inputRef": "chat_id"}, "text": {"inputRef": "text"}},
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "send_message",
                                "operationId": "telegram_send_message",
                                "execution_mode": "brokered",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["telegram:send"],
                                "outputs": {"allowed": ["message_id", "chat_id"]},
                                "idempotency": {"key_fields": ["chat_id", "text"]},
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [{"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]}],
                "approvals": [{"id": "admin_approval", "mode": "explicit", "required_for": ["notify_admin"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  chat_id: admin-chat\n  text: Production alert.\n", encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text("expected:\n  status: rejected\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nDelete the Telegram message manually if required.\n", encoding="utf-8")
    return package


def _valid_workflow_draft_text() -> str:
    workflow_yaml = """
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved issue.
design:
  intent: Review dependency alerts and draft a human-approved issue.
  trigger: Weekly scheduled dependency alert review.
  inputs:
    - GitHub dependency alert list.
  outputs:
    - Human-reviewed issue draft.
  assumptions:
    - GitHub credentials are provided by secret reference at execution time.
  side_effects:
    - Create a GitHub issue only after explicit human approval.
  approval_gates:
    - human-review
  failure_modes:
    - GitHub API unavailable.
    - Human rejects the issue draft.
  rollback:
    - Close the drafted issue manually if it was created.
triggers:
  - id: weekly
    type: schedule
    schedule: "0 9 * * 5"
entrypoint: inspect_alerts
steps:
  - id: inspect_alerts
    uses: github.list_dependabot_alerts
    next: draft_issue
  - id: draft_issue
    uses: github.create_issue
    side_effect_class: create
    approval: human-review
    next: done
  - id: done
    type: terminal
""".lstrip()
    connectors_yaml = """
version: appv24.connectors/v1
connectors:
  - id: github
    adapter: openapi
    description: GitHub REST API.
    actions:
      - id: list_dependabot_alerts
        operationId: dependabot_list_alerts
        side_effect: false
      - id: create_issue
        operationId: issues_create
        side_effect: true
        side_effect_class: create
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        idempotency:
          key_fields:
            - repo
            - alert_id
        outputs:
          allowed:
            - issue_number
            - issue_url
        required_scopes:
          - issues:write
""".lstrip()
    permissions_yaml = """
version: appv24.permissions/v1
approvals:
  - id: human-review
    mode: explicit
    required_for:
      - draft_issue
permissions:
  - connector: github
    actions:
      - list_dependabot_alerts
      - create_issue
    scopes:
      - dependabot:read
      - issues:write
""".lstrip()

    return f"""
Here is the package draft.

```yaml workflow.yaml
{workflow_yaml}```

```yaml connectors.yaml
{connectors_yaml}```

```yaml permissions.yaml
{permissions_yaml}```

```yaml dry_run_fixtures/happy_path.yaml
input:
  repo: owner/project
```

```yaml dry_run_fixtures/rejection_path.yaml
approval:
  human-review: rejected
```

```markdown rollback.md
# Rollback

Close the drafted issue if it was created.
```
"""


def _invalid_workflow_draft_text() -> str:
    return _valid_workflow_draft_text().replace("side_effect: true", "side_effect: send")


@pytest.fixture(autouse=True)
def _disable_real_startup_live_catalog_fetch(monkeypatch) -> None:
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "false")


def test_coding_app_plain_mode_does_not_render_live_tui(tmp_path, capsys) -> None:
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "plain reply")))
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), enable_tui=False)
    app.run_turn("hi")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert any(getattr(message, "role", None) == "assistant" for message in app.messages)


def test_cli_without_prompt_starts_interactive_tui(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            created["app"] = self

    class FakeInteractiveMode:
        def __init__(self, app, **kwargs):
            created["mode_app"] = app

        def run(self):
            return 17

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_model_from_env",
        lambda dotenv_path, **kwargs: Model(id="m", name="m", api="faux", provider="faux", base_url=""),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "InteractiveMode", FakeInteractiveMode)

    exit_code = cli.main(["--cwd", str(tmp_path)])

    app = created["app"]
    assert exit_code == 17
    assert created["mode_app"] is app
    assert app.enable_tui is True
    assert app.thinking_level == "off"
    assert app.scoped_models == []


def test_cli_rejects_missing_cwd_before_starting_app(monkeypatch, tmp_path, capsys) -> None:
    missing_cwd = tmp_path / "missing-project"

    def fail_startup(*args, **kwargs):
        raise AssertionError("invalid cwd should stop before provider/model/app startup")

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--cwd", str(missing_cwd), "--plain", "pwd"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert f"Error: working directory does not exist: {missing_cwd.resolve()}" in captured.err


def test_cli_provider_and_model_flags_resolve_registered_model_when_live_catalog_misses(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    selected_model = Model(
        id="qwen/qwen3-coder:exacto",
        name="Qwen3 Coder Exacto",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.example.test/api",
        context_window=128000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(selected_model)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "qwen/qwen3-coder:exacto",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is selected_model
    assert app.enable_tui is False
    assert app.thinking_level == "off"


def test_cli_exact_registered_openrouter_model_prefers_live_metadata(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")
    registered_model = Model(
        id="openai/gpt-5.4-mini",
        name="Old Registered Metadata",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.example.test/api",
        context_window=128000,
        max_tokens=8192,
    )
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(registered_model)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.model.context_window == 400000
    assert app.model.max_tokens == 128000
    assert "Using custom model id" not in captured.err
    assert app.scoped_models == []


def test_cli_startup_live_catalog_force_refreshes_before_stale_cache_fallback(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    observed_kwargs: list[dict[str, object]] = []
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    def fake_live_catalog(**kwargs):
        observed_kwargs.append(dict(kwargs))
        return [live_model]

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fake_live_catalog)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert created["app"].model is live_model
    assert observed_kwargs and observed_kwargs[0]["force_refresh"] is True
    assert "Using custom model id" not in captured.err


def test_cli_unqualified_openrouter_model_id_hydrates_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert created["prompt"] == "inspect"
    assert created["app"].model is live_model
    assert "Using custom model id" not in captured.err


def test_cli_startup_fetch_flag_disables_startup_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    def fail_live_catalog(**kwargs):
        raise AssertionError("startup live catalog should be disabled")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_catalog)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "unknown/vendor-model",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert created["app"].model.provider == "openrouter"
    assert created["app"].model.id == "unknown/vendor-model"
    assert "Using custom model id" in captured.err


def test_cli_model_registry_find_prefers_live_override_over_global_registered_model() -> None:
    registered_model = Model(
        id="openai/gpt-5.4-mini",
        name="Old Registered Metadata",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.example.test/api",
        context_window=128000,
        max_tokens=8192,
    )
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    register_model(registered_model)
    registry = cli._CliModelRegistry([live_model])

    assert registry.find("openrouter", "openai/gpt-5.4-mini") is live_model


def test_cli_matching_live_model_is_case_insensitive() -> None:
    env_model = Model(
        id="OpenAI/GPT-5.4-MINI",
        name="OpenAI/GPT-5.4-MINI",
        api="openai-completions",
        provider="OpenRouter",
        base_url="https://openrouter.example.test/api",
    )
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    assert cli._matching_live_model(env_model, [live_model]) is live_model


def test_cli_default_openrouter_startup_prefers_live_env_model_metadata(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")

    live_model = Model(
        id="moonshotai/kimi-k2.6",
        name="Kimi K2.6 Live",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=256000,
        max_tokens=16384,
        reasoning=True,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.model.context_window == 256000
    assert app.model.max_tokens == 16384
    assert app.scoped_models == []
    assert "Using custom model id" not in captured.err


def test_cli_implicit_default_openrouter_startup_prefers_live_env_model_metadata(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="moonshotai/kimi-k2.6",
        name="Kimi K2.6 Live",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=256000,
        max_tokens=16384,
        reasoning=True,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.scoped_models == []
    assert "Using custom model id" not in captured.err


def test_cli_loads_persisted_auth_before_model_selection(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "auth.json").write_text(
        '{"openrouter": {"type": "api_key", "key": "persisted-key"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("APPV24_CODING_AGENT_DIR", str(agent_dir))

    class FakeApp:
        def __init__(
            self,
            *,
            cwd,
            model,
            enable_tui,
            thinking_level,
            scoped_models,
            **kwargs,
        ):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            observed["app"] = self

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    def record_startup(dotenv_path, **kwargs):
        observed["api_key"] = get_api_key_for_provider("openrouter")
        return cli._StartupModelSelection(
            model=Model(
                id="qwen/qwen3.6-flash",
                name="qwen/qwen3.6-flash",
                api="faux",
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
            )
        )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--plain", "hi"])

    assert exit_code == 0
    assert observed["prompt"] == "hi"
    assert observed["api_key"] == "persisted-key"


def test_appv24_config_reads_appv24_agent_dir(monkeypatch, tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    monkeypatch.delenv(ENV_AGENT_DIR, raising=False)
    monkeypatch.setenv("APPV24_CODING_AGENT_DIR", str(agent_dir))

    assert get_agent_dir() == str(agent_dir)


def test_cli_passes_hermes_loop_runtime_options(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(
            self,
            *,
            cwd,
            model,
            enable_tui,
            thinking_level,
            scoped_models,
            max_iterations=None,
            tool_loop_guardrails=None,
            **kwargs,
        ):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.max_iterations = max_iterations
            self.tool_loop_guardrails = tool_loop_guardrails
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda dotenv_path, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--max-iterations",
            "7",
            "--tool-loop-hard-stop",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert app.max_iterations == 7
    assert app.tool_loop_guardrails == {"hard_stop_enabled": True}
    assert created["prompt"] == "inspect"


def test_cli_default_dotenv_searches_parent_dirs_for_npm_prefix_cwd(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "appV2.4"
    app_dir.mkdir(parents=True)
    project = repo / "project"
    project.mkdir()
    env_path = repo / ".env"
    env_path.write_text(
        "APPV24_WORKER_LLM_ENABLED=true\nOPENROUTER_API_KEY=test-key\n",
        encoding="utf-8",
    )
    (app_dir / ".env").write_text(
        "APPV24_WORKER_LLM_ENABLED=true\nOPENROUTER_API_KEY=wrong-prefix-key\n",
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            observed["app"] = self

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    def record_provider_registration(dotenv_path, config=None):
        observed["registered_dotenv"] = Path(dotenv_path)

    def record_startup(dotenv_path, **kwargs):
        observed["startup_dotenv"] = Path(dotenv_path)
        return cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        )

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    monkeypatch.setattr(cli, "register_builtin_providers", record_provider_registration)
    monkeypatch.setattr(cli, "_startup_model_from_env", record_startup)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(project), "--plain", "inspect"])

    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert observed["registered_dotenv"] == env_path
    assert observed["startup_dotenv"] == env_path


def test_cli_default_cwd_uses_npm_initial_cwd_for_prefix_wrapper(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "appV2.4"
    app_dir.mkdir(parents=True)
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            observed["app"] = self

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda dotenv_path, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--plain", "inspect"])

    app = observed["app"]
    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert app.cwd == str(repo.resolve())


def test_cli_explicit_relative_dotenv_uses_npm_initial_cwd(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    app_dir = repo / "appV2.4"
    app_dir.mkdir(parents=True)
    env_path = repo / ".env"
    env_path.write_text("APPV24_WORKER_LLM_ENABLED=true\n", encoding="utf-8")
    (app_dir / ".env").write_text("APPV24_WORKER_LLM_ENABLED=false\n", encoding="utf-8")
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("INIT_CWD", str(repo))
    monkeypatch.setenv("npm_lifecycle_event", "tui")
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: observed.setdefault("dotenv", dotenv_path))
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda dotenv_path, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url="")
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--dotenv", ".env", "--plain", "inspect"])

    assert exit_code == 0
    assert observed["prompt"] == "inspect"
    assert observed["dotenv"] == env_path


def test_cli_model_thinking_suffix_sets_initial_thinking_level(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    selected_model = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="openai-completions",
        provider="anthropic",
        base_url="https://anthropic.example.test/api",
        reasoning=True,
        context_window=200000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(selected_model)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--model",
            "anthropic/claude-sonnet-4-5:high",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is selected_model
    assert app.thinking_level == "high"


def test_cli_thinking_flag_overrides_model_suffix(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    selected_model = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="openai-completions",
        provider="anthropic",
        base_url="https://anthropic.example.test/api",
        reasoning=True,
        context_window=200000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(selected_model)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--model",
            "anthropic/claude-sonnet-4-5:low",
            "--thinking",
            "high",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is selected_model
    assert app.thinking_level == "high"


def test_cli_invalid_thinking_level_warns_and_uses_default(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--thinking",
            "turbo",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    captured = capsys.readouterr()
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.thinking_level == "off"
    assert 'Invalid thinking level "turbo"' in captured.err


def test_cli_export_session_file_to_html_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    session_path = tmp_path / "session.jsonl"
    output_path = tmp_path / "session.html"
    called: dict[str, object] = {}

    def fake_export_from_file(input_path, options=None):
        called["input_path"] = input_path
        called["options"] = options
        return str(output_path)

    def fail_startup(*args, **kwargs):
        raise AssertionError("export should not initialize providers or app runtime")

    monkeypatch.setattr(cli, "export_from_file", fake_export_from_file, raising=False)
    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--export", str(session_path), str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert called == {"input_path": str(session_path), "options": str(output_path)}
    assert captured.out == f"Exported to: {output_path}\n"
    assert captured.err == ""


def test_cli_models_flag_sets_scoped_models_and_initial_model(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}
    sonnet = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="openai-completions",
        provider="anthropic",
        base_url="https://anthropic.example.test/api",
        reasoning=True,
        context_window=200000,
        max_tokens=8192,
    )
    qwen = Model(
        id="qwen/qwen3-coder:exacto",
        name="Qwen3 Coder Exacto",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.example.test/api",
        context_window=128000,
        max_tokens=8192,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    register_model(sonnet)
    register_model(qwen)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "anthropic/claude-sonnet-4-5:high,openrouter/qwen/qwen3-coder:exacto:low",
            "--plain",
            "inspect",
        ]
    )

    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is sonnet
    assert app.thinking_level == "high"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [
        (sonnet, "high"),
        (qwen, "low"),
    ]


def test_cli_models_unqualified_openrouter_model_id_hydrates_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "openai/gpt-5.4-mini:medium",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.thinking_level == "medium"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [(live_model, "medium")]
    assert "Using custom model id" not in captured.err


def test_cli_list_models_exits_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="step-3.7-flash",
            name="Step 3.7 Flash",
            api="openai-completions",
            provider="stepfun",
            base_url="",
        )
    )
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--list-models"])

    assert code == 0
    assert "stepfun/step-3.7-flash" in capsys.readouterr().out


def test_cli_list_models_without_openrouter_provider_does_not_fetch_live_catalog(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="step-3.7-flash",
            name="Step 3.7 Flash",
            api="openai-completions",
            provider="stepfun",
            base_url="",
        )
    )

    calls = {"count": 0}

    def fail_live_fetch(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("unqualified --list-models should not fetch live OpenRouter catalog")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_fetch)

    code = cli.main(["--cwd", str(tmp_path), "--list-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "stepfun/step-3.7-flash" in captured.out
    assert captured.err == ""
    assert calls["count"] == 0


def test_cli_list_models_includes_live_openrouter_catalog(monkeypatch, tmp_path, capsys) -> None:
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models"])

    assert code == 0
    assert "openrouter/openai/gpt-5.4-mini" in capsys.readouterr().out


def test_cli_list_models_warns_when_openrouter_live_catalog_unavailable(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")
    register_model(
        Model(
            id="known-local",
            name="Known Local",
            api="openai-completions",
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [])

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/known-local" in captured.out
    assert "OpenRouter live model catalog unavailable" in captured.err


def test_cli_list_models_does_not_warn_when_startup_live_catalog_disabled(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="known-local",
            name="Known Local",
            api="openai-completions",
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )
    )

    def fail_live_fetch(*args, **kwargs):
        raise AssertionError("disabled startup live catalog should not fetch")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_fetch)

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/known-local" in captured.out
    assert captured.err == ""


def test_cli_list_models_verbose_shows_live_metadata(monkeypatch, tmp_path, capsys) -> None:
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
        input=["text", "image"],
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models", "--verbose-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/openai/gpt-5.4-mini" in captured.out
    assert "context=400000" in captured.out
    assert "max_tokens=128000" in captured.out
    assert "reasoning=true" in captured.out
    assert "input=text,image" in captured.out


def test_cli_list_providers_exits_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    register_model(
        Model(
            id="step-3.7-flash",
            name="Step 3.7 Flash",
            api="openai-completions",
            provider="stepfun",
            base_url="",
        )
    )
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--list-providers"])

    assert code == 0
    assert "stepfun" in capsys.readouterr().out.splitlines()


def test_cli_provider_stepfun_model_uses_custom_known_provider(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            observed.update(kwargs)
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--provider",
            "stepfun",
            "--model",
            "step-3.7-flash",
            "--plain",
            "noop",
        ]
    )

    assert code == 0
    model = observed["model"]
    assert model.provider == "stepfun"
    assert model.id == "step-3.7-flash"


def test_cli_reads_appv24_worker_llm_prefix(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    env_path = tmp_path / ".env"
    env_path.write_text(
        "APPV24_WORKER_LLM_ENABLED=true\n"
        "APPV24_WORKER_LLM_MODEL=openai/gpt-5.4-mini\n"
        "APPV24_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1\n",
        encoding="utf-8",
    )

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [])

    code = cli.main(["--cwd", str(tmp_path), "--dotenv", str(env_path), "--plain", "inspect"])

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert app.model.provider == "openrouter"
    assert app.model.id == "openai/gpt-5.4-mini"
    assert "APPV24_WORKER_LLM" not in captured.err


def test_cli_startup_hydrates_live_openrouter_model_before_custom_fallback(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.model.context_window == 400000
    assert app.model.max_tokens == 128000
    assert "Using custom model id" not in captured.err


def test_cli_scoped_models_hydrates_exact_live_openrouter_model(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "openrouter/openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.thinking_level == "off"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [(live_model, None)]
    assert "No models match" not in captured.err
    assert "Using custom model id" not in captured.err


def test_cli_scoped_models_hydrates_unqualified_live_openrouter_model_with_thinking(
    monkeypatch, tmp_path, capsys
) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", lambda **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--models",
            "openai/gpt-5.4-mini:medium",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model is live_model
    assert app.thinking_level == "medium"
    assert [(item.model, item.thinking_level) for item in app.scoped_models] == [(live_model, "medium")]
    assert "No models match" not in captured.err
    assert "Using custom model id" not in captured.err


def test_cli_startup_preserves_custom_model_fallback_when_live_catalog_misses(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "unknown/vendor-model",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert app.model.provider == "openrouter"
    assert app.model.id == "unknown/vendor-model"
    assert 'Model "unknown/vendor-model" not found for provider "openrouter". Using custom model id.' in captured.err


def test_cli_startup_preserves_custom_model_fallback_when_live_catalog_raises(
    monkeypatch, tmp_path, capsys, caplog
) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("APPV24_MODEL_CATALOG_STARTUP_FETCH", "true")

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    def fail_live_catalog(*args, **kwargs):
        raise RuntimeError("catalog boom")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "get_live_openrouter_models", fail_live_catalog)

    with caplog.at_level(logging.WARNING, logger="appv24.cli"):
        code = cli.main(
            [
                "--cwd",
                str(tmp_path),
                "--dotenv",
                str(tmp_path / "missing.env"),
                "--provider",
                "openrouter",
                "--model",
                "unknown/vendor-model",
                "--plain",
                "inspect",
            ]
        )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert created["prompt"] == "inspect"
    assert app.model.provider == "openrouter"
    assert app.model.id == "unknown/vendor-model"
    assert 'Model "unknown/vendor-model" not found for provider "openrouter". Using custom model id.' in captured.err
    assert "OpenRouter live model catalog unavailable during startup" in caplog.text
    assert "catalog boom" in caplog.text


def test_cli_generation_flags_are_passed_to_registered_provider(monkeypatch, tmp_path, capsys) -> None:
    observed: dict[str, object] = {}

    def record_registration(dotenv_path, config=None):
        observed["config"] = config

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", record_registration)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "stepfun/step-3.7-flash",
            "--temperature",
            "0.2",
            "--top-p",
            "0.9",
            "--max-tokens",
            "4096",
            "--timeout-seconds",
            "75",
            "--provider-sort",
            "throughput",
            "--stop",
            "END,STOP",
            "--plain",
            "noop",
        ]
    )

    assert code == 0
    params = observed["config"].generation_params
    assert params.temperature == 0.2
    assert params.top_p == 0.9
    assert params.max_tokens == 4096
    assert params.timeout_seconds == 75
    assert params.provider_sort == "throughput"
    assert params.stop == ("END", "STOP")
    assert observed["config"].timeout_seconds == 75
    assert "Warning: generation parameter provider_sort dropped:" in capsys.readouterr().err


def test_cli_passes_generation_params_to_interactive_mode(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []
            self.cwd = kwargs["cwd"]
            self.session = type(
                "FakeSession",
                (),
                {
                    "model": kwargs["model"],
                    "thinking_level": kwargs["thinking_level"],
                    "session_name": "test",
                    "subscribe": lambda self, callback: (lambda: None),
                },
            )()
            self.tui = None

    class FakeInteractiveMode:
        def __init__(self, app, *, generation_params=None, **kwargs):
            observed["generation_params"] = generation_params

        def run(self):
            return 0

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "InteractiveMode", FakeInteractiveMode)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "stepfun/step-3.7-flash",
            "--temperature",
            "0.2",
            "--max-tokens",
            "4096",
            "--tui",
        ]
    )

    assert code == 0
    params = observed["generation_params"]
    assert params.temperature == 0.2
    assert params.max_tokens == 4096


def test_cli_generation_merge_preserves_legacy_config_fields_when_flags_absent() -> None:
    config = ModelConfig(
        enabled=True,
        api_key="k",
        model="m",
        base_url="https://example.test/v1",
        timeout_seconds=45,
        temperature=0,
        top_p=0.8,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        stop=["END"],
        provider_sort="latency",
        max_tokens=2048,
    )
    args = Namespace(
        temperature=None,
        top_p=None,
        max_tokens=None,
        timeout_seconds=None,
        provider_sort=None,
        stop=None,
    )

    merged = cli._config_with_cli_generation_params(config, args)

    assert merged.top_p == 0.8
    assert merged.max_tokens == 2048
    assert merged.provider_sort == "latency"
    assert merged.stop == ["END"]


def test_cli_list_profiles_prints_builtins_without_starting_app(monkeypatch, capsys) -> None:
    def fail_startup(*args, **kwargs):
        raise AssertionError("profile listing should not start providers, models, or app")

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--list-profiles"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "coding" in captured.out
    assert "read-only" in captured.out
    assert "security-lab-lite" in captured.out
    assert "workflow-designer-lite" in captured.out
    assert captured.err == ""


def test_cli_read_only_profile_passes_profile_options_to_coding_app(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "read-only", "--plain", "inspect"])

    kwargs = created["kwargs"]
    assert exit_code == 0
    assert created["prompt"] == "inspect"
    assert kwargs["allowed_tool_names"] == ["read", "grep", "find", "ls"]
    assert kwargs["excluded_tool_names"] == ["bash", "edit", "write"]
    assert kwargs["max_iterations"] == 40
    assert kwargs["tool_loop_guardrails"] == {"hard_stop_enabled": True}
    assert "Do not edit, write, delete, move, or create files." in kwargs["append_system_prompt"]


def test_cli_default_profile_is_coding_noop(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--plain", "inspect"])

    kwargs = created["kwargs"]
    assert exit_code == 0
    assert "allowed_tool_names" not in kwargs
    assert "excluded_tool_names" not in kwargs
    assert "append_system_prompt" not in kwargs


def test_cli_workflow_designer_lite_profile_passes_profile_options_to_coding_app(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        ["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain", "design workflow"]
    )

    kwargs = created["kwargs"]
    app = created["app"]
    assert exit_code == 0
    assert len(app.run_prompts) == 3
    assert "Workflow draft staging 1/3: intent" in app.run_prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in app.run_prompts[1]
    assert "Workflow draft staging 3/3: package" in app.run_prompts[2]
    assert kwargs["allowed_tool_names"] == ["read", "grep", "find", "ls"]
    assert kwargs["excluded_tool_names"] == ["bash", "edit", "write"]
    assert kwargs["max_iterations"] == 60
    assert kwargs["tool_loop_guardrails"] == {"hard_stop_enabled": True}
    assert "workflow-as-code designs" in kwargs["append_system_prompt"]
    assert "final response only" in kwargs["append_system_prompt"]


def test_cli_workflow_designer_lite_prompt_prints_validation_report(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    assistant_text = _valid_workflow_draft_text()

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            created["prompt"] = prompt
            self.messages.append(_assistant_message(assistant_text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "design workflow"])

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert len(app.run_prompts) == 3
    assert "Workflow draft staging 1/3: intent" in app.run_prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in app.run_prompts[1]
    assert "Workflow draft staging 3/3: package" in app.run_prompts[2]
    assert "Here is the package draft." in captured.out
    assert "Workflow draft validation: passed" in captured.out


def test_cli_workflow_profile_stages_prompt_before_validation(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            text = _valid_workflow_draft_text() if "staging 3/3: package" in prompt else "stage note"
            self.messages.append(_assistant_message(text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "design workflow"])

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert len(app.run_prompts) == 3
    assert "Workflow draft staging 1/3: intent" in app.run_prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in app.run_prompts[1]
    assert "Workflow draft staging 3/3: package" in app.run_prompts[2]
    assert "design workflow" in app.run_prompts[0]
    assert "Workflow draft stage: intent 1/3" in captured.out
    assert "Workflow draft stage: skeleton 2/3" in captured.out
    assert "Workflow draft stage: package 3/3" in captured.out
    assert "Workflow draft validation: passed" in captured.out


def test_cli_coding_profile_does_not_stage_prompt(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            self.messages.append(_assistant_message(_valid_workflow_draft_text()))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "coding", "design workflow"])

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert app.run_prompts == ["design workflow"]
    assert "Workflow draft stage:" not in captured.out
    assert "Workflow draft validation:" not in captured.out


def test_cli_workflow_profile_repairs_invalid_draft_before_printing(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            if "Repair attempt" in prompt:
                text = _valid_workflow_draft_text()
            elif "staging 3/3: package" in prompt:
                text = _invalid_workflow_draft_text()
            else:
                text = "stage note"
            self.messages.append(_assistant_message(text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "design workflow"])

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert "Workflow draft staging 1/3: intent" in app.run_prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in app.run_prompts[1]
    assert "Workflow draft staging 3/3: package" in app.run_prompts[2]
    assert "Repair attempt 1." in app.run_prompts[3]
    assert "Repair attempt 1/" not in app.run_prompts[3]
    assert "Workflow draft repair: attempt 1" in captured.out
    assert "Workflow draft repair: attempt 1/" not in captured.out
    assert "Workflow draft validation: passed" in captured.out
    assert "side_effect: send" not in captured.out


def test_cli_workflow_repair_provider_error_reports_failure(monkeypatch, tmp_path, capsys) -> None:
    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            if "Repair attempt" in prompt:
                raise RuntimeError("provider exploded")
            text = _invalid_workflow_draft_text() if "staging 3/3: package" in prompt else "stage note"
            self.messages.append(_assistant_message(text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "design workflow"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft repair failed: provider exploded" in captured.out
    assert "Traceback" not in captured.err


def test_cli_workflow_validation_report_includes_repair_stop_reason(capsys) -> None:
    validation = cli.validate_workflow_draft_text(_invalid_workflow_draft_text())
    repair_result = cli.WorkflowDraftRepairResult(
        validation=validation,
        attempts=2,
        repaired=False,
        exhausted=True,
        stop_reason="repeated_validation",
    )

    cli._print_workflow_validation_if_needed(
        SimpleNamespace(messages=[]),
        "workflow-designer-lite",
        repair_result,
    )

    captured = capsys.readouterr()
    assert "Workflow draft repair stopped: repeated_validation after 2 attempt(s)" in captured.out
    assert "Workflow draft validation: failed" in captured.out


def test_cli_plain_workflow_save_writes_last_valid_draft(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    assistant_text = _valid_workflow_draft_text()
    prompts = iter(["design workflow", "/workflow-save saved-workflow", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            self.messages.append(_assistant_message(assistant_text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    saved_path = tmp_path / "saved-workflow"
    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert len(app.run_prompts) == 3
    assert "Workflow draft staging 1/3: intent" in app.run_prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in app.run_prompts[1]
    assert "Workflow draft staging 3/3: package" in app.run_prompts[2]
    assert saved_path.joinpath("workflow.yaml").is_file()
    assert saved_path.joinpath("dry_run_fixtures", "happy_path.yaml").is_file()
    assert "Workflow draft saved:" in captured.out
    assert str(saved_path.resolve()) in captured.out
    assert "Workflow draft validation: passed" in captured.out
    assert "Workflow draft save failed:" not in captured.out
    assert app.cwd == tmp_path.resolve()


def test_cli_plain_workflow_explain_summarizes_saved_package_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    prompts = iter([f"/workflow-explain {package.name}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow explain command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Package: telegram_support_ticket" in captured.out
    assert "Validation: passed" in captured.out
    assert "ticket.create_draft" in captured.out
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_run_explain_summarizes_saved_run_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=True,
        )
    )
    prompts = iter([f"/workflow-run-explain {package.name} {report.run_id}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow run explain command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow run: passed" in captured.out
    assert "terminal: done" in captured.out
    assert "notify_admin: telegram.send_message -> simulated" in captured.out
    assert len(list((package / ".workflow-runs").glob("run-*"))) == 1


def test_cli_plain_workflow_deny_records_approval_decision(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(report.run_id)[0]
    prompts = iter([f"/workflow-deny {package.name} {report.run_id} {request.approval_request_id}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow approval command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    decisions = store.read_approval_decisions(report.run_id)
    assert exit_code == 0
    assert "Workflow approval recorded: denied" in captured.out
    assert f"request: {request.approval_request_id}" in captured.out
    assert "action: ticket.create_draft" in captured.out
    assert decisions[0]["approved"] is False
    assert decisions[0]["status"] == "denied"


def test_cli_plain_workflow_approvals_lists_request_ids_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "gmail_draft_reply")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(report.run_id)[0]
    prompts = iter([f"/workflow-approvals {package.name} {report.run_id}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow approvals command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Workflow approvals: {report.run_id}" in captured.out
    assert request.approval_request_id in captured.out
    assert "pending create_gmail_draft gmail.create_draft" in captured.out


def test_cli_plain_workflow_live_draft_preflight_passes_with_env_credentials(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "gmail_draft_reply")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(report.run_id)[0]
    store.record_approval_decision(
        report.run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=report.package_fingerprint, decided_by="test"),
    )
    prompts = iter([f"/workflow-live-draft-preflight {package.name} happy_path.yaml {report.run_id} --allow gmail.create_draft", "/exit"])
    monkeypatch.setenv("APPV24_GMAIL_CREDENTIAL_REF", "gmail-prod")
    monkeypatch.setenv("APPV24_GMAIL_SCOPES", "gmail.compose")
    monkeypatch.setenv("APPV24_WORKFLOW_LIVE_DRAFT_ENABLED", "true")
    monkeypatch.setenv("APPV24_GMAIL_ACCESS_TOKEN", "gmail-token")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow preflight command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Live-draft preflight: passed" in captured.out
    assert "workflow: gmail_draft_reply" in captured.out
    assert "- gmail.create_draft" in captured.out
    assert len(list((package / ".workflow-runs").glob("run-*"))) == 1


def test_cli_plain_workflow_live_draft_preflight_blocks_without_transport_registry(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "gmail_draft_reply")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(report.run_id)[0]
    store.record_approval_decision(
        report.run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=report.package_fingerprint, decided_by="test"),
    )
    prompts = iter([f"/workflow-live-draft-preflight {package.name} happy_path.yaml {report.run_id} --allow gmail.create_draft", "/exit"])
    monkeypatch.setenv("APPV24_GMAIL_CREDENTIAL_REF", "gmail-prod")
    monkeypatch.setenv("APPV24_GMAIL_SCOPES", "gmail.compose")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow preflight command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Live-draft preflight: blocked" in captured.out
    assert "live-draft transport is not configured for gmail.create_draft" in captured.out


def test_cli_plain_workflow_live_read_preflight_passes_without_model_or_run(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_read_workflow_package(tmp_path)
    prompts = iter(
        [
            f"/workflow-live-read-preflight {package.name} happy_path.yaml --allow github.list_dependabot_alerts",
            "/exit",
        ]
    )
    monkeypatch.setenv("APPV24_GITHUB_CREDENTIAL_REF", "github-prod")
    monkeypatch.setenv("APPV24_GITHUB_SCOPES", "dependabot:read")
    monkeypatch.setenv("APPV24_WORKFLOW_LIVE_READ_ENABLED", "true")
    monkeypatch.setenv("APPV24_GITHUB_TOKEN", "github-token")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-read preflight command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Live-read preflight: passed" in captured.out
    assert "workflow: dependency-alert-read" in captured.out
    assert "- github.list_dependabot_alerts" in captured.out
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_live_read_preflight_blocks_without_transport_registry(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_read_workflow_package(tmp_path)
    prompts = iter(
        [
            f"/workflow-live-read-preflight {package.name} happy_path.yaml --allow github.list_dependabot_alerts",
            "/exit",
        ]
    )
    monkeypatch.setenv("APPV24_GITHUB_CREDENTIAL_REF", "github-prod")
    monkeypatch.setenv("APPV24_GITHUB_SCOPES", "dependabot:read")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-read preflight command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Live-read preflight: blocked" in captured.out
    assert "live-read transport is not configured for github.list_dependabot_alerts" in captured.out


def test_cli_plain_workflow_live_ready_reports_setup_without_model_or_run(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_read_workflow_package(tmp_path)
    prompts = iter(
        [
            f"/workflow-live-ready live-read {package.name} happy_path.yaml --allow github.list_dependabot_alerts",
            "/exit",
        ]
    )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live readiness command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow live readiness: blocked" in captured.out
    assert "mode: live-read" in captured.out
    assert "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN" in captured.out
    assert "model should not run" not in captured.out
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_live_env_template_without_model_or_run(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_read_workflow_package(tmp_path)
    prompts = iter(
        [
            f"/workflow-live-env-template live-read {package.name} --allow github.list_dependabot_alerts",
            "/exit",
        ]
    )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live env template command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow live env template: live-read" in captured.out
    assert "APPV24_GITHUB_CREDENTIAL_REF=<credential-ref>" in captured.out
    assert "APPV24_GITHUB_TOKEN=<github-token>" in captured.out
    assert "model should not run" not in captured.out
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_live_production_preflight_passes_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_production_workflow_package(tmp_path)
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(approval_report.run_id)[0]
    store.record_approval_decision(
        approval_report.run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=approval_report.package_fingerprint, decided_by="test"),
    )
    prompts = iter(
        [
            f"/workflow-live-production-preflight {package.name} happy_path.yaml {approval_report.run_id} --allow telegram.send_message",
            "/exit",
        ]
    )
    monkeypatch.setenv("APPV24_TELEGRAM_CREDENTIAL_REF", "telegram-prod")
    monkeypatch.setenv("APPV24_TELEGRAM_SCOPES", "telegram:send")
    monkeypatch.setenv("APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("APPV24_TELEGRAM_BOT_TOKEN", "telegram-token")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-production preflight command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Live-production preflight: passed" in captured.out
    assert "workflow: telegram-production-alert" in captured.out
    assert "- telegram.send_message" in captured.out
    assert len(list((package / ".workflow-runs").glob("run-*"))) == 1


def test_cli_plain_workflow_export_intent_prints_checklist_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    prompts = iter([f"/workflow-export-intent {package.name}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow export command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Deployment intent: telegram_support_ticket" in captured.out
    assert "- APPV24_TICKET_CREDENTIAL_REF" in captured.out
    assert "- webhook: telegram_message" in captured.out
    assert "- admin_approval: create_ticket_draft(ticket.create_draft), notify_admin(telegram.send_message)" in captured.out
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_live_plan_prints_phases_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    prompts = iter([f"/workflow-live-plan {package.name} happy_path.yaml", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live plan command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow live plan: partial-live" in captured.out
    assert "create_ticket_draft: ticket.create_draft" in captured.out
    assert "notify_admin: telegram.send_message" in captured.out
    assert "phase sequence:" in captured.out
    assert "1 local: check_authorized_customer: decision -> authorized" in captured.out
    assert "2 live-draft: create_ticket_draft: ticket.create_draft (transport gap)" in captured.out
    assert "3 live-production: notify_admin: telegram.send_message" in captured.out
    assert "/workflow-run-approval" in captured.out
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_run_approval_creates_pending_request_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "gmail_draft_reply")
    prompts = iter([f"/workflow-run-approval {package.name} happy_path.yaml", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow approval prep command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    run_dirs = sorted((package / ".workflow-runs").glob("run-*"))
    store = WorkflowRunStore(package / ".workflow-runs")
    requests = store.read_approval_requests(run_dirs[0].name)
    assert exit_code == 0
    assert "Workflow approval prep run: blocked" in captured.out
    assert "approval required for create_gmail_draft" in captured.out
    assert len(requests) == 1
    assert requests[0].status == "pending"
    assert requests[0].step_id == "create_gmail_draft"
    assert requests[0].connector_action == "gmail.create_draft"


def test_cli_plain_workflow_run_local_runs_saved_package_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    prompts = iter([f"/workflow-run-local {package.name} happy_path.yaml", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow local run command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow local run: passed" in captured.out
    assert "terminal: done" in captured.out
    assert "brokered actions: 2" in captured.out
    assert len(list((package / ".workflow-runs").glob("run-*"))) == 1


def test_cli_plain_workflow_run_sandbox_runs_saved_package_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    prompts = iter([f"/workflow-run-sandbox {package.name} happy_path.yaml", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow sandbox run command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow sandbox run: passed" in captured.out
    assert "terminal: done" in captured.out
    assert len(list((package / ".workflow-runs").glob("run-*"))) == 1


def test_cli_plain_workflow_run_live_read_creates_blocked_run_without_transport(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_read_workflow_package(tmp_path)
    prompts = iter(
        [
            f"/workflow-run-live-read {package.name} happy_path.yaml --allow github.list_dependabot_alerts",
            "/exit",
        ]
    )
    monkeypatch.setenv("APPV24_GITHUB_CREDENTIAL_REF", "github-prod")
    monkeypatch.setenv("APPV24_GITHUB_SCOPES", "dependabot:read")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-read command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    run_dirs = sorted((package / ".workflow-runs").glob("run-*"))
    assert exit_code == 0
    assert "Workflow live-read run: blocked" in captured.out
    assert "live transport is not configured for github.list_dependabot_alerts" in captured.out
    assert len(run_dirs) == 1


def test_cli_plain_workflow_run_live_read_uses_env_transport_registry(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_read_workflow_package(tmp_path)
    prompts = iter(
        [
            f"/workflow-run-live-read {package.name} happy_path.yaml --allow github.list_dependabot_alerts",
            "/exit",
        ]
    )
    monkeypatch.setenv("APPV24_GITHUB_CREDENTIAL_REF", "github-prod")
    monkeypatch.setenv("APPV24_GITHUB_SCOPES", "dependabot:read")

    class FakeTransport:
        def invoke(self, request):
            return BrokerResult(
                status="live-read",
                provider_id="fake-live",
                outputs={"alerts": [{"number": 1}], "count": 1},
            )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-read command: {prompt}")

    monkeypatch.setattr(
        cli,
        "build_live_read_transports_from_env",
        lambda env: {"github.list_dependabot_alerts": FakeTransport()},
        raising=False,
    )
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    run_dirs = sorted((package / ".workflow-runs").glob("run-*"))
    assert exit_code == 0
    assert "Workflow live-read run: passed" in captured.out
    assert "inspect_alerts: github.list_dependabot_alerts -> live-read" in captured.out
    assert len(run_dirs) == 1


def test_cli_plain_workflow_run_live_draft_creates_blocked_run_without_transport(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "gmail_draft_reply")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(approval_report.run_id)[0]
    store.record_approval_decision(
        approval_report.run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=approval_report.package_fingerprint, decided_by="test"),
    )
    prompts = iter([f"/workflow-run-live-draft {package.name} happy_path.yaml {approval_report.run_id} --allow gmail.create_draft", "/exit"])
    monkeypatch.setenv("APPV24_GMAIL_CREDENTIAL_REF", "gmail-prod")
    monkeypatch.setenv("APPV24_GMAIL_SCOPES", "gmail.compose")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-draft command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    run_dirs = sorted((package / ".workflow-runs").glob("run-*"))
    assert exit_code == 0
    assert "Workflow live-draft run: blocked" in captured.out
    assert "live transport is not configured for gmail.create_draft" in captured.out
    assert len(run_dirs) == 2


def test_cli_plain_workflow_run_live_draft_uses_env_transport_registry(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "gmail_draft_reply")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(approval_report.run_id)[0]
    store.record_approval_decision(
        approval_report.run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=approval_report.package_fingerprint, decided_by="test"),
    )
    prompts = iter([f"/workflow-run-live-draft {package.name} happy_path.yaml {approval_report.run_id} --allow gmail.create_draft", "/exit"])
    monkeypatch.setenv("APPV24_GMAIL_CREDENTIAL_REF", "gmail-prod")
    monkeypatch.setenv("APPV24_GMAIL_SCOPES", "gmail.compose")

    class FakeTransport:
        def invoke(self, request):
            return BrokerResult(
                status="live-draft",
                provider_id="fake-live",
                outputs={"draft_id": "draft-1", "thread_id": "thread-1"},
            )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-draft command: {prompt}")

    monkeypatch.setattr(
        cli,
        "build_live_draft_transports_from_env",
        lambda env: {"gmail.create_draft": FakeTransport()},
        raising=False,
    )
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    run_dirs = sorted((package / ".workflow-runs").glob("run-*"))
    assert exit_code == 0
    assert "Workflow live-draft run: passed" in captured.out
    assert "create_gmail_draft: gmail.create_draft -> live-draft" in captured.out
    assert len(run_dirs) == 2


def test_cli_plain_workflow_run_live_production_uses_env_transport_registry(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_production_workflow_package(tmp_path)
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(package / ".workflow-runs")
    request = store.read_approval_requests(approval_report.run_id)[0]
    store.record_approval_decision(
        approval_report.run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=approval_report.package_fingerprint, decided_by="test"),
    )
    prompts = iter([f"/workflow-run-live-production {package.name} happy_path.yaml {approval_report.run_id} --allow telegram.send_message", "/exit"])
    monkeypatch.setenv("APPV24_TELEGRAM_CREDENTIAL_REF", "telegram-prod")
    monkeypatch.setenv("APPV24_TELEGRAM_SCOPES", "telegram:send")

    class FakeTransport:
        def invoke(self, request):
            return BrokerResult(
                status="live-production",
                provider_id="fake-live",
                outputs={"message_id": "msg-1", "chat_id": "admin-chat"},
            )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow live-production command: {prompt}")

    called: dict[str, object] = {}

    def fake_phase_runner(request, *, approval_run_id, live_production_broker, credential_resolver):
        called["approval_run_id"] = approval_run_id
        live_report = cli.run_live_production_workflow(
            WorkflowRunRequest(
                package_path=request.package_path,
                fixture_name=request.fixture_name,
                mode=WorkflowRunMode.LIVE_PRODUCTION,
                run_root=request.run_root,
                approval_run_id=approval_run_id,
                connector_allowlist=request.connector_allowlist,
            ),
            broker=live_production_broker,
            credential_resolver=credential_resolver,
        )
        return WorkflowPhaseRunReport(
            status=live_report.status,
            live_read_run_id="",
            local_run_id="",
            approval_run_id=approval_run_id,
            phase_statuses=(f"live-production: {live_report.status}",),
            next_commands=(),
            live_production_run_id=live_report.run_id,
            live_production_report=live_report,
            issues=live_report.issues,
        )

    monkeypatch.setattr(
        cli,
        "build_live_production_transports_from_env",
        lambda env: {"telegram.send_message": FakeTransport()},
        raising=False,
    )
    monkeypatch.setattr(cli, "run_workflow_approved_live_production", fake_phase_runner, raising=False)
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    run_dirs = sorted((package / ".workflow-runs").glob("run-*"))
    assert exit_code == 0
    assert called["approval_run_id"] == approval_report.run_id
    assert "Workflow live-production run: passed" in captured.out
    assert "notify_admin: telegram.send_message -> live-production" in captured.out
    assert len(run_dirs) == 2


def test_cli_plain_workflow_connectors_renders_safe_registry(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_production_workflow_package(tmp_path)
    prompts = iter([f"/workflow-connectors {package.name}", "/exit"])
    monkeypatch.setenv("APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("APPV24_TELEGRAM_BOT_TOKEN", "telegram-secret-token")
    monkeypatch.setenv("APPV24_TELEGRAM_CREDENTIAL_REF", "telegram-prod")
    monkeypatch.setenv("APPV24_TELEGRAM_SCOPES", "telegram:send")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow connectors command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow connector registry:" in captured.out
    assert "- telegram.send_message: cataloged" in captured.out
    assert "live modes: live-production" in captured.out
    assert "configured: live-production" in captured.out
    assert "APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES=telegram:send" in captured.out
    assert "telegram-secret-token" not in captured.out


def test_cli_plain_workflow_export_bundle_writes_bundle_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_production_workflow_package(tmp_path)
    prompts = iter([f"/workflow-export-bundle {package.name}", "/exit"])
    monkeypatch.setenv("APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("APPV24_TELEGRAM_BOT_TOKEN", "telegram-secret-token")
    monkeypatch.setenv("APPV24_TELEGRAM_CREDENTIAL_REF", "telegram-prod")
    monkeypatch.setenv("APPV24_TELEGRAM_SCOPES", "telegram:send")

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow bundle export command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    bundle_dir = package / ".workflow-bundle"
    assert exit_code == 0
    assert "Workflow bundle exported:" in captured.out
    assert "workflow: telegram-production-alert" in captured.out
    assert "runtime mode: export-only" in captured.out
    assert (bundle_dir / "workflow.ir.json").is_file()
    assert (bundle_dir / "connectors.manifest.json").is_file()
    assert (bundle_dir / "runtime.json").is_file()
    assert "telegram-secret-token" not in captured.out
    assert "telegram-secret-token" not in (bundle_dir / "secrets.required.json").read_text(encoding="utf-8")
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_bundle_check_verifies_export_without_model(monkeypatch, tmp_path, capsys) -> None:
    package = _write_live_production_workflow_package(tmp_path)
    bundle_dir = package / ".workflow-bundle"
    prompts = iter([f"/workflow-export-bundle {package.name}", f"/workflow-bundle-check {package.name}/.workflow-bundle", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError(f"model should not run for workflow bundle check command: {prompt}")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow bundle check: passed" in captured.out
    assert f"bundle: {bundle_dir.resolve()}" in captured.out
    assert "files checked:" in captured.out
    assert "runtime.json" in captured.out
    assert not (package / ".workflow-runs").exists()


def test_cli_plain_workflow_save_rejects_parent_traversal(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    assistant_text = _valid_workflow_draft_text()
    outside_name = f"{tmp_path.name}-outside-workflow-save"
    prompts = iter(["design workflow", f"/workflow-save ../{outside_name}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            created["app"] = self
            self.cwd = Path(kwargs["cwd"])
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            self.messages.append(_assistant_message(assistant_text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(created["app"].run_prompts) == 3
    assert "Workflow draft staging 1/3: intent" in created["app"].run_prompts[0]
    assert "Workflow draft staging 2/3: skeleton" in created["app"].run_prompts[1]
    assert "Workflow draft staging 3/3: package" in created["app"].run_prompts[2]
    assert "Workflow draft save failed:" in captured.out
    assert "must stay inside" in captured.out
    assert not (tmp_path.parent / outside_name).exists()


def test_cli_plain_workflow_save_rejects_absolute_path(monkeypatch, tmp_path, capsys) -> None:
    assistant_text = _valid_workflow_draft_text()
    absolute_target = tmp_path.parent / "appv24-absolute-save"
    prompts = iter(["design workflow", f"/workflow-save {absolute_target}", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            self.messages.append(_assistant_message(assistant_text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft save failed:" in captured.out
    assert "relative" in captured.out.lower() or "inside" in captured.out.lower()
    assert not absolute_target.exists()


def test_cli_plain_workflow_save_refuses_overwrite(monkeypatch, tmp_path, capsys) -> None:
    assistant_text = _valid_workflow_draft_text()
    existing = tmp_path / "saved-workflow"
    existing.mkdir()
    prompts = iter(["design workflow", "/workflow-save saved-workflow", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            self.messages.append(_assistant_message(assistant_text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft save failed:" in captured.out
    assert "already exists" in captured.out
    assert not existing.joinpath("workflow.yaml").exists()


def test_cli_plain_workflow_save_fails_outside_workflow_profile(monkeypatch, tmp_path, capsys) -> None:
    prompts = iter(["/workflow-save saved-workflow", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("save command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "coding", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft save failed: active profile is not workflow-designer-lite" in captured.out
    assert not (tmp_path / "saved-workflow").exists()


def test_cli_plain_workflow_activate_creates_activation(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    prompts = iter([f"/workflow-activate {package.name} happy_path.yaml", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("activation command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    activation_root = tmp_path / ".appv24" / "workflow-activations"
    activations = list(activation_root.glob("activation-*"))
    assert exit_code == 0
    assert "Workflow activation created:" in captured.out
    assert "workflow: telegram_support_ticket" in captured.out
    assert "trigger: manual" in captured.out
    assert len(activations) == 1
    assert (activations[0] / "activation.json").is_file()


def test_cli_plain_workflow_activation_run_status_and_history(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    prompts = iter(
        [
            f"/workflow-activation-run {activation.activation_id}",
            f"/workflow-activation-status {activation.activation_id}",
            f"/workflow-activation-history {activation.activation_id}",
            "/exit",
        ]
    )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("activation command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow activation run: passed" in captured.out
    assert f"activation: {activation.activation_id}" in captured.out
    assert "terminal: done" in captured.out
    assert "Workflow activation status:" in captured.out
    assert "last status: passed" in captured.out
    assert "Workflow activation history:" in captured.out
    assert "run-1:" in captured.out
    assert len(list((store.root / activation.activation_id / "runs").glob("run-*"))) == 1


def test_cli_plain_workflow_activation_run_explain(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    report = store.run_activation(activation.activation_id)
    prompts = iter(
        [
            f"/workflow-activation-run-explain {activation.activation_id} {report.run_id}",
            "/exit",
        ]
    )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("activation command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow run: passed" in captured.out
    assert f"run id: {report.run_id}" in captured.out
    assert "terminal: done" in captured.out
    assert "notify_admin: telegram.send_message -> simulated" in captured.out


def test_cli_plain_workflow_activation_rejects_path_like_id(monkeypatch, tmp_path, capsys) -> None:
    prompts = iter(["/workflow-activation-status ../activation-bad", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("activation command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow activation failed:" in captured.out
    assert "activation id must not contain path traversal" in captured.out


def test_cli_plain_workflow_activation_list(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    prompts = iter(["/workflow-activation-list", "/exit"])

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("activation command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow activations:" in captured.out
    assert activation.activation_id in captured.out
    assert "telegram_support_ticket" in captured.out
    assert "manual" in captured.out


def test_cli_plain_workflow_activation_status_and_list_show_package_drift(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("telegram_support_ticket", "changed_ticket_workflow"),
        encoding="utf-8",
    )
    prompts = iter(
        [
            f"/workflow-activation-status {activation.activation_id}",
            "/workflow-activation-list",
            "/exit",
        ]
    )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("activation command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "package: changed" in captured.out
    assert "package=changed" in captured.out


def test_cli_plain_workflow_activation_disable_and_enable(monkeypatch, tmp_path, capsys) -> None:
    package = _write_golden_workflow_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    prompts = iter(
        [
            f"/workflow-activation-disable {activation.activation_id}",
            f"/workflow-activation-run {activation.activation_id}",
            f"/workflow-activation-enable {activation.activation_id}",
            f"/workflow-activation-run {activation.activation_id}",
            "/exit",
        ]
    )

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt):
            raise AssertionError("activation command should not call the model")

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow activation disabled:" in captured.out
    assert "Workflow activation failed: workflow activation is disabled" in captured.out
    assert "Workflow activation enabled:" in captured.out
    assert "Workflow activation run: passed" in captured.out
    assert store.get_activation(activation.activation_id).enabled is True


def test_cli_coding_prompt_does_not_print_workflow_validation_report(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}
    assistant_text = _valid_workflow_draft_text()

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            created["prompt"] = prompt
            self.messages.append(_assistant_message(assistant_text))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "coding", "design workflow"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert created["prompt"] == "design workflow"
    assert "Here is the package draft." in captured.out
    assert "Workflow draft validation:" not in captured.out


def test_cli_coding_profile_does_not_repair_workflow_draft(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["app"] = self
            self.messages = []
            self.run_prompts: list[str] = []

        def run_turn(self, prompt):
            self.run_prompts.append(prompt)
            self.messages.append(_assistant_message(_invalid_workflow_draft_text()))

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "coding", "answer normally"])

    captured = capsys.readouterr()
    app = created["app"]
    assert exit_code == 0
    assert app.run_prompts == ["answer normally"]
    assert "Workflow draft repair:" not in captured.out
    assert "Workflow draft validation:" not in captured.out


def test_cli_unknown_profile_stops_before_provider_startup(monkeypatch, tmp_path, capsys) -> None:
    def fail_startup(*args, **kwargs):
        raise AssertionError("unknown profile should stop before provider/model/app startup")

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--cwd", str(tmp_path), "--profile", "missing", "--plain", "inspect"])

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert 'Unknown profile "missing"' in captured.err


def test_cli_flags_override_profile_defaults(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            observed["app_kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            observed["prompt"] = prompt

    def fake_startup_model_from_env(*args, **kwargs):
        observed["startup_kwargs"] = kwargs
        return cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_startup_model_from_env", fake_startup_model_from_env)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--profile",
            "planning",
            "--provider",
            "openrouter",
            "--model",
            "qwen/qwen3.7-max",
            "--thinking",
            "high",
            "--max-iterations",
            "12",
            "--tool-loop-hard-stop",
            "--plain",
            "inspect",
        ]
    )

    startup_kwargs = observed["startup_kwargs"]
    app_kwargs = observed["app_kwargs"]
    assert exit_code == 0
    assert startup_kwargs["cli_provider"] == "openrouter"
    assert startup_kwargs["cli_model"] == "qwen/qwen3.7-max"
    assert startup_kwargs["cli_thinking"] == "high"
    assert app_kwargs["thinking_level"] == "high"
    assert app_kwargs["max_iterations"] == 12
    assert app_kwargs["tool_loop_guardrails"] == {"hard_stop_enabled": True}
