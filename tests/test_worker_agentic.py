import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.schemas import ArtifactPayload, Envelope, MutationScope, Plan, PlanStep, Task, resolve_mutation_scope_proposal
from app.runtime_matrix import RuntimeMatrixLogger
from app.worker_kernel.compiler import TaskCompiler
from app.worker_kernel.agentic import (
    AgenticWorkerGroupRunner,
    WorkerInstanceTemplate,
    WorkerLLMController,
    _normalize_worker_decision,
)
from app.worker_kernel.artifact_contracts import artifact_contract, evaluate_artifact_quality
from app.worker_kernel.model_adapter import WorkerModelDecisionError
from app.worker_kernel.env_config import build_worker_model_client, load_worker_runtime_config
from app.worker_kernel.runtime import WorkerKernelRuntime
from app.worker_kernel.tools import (
    MutationOperationDeniedError,
    ToolExecutionError,
    ToolPermissionError,
    WorkerToolConfig,
    WorkerToolbox,
    _is_allowed_uv_pytest_command,
)
from app.worker_kernel.workers.agentic_templates import get_agentic_worker_templates


def _task(
    *,
    worker_type: str = "repo_worker",
    expected_outputs: list[str] | None = None,
    permissions: dict[str, Any] | None = None,
    max_tool_calls: int = 3,
    max_model_calls: int = 2,
) -> Task:
    return Task(
        task_id="task_1",
        run_id="run_1",
        step_id="step_1",
        worker_type=worker_type,
        instruction="complete scoped worker task",
        expected_outputs=expected_outputs or ["final_artifact"],
        max_tool_calls=max_tool_calls,
        max_model_calls=max_model_calls,
        permissions=permissions
        or {
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    )


class FakeConfiguredClient:
    configs: list[dict[str, Any]] = []

    def __init__(self, **config: Any) -> None:
        type(self).configs.append(config)


class QueueClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.stages: list[str] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.stages.append(stage)
        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0))


class RawQueueClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.stages: list[str] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.stages.append(stage)
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_worker_env_config_builds_openrouter_compatible_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_LLM_ENABLED", raising=False)
    monkeypatch.delenv("WORKER_LLM_API_KEY", raising=False)
    monkeypatch.delenv("WORKER_LLM_MODEL", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "WORKER_LLM_ENABLED=true",
                "WORKER_LLM_API_KEY=test-key",
                "WORKER_LLM_MODEL=test-model",
                "WORKER_LLM_BASE_URL=https://worker.example/v1",
                "WORKER_LLM_PROVIDER_SORT=latency",
                "WORKER_MAX_PARALLEL_INSTANCES=2",
                "WORKER_TOOL_TIMEOUT_SECONDS=7",
                "WORKER_MAX_FILE_BYTES=1234",
                "WORKER_WEB_SEARCH_PROVIDER=duckduckgo",
                "WORKER_WEB_SEARCH_API_KEY=brave-key",
                "WORKER_WEB_SEARCH_MAX_RESULTS=7",
                "WORKER_RETRY_ADVISOR_ENABLED=true",
                "WORKER_RETRY_ADVISOR_MODEL=advisor-model",
                "WORKER_RETRY_ADVISOR_MAX_TOKENS=321",
                "WORKER_RETRY_ADVISOR_TIMEOUT_SECONDS=12",
            ]
        ),
        encoding="utf-8",
    )

    config = load_worker_runtime_config(dotenv)
    client = build_worker_model_client(dotenv, client_factory=FakeConfiguredClient)

    assert client is not None
    assert config.llm_enabled is True
    assert config.max_parallel_instances == 2
    assert config.tool_timeout_seconds == 7
    assert config.max_file_bytes == 1234
    assert config.web_search_provider == "duckduckgo"
    assert config.web_search_api_key == "brave-key"
    assert config.web_search_max_results == 7
    assert config.retry_advisor_enabled is True
    assert config.retry_advisor_model == "advisor-model"
    assert config.retry_advisor_max_tokens == 321
    assert config.retry_advisor_timeout_seconds == 12
    assert FakeConfiguredClient.configs[-1]["api_key"] == "test-key"
    assert FakeConfiguredClient.configs[-1]["model"] == "test-model"
    assert FakeConfiguredClient.configs[-1]["base_url"] == "https://worker.example/v1"
    assert FakeConfiguredClient.configs[-1]["provider_sort"] == "latency"


def test_worker_from_env_falls_back_to_stub_registry_when_disabled(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("WORKER_LLM_ENABLED=false\n", encoding="utf-8")
    runtime = WorkerKernelRuntime.from_env(str(dotenv))

    result = runtime.run(
        Plan.model_validate(
            {
            "plan_id": "plan_direct",
            "request_id": "req_direct",
            "planner": "test",
            "objective": "answer",
            "strategy": "direct",
            "steps": [
                {
                    "step_id": "answer",
                    "worker_type": "direct_worker",
                    "instruction": "answer",
                    "output_artifacts": ["direct_answer"],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                    "permissions": {
                        "read_files": False,
                        "write_files": False,
                        "run_commands": False,
                        "web_research": False,
                    },
                }
            ],
            "budget": {"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
            }
        )
    )

    assert result.status == "completed"


def test_repo_worker_agentic_templates_are_split_by_role() -> None:
    templates = get_agentic_worker_templates()["repo_worker"]

    assert [template.name for template in templates] == ["repo_locator", "repo_reader", "repo_summarizer"]
    assert templates[0].allowed_tools
    assert "classify_file_management_candidates" in templates[0].allowed_tools
    assert "resume_from_kernel_memory" in templates[0].allowed_tools
    assert "read_file" in templates[1].allowed_tools
    assert templates[2].allowed_tools == ()
    assert "Every artifact must be an object" in templates[2].system_prompt


def test_filesystem_worker_template_exposes_scoped_file_tools() -> None:
    templates = get_agentic_worker_templates()["filesystem_worker"]

    assert [template.name for template in templates] == ["filesystem_operator"]
    assert "apply_file_operations" in templates[0].allowed_tools
    assert "write_many_files" in templates[0].allowed_tools
    assert "write_json_manifest" in templates[0].allowed_tools
    assert "resume_from_kernel_memory" in templates[0].allowed_tools
    assert "verify_file_state_against_manifest" in templates[0].allowed_tools
    assert "move_file" in templates[0].allowed_tools
    assert "delete_file" in templates[0].allowed_tools
    assert "runtime_capabilities" in templates[0].allowed_tools
    assert "shell chaining" in templates[0].system_prompt
    assert "hatchling" in templates[0].system_prompt
    assert "write_json_manifest" in templates[0].system_prompt
    assert "use an operations array" in templates[0].system_prompt
    assert "never return completed final_result" in templates[0].system_prompt.lower()
    assert 'packages = ["app"]' in templates[0].system_prompt


def test_file_management_worker_prompts_preserve_exact_file_type_classification() -> None:
    templates = get_agentic_worker_templates()
    repo_prompt = templates["repo_worker"][0].system_prompt
    research_prompt = templates["research_worker"][0].system_prompt
    filesystem_prompt = templates["filesystem_worker"][0].system_prompt
    code_prompt = templates["code_worker"][0].system_prompt
    verify_prompt = templates["verify_worker"][0].system_prompt

    assert "Capture exact manifest/report key" in repo_prompt
    assert "classify_file_management_candidates" in repo_prompt
    assert "file_read" in repo_prompt
    assert "batch_read" in repo_prompt
    assert '"markdown" means Markdown files such as .md or .markdown' in research_prompt
    assert "not arbitrary .txt files" in research_prompt
    assert "Do not leave a" in research_prompt
    assert "discovered JSON/log candidate at its source path" in research_prompt
    assert "Respect exact file-type words from the prompt" in filesystem_prompt
    assert "not .txt" in filesystem_prompt
    assert "kept as-is" in filesystem_prompt
    assert "moved_build_logs" in filesystem_prompt
    assert "Never return needs_replan solely because write_files=false" in filesystem_prompt
    assert "summarize success without evidence" in filesystem_prompt
    assert "resume_from_kernel_memory" in filesystem_prompt
    assert "moved_json_files" in code_prompt
    assert "Do not invent synonym tool names" in code_prompt
    assert "mutation_scope_missing_required_path" in verify_prompt
    assert "kernel_memory alone" in verify_prompt


def test_verify_worker_template_exposes_project_test_tool() -> None:
    templates = get_agentic_worker_templates()["verify_worker"]

    assert [template.name for template in templates] == ["verification_runner"]
    assert "run_required_verification" in templates[0].allowed_tools
    assert "verify_file_state_against_manifest" in templates[0].allowed_tools
    assert "run_project_tests" in templates[0].allowed_tools
    assert "release-gate verification worker" in templates[0].system_prompt
    assert "uv run --extra dev pytest -q" in templates[0].system_prompt
    assert "uv run --extra test pytest -q" in templates[0].system_prompt


def test_worker_decision_normalizes_implementation_failure_issue_type() -> None:
    normalized = _normalize_worker_decision(
        {
            "final_result": {
                "status": "failed",
                "summary": "Generated package failed verification.",
                "issues": [
                    {
                        "issue_type": "implementation_failure",
                        "code": "pytest_failed",
                        "message": "pytest failed after scaffold",
                    }
                ],
            }
        }
    )

    assert normalized["final_result"]["issues"][0]["issue_type"] == "instance_failure"


def test_worker_decision_drops_provider_thoughts_field() -> None:
    normalized = _normalize_worker_decision(
        {
            "thoughts": "I should now finish.",
            "final_result": {
                "status": "completed",
                "summary": "Done.",
                "artifacts": [{"id": "final_artifact", "content": "ok"}],
            },
        }
    )

    assert "thoughts" not in normalized
    assert normalized["final_result"]["summary"] == "Done."


def test_worker_prompt_keeps_readonly_synthesis_from_becoming_replan(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Synthesized from artifacts.",
                    "artifacts": [{"id": "analysis_evidence", "content": {"evidence": ["from input"]}}],
                }
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="research_worker",
        templates=[
            WorkerInstanceTemplate(
                name="context_synthesizer",
                role="synthesize",
                system_prompt="synthesize from artifacts",
                allowed_tools=(),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="research_worker",
        expected_outputs=["analysis_evidence"],
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=0,
        max_model_calls=1,
    ).model_copy(
        update={
            "metadata": {"phase": "ANALYZE", "mode": "observe_only"},
            "input_artifacts": [ArtifactPayload(id="repo_inventory", content={"files": ["README.md"]})],
        }
    )

    result = runner.run(task)

    assert result.status == "completed"
    assert "do not return needs_replan solely because tools or write permissions are unavailable" in client.prompts[0]


def test_toolbox_enforces_read_write_command_and_web_permissions(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("hello worker", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    no_permissions = _task(
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        }
    )

    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="read_file", arguments={"path": "sample.txt"})
    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="write_file", arguments={"path": "x.txt", "content": "x"})
    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="run_readonly_command", arguments={"command": "rg hello ."})
    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="web_search", arguments={"query": "hello"})

    read_task = _task()
    assert toolbox.execute(task=read_task, tool_name="read_file", arguments={"path": "sample.txt"})["content"] == "hello worker"

    command_task = _task(
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": True,
            "web_research": False,
        }
    )
    version = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": ["python", "-m", "pytest", "--version"]},
    )
    missing = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": ["python", "-m", "pytest", "does_not_exist.py"]},
    )
    pythonpath = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": "PYTHONPATH=. pytest --version"},
    )
    env_pythonpath = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": "env PYTHONPATH=. pytest --version"},
    )
    sh_pythonpath = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": "sh -c 'PYTHONPATH=. pytest --version'"},
    )
    assert version["returncode"] == 0
    assert missing["returncode"] != 0
    assert pythonpath["returncode"] == 0
    assert pythonpath["command"][1:3] == ["-m", "pytest"]
    assert pythonpath["env"] == {"PYTHONPATH": "."}
    assert env_pythonpath["returncode"] == 0
    assert env_pythonpath["command"][1:3] == ["-m", "pytest"]
    assert env_pythonpath["env"] == {"PYTHONPATH": "."}
    assert sh_pythonpath["returncode"] == 0
    assert sh_pythonpath["command"][1:3] == ["-m", "pytest"]
    assert sh_pythonpath["env"] == {"PYTHONPATH": "."}

    with pytest.raises(ToolPermissionError):
        toolbox.execute(
            task=command_task,
            tool_name="run_readonly_command",
            arguments={"command": "OPENAI_API_KEY=x pytest --version"},
        )
    with pytest.raises(ToolPermissionError):
        toolbox.execute(
            task=command_task,
            tool_name="run_readonly_command",
            arguments={"command": "sh -c 'pytest --version && echo unsafe'"},
        )


def test_toolbox_high_level_worker_tools_are_permission_gated_and_scoped(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text(
        "from src.app import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, text=True)
    target.write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")

    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    read_task = _task(
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={"target_paths": ["src/app.py"], "reason": "app logic", "max_files": 1},
                )
            ]
        }
    )
    command_task = read_task.model_copy(
        update={
            "permissions": read_task.permissions.model_copy(update={"run_commands": True})
        }
    )

    snapshot = toolbox.execute(task=read_task, tool_name="repo_snapshot", arguments={"path": "."})
    many = toolbox.execute(
        task=read_task,
        tool_name="read_many_files",
        arguments={"paths": ["src/app.py", "tests/test_app.py"]},
    )
    diff = toolbox.execute(task=read_task, tool_name="diff_summary", arguments={"path": "src/app.py"})
    scope = toolbox.execute(task=read_task, tool_name="mutation_scope_check", arguments={})
    tests = toolbox.execute(
        task=command_task,
        tool_name="run_focused_tests",
        arguments={"paths": "tests/test_app.py"},
    )

    assert "src/app.py" in snapshot["files"]
    assert "tests/test_app.py" in snapshot["test_candidates"]
    assert [file["path"] for file in many["files"]] == ["src/app.py", "tests/test_app.py"]
    assert diff["changed_files"] == ["src/app.py"]
    assert scope["passed"] is True
    assert scope["in_scope"] == ["src/app.py"]
    assert tests["returncode"] == 0
    assert tests["env"] == {"PYTHONPATH": "."}


def test_toolbox_web_search_uses_configured_duckduckgo_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return b'''
                <html>
                  <a class="result__a" href="/l/?uddg=https%3A%2F%2Fstripe.com%2Fdocs%2Fpayments%2Fpayment-intents">Stripe docs</a>
                  <a class="result__snippet">Use idempotency keys for retry safety.</a>
                  <a class="result__a" href="https://example.com/retry">Retry guide</a>
                  <a class="result__snippet">Backoff reduces duplicate pressure.</a>
                </html>
            '''

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    toolbox = WorkerToolbox(
        WorkerToolConfig(
            root_path=tmp_path,
            web_search_provider="duckduckgo",
            web_search_max_results=2,
        )
    )
    result = toolbox.execute(
        task=_task(
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            }
        ),
        tool_name="web_search",
        arguments={"query": "payment idempotency retry"},
    )

    assert "payment+idempotency+retry" in captured["url"]
    assert captured["timeout"] == 15.0
    assert result["provider"] == "duckduckgo"
    assert len(result["results"]) == 2
    assert result["results"][0]["url"] == "https://stripe.com/docs/payments/payment-intents"
    assert "idempotency keys" in result["results"][0]["snippet"]


def test_toolbox_web_search_uses_brave_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return json.dumps(
                {
                    "query": {"original": "payment idempotency retry"},
                    "web": {
                        "results": [
                            {
                                "title": "Stripe idempotency docs",
                                "url": "https://stripe.com/docs/idempotency",
                                "description": "Use idempotency keys for retries.",
                                "extra_snippets": ["Retry requests can safely use the same key."],
                                "profile": {"name": "Stripe"},
                                "age": "2 weeks ago",
                                "language": "en",
                            }
                        ]
                    },
                }
            ).encode("utf-8")

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    toolbox = WorkerToolbox(
        WorkerToolConfig(
            root_path=tmp_path,
            web_search_provider="brave",
            web_search_api_key="test-brave-key",
            web_search_max_results=3,
        )
    )
    result = toolbox.execute(
        task=_task(
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            }
        ),
        tool_name="web_search",
        arguments={"query": "payment idempotency retry"},
    )

    assert "api.search.brave.com/res/v1/web/search" in captured["url"]
    assert "payment+idempotency+retry" in captured["url"]
    assert captured["headers"]["X-subscription-token"] == "test-brave-key"
    assert captured["timeout"] == 15.0
    assert result["provider"] == "brave"
    assert result["results"][0]["title"] == "Stripe idempotency docs"
    assert result["results"][0]["snippets"] == [
        "Use idempotency keys for retries.",
        "Retry requests can safely use the same key.",
    ]


def test_toolbox_web_fetch_extracts_readable_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeHeaders:
        def get(self, key: str, default: str = "") -> str:
            return "text/html; charset=utf-8" if key == "Content-Type" else default

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def geturl(self) -> str:
            return "https://example.com/final"

        def read(self, limit: int) -> bytes:
            return b"""
                <html>
                  <head>
                    <title>Retry Safety</title>
                    <meta name="description" content="Payment retry notes">
                    <script>secret()</script>
                  </head>
                  <body>
                    <h1>Payment retries</h1>
                    <p>Use stable idempotency keys.</p>
                    <a href="https://example.com/source">Source page</a>
                  </body>
                </html>
            """

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    result = toolbox.execute(
        task=_task(
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            }
        ),
        tool_name="web_fetch",
        arguments={"url": "https://example.com/retry"},
    )

    assert result["final_url"] == "https://example.com/final"
    assert result["title"] == "Retry Safety"
    assert result["description"] == "Payment retry notes"
    assert "Use stable idempotency keys." in result["content"]
    assert "secret()" not in result["content"]
    assert result["links"] == [{"url": "https://example.com/source", "text": "Source page"}]


def test_toolbox_excludes_repo_noise_from_discovery(tmp_path: Path) -> None:
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "blob").write_text("noise", encoding="utf-8")
    (tmp_path / "src" / "__pycache__").mkdir(parents=True)
    (tmp_path / "src" / "__pycache__" / "module.pyc").write_text("noise", encoding="utf-8")
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task()

    listed = toolbox.execute(task=task, tool_name="list_dir", arguments={"path": "."})
    searched = toolbox.execute(task=task, tool_name="file_search", arguments={"path": ".", "pattern": "**/*"})

    assert ".git" not in {entry["name"] for entry in listed["entries"]}
    assert "src/checkout.py" in searched["matches"]
    assert not any(".git" in match or "__pycache__" in match for match in searched["matches"])


def test_toolbox_treats_root_basename_as_mounted_repo_root(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task()

    snapshot = toolbox.execute(task=task, tool_name="repo_snapshot", arguments={"path": tmp_path.name})
    read = toolbox.execute(
        task=task,
        tool_name="read_file",
        arguments={"path": f"{tmp_path.name}/src/app.py"},
    )

    assert snapshot["path"] == "."
    assert snapshot["is_empty"] is False
    assert "src/app.py" in snapshot["files"]
    assert read["path"] == "src/app.py"


def test_toolbox_runtime_capabilities_is_structured_command_tool(tmp_path: Path) -> None:
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": True,
            "web_research": False,
        }
    )

    result = toolbox.execute(task=task, tool_name="runtime_capabilities", arguments={})

    assert result["preferred_local_stack"] == "python"
    assert result["capabilities"]["python"]["available"] is True
    assert result["capabilities"]["pytest"]["command"][1:3] == ["-m", "pytest"]


def test_toolbox_project_tests_selects_uv_pytest_extra_when_pytest_is_optional(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "sample"
version = "0.1.0"
dependencies = []

[project.optional-dependencies]
dev = ["pytest", "httpx"]
""",
        encoding="utf-8",
    )
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))

    assert toolbox._project_pytest_command(["-q"]) == ["uv", "run", "--extra", "dev", "pytest", "-q"]
    assert _is_allowed_uv_pytest_command(["--extra", "dev", "pytest", "-q"]) is True
    assert _is_allowed_uv_pytest_command(["--all-extras", "python", "-m", "pytest", "-q"]) is True
    assert _is_allowed_uv_pytest_command(["--directory", "/tmp", "pytest", "-q"]) is False

    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "sample"
version = "0.1.0"
dependencies = []

[project.optional-dependencies]
test = ["pytest", "httpx"]
""",
        encoding="utf-8",
    )
    assert toolbox._project_pytest_command(["-q"]) == ["uv", "run", "--extra", "test", "pytest", "-q"]
    assert toolbox._canonical_readonly_command(["uv", "run", "pytest", "tests/", "-v"]) == [
        "uv",
        "run",
        "--extra",
        "test",
        "pytest",
        "tests/",
        "-v",
    ]
    assert toolbox._canonical_readonly_command(["uv", "run", "--locked", "pytest", "-q"]) == [
        "uv",
        "run",
        "--locked",
        "--extra",
        "test",
        "pytest",
        "-q",
    ]
    assert toolbox._canonical_readonly_command(["uv", "run", "--extra", "dev", "pytest", "-q"]) == [
        "uv",
        "run",
        "--extra",
        "dev",
        "pytest",
        "-q",
    ]


def test_toolbox_batch_write_move_and_delete_are_scoped(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging" / "draft.md").write_text("draft", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["docs", "staging/draft.md"],
        }
    )

    written = toolbox.execute(
        task=task,
        tool_name="write_many_files",
        arguments={
            "files": [
                {"path": "docs/a.md", "content": "# A\n"},
                {"path": "docs/b.md", "content": "# B\n"},
            ]
        },
    )
    moved = toolbox.execute(
        task=task,
        tool_name="move_file",
        arguments={"source": "staging/draft.md", "destination": "docs/draft.md"},
    )
    deleted = toolbox.execute(task=task, tool_name="delete_file", arguments={"path": "docs/b.md"})

    assert written["count"] == 2
    assert moved["destination"] == "docs/draft.md"
    assert deleted["deleted"] is True
    assert (tmp_path / "docs" / "a.md").read_text(encoding="utf-8") == "# A\n"
    assert not (tmp_path / "staging" / "draft.md").exists()
    with pytest.raises(ToolPermissionError, match="outside allowed scope"):
        toolbox.execute(
            task=task,
            tool_name="write_many_files",
            arguments={"files": [{"path": "src/app.py", "content": "bad"}]},
        )
    assert not (tmp_path / "src" / "app.py").exists()


def test_toolbox_apply_file_operations_batches_real_file_management_paths(tmp_path: Path) -> None:
    (tmp_path / "incoming" / "desk dump").mkdir(parents=True)
    (tmp_path / "incoming" / "finance").mkdir(parents=True)
    (tmp_path / "handoff" / "documents").mkdir(parents=True)
    (tmp_path / "handoff" / "finance" / "2026").mkdir(parents=True)
    (tmp_path / "incoming" / "desk dump" / "Kickoff Notes.md").write_text("notes\n", encoding="utf-8")
    (tmp_path / "incoming" / "finance" / "Receipt May 2026.txt").write_text("paid\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        worker_type="filesystem_worker",
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": [
                "incoming/desk dump/Kickoff Notes.md",
                "incoming/finance/Receipt May 2026.txt",
                "handoff/documents/kickoff_notes.md",
                "handoff/finance/2026/receipt_may_2026.txt",
                "handoff/archive_index.json",
            ],
        },
    )

    result = toolbox.execute(
        task=task,
        tool_name="apply_file_operations",
        arguments={
            "operations": [
                {
                    "action": "move",
                    "source": "incoming/desk dump/Kickoff Notes.md",
                    "destination": "handoff/documents/kickoff_notes.md",
                },
                {
                    "action": "move",
                    "source": "incoming/finance/Receipt May 2026.txt",
                    "destination": "handoff/finance/2026/receipt_may_2026.txt",
                },
                {
                    "action": "write",
                    "path": "handoff/archive_index.json",
                    "content": '{"total_moved": 2}\n',
                },
            ]
        },
    )
    replay = toolbox.execute(
        task=task,
        tool_name="move_file",
        arguments={
            "source": "incoming/desk dump/Kickoff Notes.md",
            "destination": "handoff/documents/kickoff_notes.md",
        },
    )

    assert result["applied_count"] == 3
    assert result["operation_count"] == 3
    assert (tmp_path / "handoff" / "documents" / "kickoff_notes.md").read_text(encoding="utf-8") == "notes\n"
    assert (tmp_path / "handoff" / "finance" / "2026" / "receipt_may_2026.txt").read_text(encoding="utf-8") == "paid\n"
    assert not (tmp_path / "incoming" / "finance" / "Receipt May 2026.txt").exists()
    assert replay["already_done"] is True


def test_toolbox_apply_file_operations_accepts_grouped_operation_aliases(tmp_path: Path) -> None:
    (tmp_path / "incoming").mkdir()
    (tmp_path / "incoming" / "policy.md").write_text("policy\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        worker_type="filesystem_worker",
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": [
                "incoming/policy.md",
                "records/policies",
                "records/policies/policy.md",
                "records/archive_index.json",
            ],
        },
    )

    result = toolbox.execute(
        task=task,
        tool_name="apply_file_operations",
        arguments={
            "create_dirs": ["records/policies"],
            "move_files": [
                {"source": "incoming/policy.md", "destination": "records/policies/policy.md"},
            ],
            "update_files": [
                {"path": "records/archive_index.json", "content": '{"total_moved": 1}\n'},
            ],
        },
    )

    assert result["applied_count"] == 3
    assert (tmp_path / "records" / "policies" / "policy.md").read_text(encoding="utf-8") == "policy\n"
    assert (tmp_path / "records" / "archive_index.json").read_text(encoding="utf-8") == '{"total_moved": 1}\n'


def test_toolbox_write_json_manifest_preserves_required_keys_and_counts(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        worker_type="filesystem_worker",
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["docs/workspace_manifest.json"],
        },
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "required_json_keys": [
                    "moved_documents",
                    "moved_logs",
                    "moved_json_artifacts",
                    "total_artifacts",
                ],
            }
        }
    )

    result = toolbox.execute(
        task=task,
        tool_name="write_json_manifest",
        arguments={
            "path": "docs/workspace_manifest.json",
            "payload": {
                "moved_documents": ["task_notes.md"],
                "moved_logs": ["old_build.log"],
                "moved_json_artifacts": ["error_dump.json"],
                "total_artifacts": 3,
            },
            "required_keys": [],
            "total_key": "total_artifacts",
            "count_keys": ["moved_documents", "moved_logs", "moved_json_artifacts"],
        },
    )

    assert result["counts_match"] is True
    assert result["fields_present"] == [
        "moved_documents",
        "moved_json_artifacts",
        "moved_logs",
        "total_artifacts",
    ]
    assert json.loads((tmp_path / "docs" / "workspace_manifest.json").read_text(encoding="utf-8"))[
        "moved_logs"
    ] == ["old_build.log"]

    with pytest.raises(MutationOperationDeniedError) as exc_info:
        toolbox.execute(
            task=task,
            tool_name="write_json_manifest",
            arguments={
                "path": "docs/workspace_manifest.json",
                "payload": {
                    "moved_documents": ["task_notes.md"],
                    "moved_json_artifacts": ["error_dump.json"],
                    "total_artifacts": 2,
                },
                "required_keys": [],
                "total_key": "total_artifacts",
                "count_keys": ["moved_documents", "moved_logs", "moved_json_artifacts"],
            },
        )

    assert exc_info.value.denial.code == "manifest_missing_required_keys"
    assert exc_info.value.denial.metadata["missing_keys"] == ["moved_logs"]


def test_toolbox_write_json_manifest_infers_literal_total_and_excludes_held_items(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir()
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        worker_type="filesystem_worker",
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["records/archive_index.json"],
        },
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "required_json_keys": [
                    "moved_documents",
                    "moved_evidence",
                    "moved_logs",
                    "moved_exports",
                    "held_items",
                    "total_moved",
                ],
            }
        }
    )

    result = toolbox.execute(
        task=task,
        tool_name="write_json_manifest",
        arguments={
            "path": "records/archive_index.json",
            "payload": {
                "moved_documents": ["client_alpha_notes.md", "client_beta_followup.md", "retention_policy.md"],
                "moved_evidence": ["access_review.json", "vendor_exception.json"],
                "moved_logs": ["audit_2026_06.log"],
                "moved_exports": ["owner_map.csv", "policy_matrix.csv"],
                "held_items": [
                    "client_gamma_hold.md",
                    "debug_keep.log",
                    "draft_policy_do_not_move.md",
                    "keep_local.json",
                ],
                "total_moved": 8,
            },
        },
    )

    assert result["counts_match"] is True
    assert result["total_key"] == "total_moved"
    assert result["count_keys"] == ["moved_documents", "moved_evidence", "moved_logs", "moved_exports"]
    assert result["counted_total"] == 8
    payload = json.loads((tmp_path / "records" / "archive_index.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "moved_documents",
        "moved_evidence",
        "moved_logs",
        "moved_exports",
        "held_items",
        "total_moved",
    }


def test_toolbox_classifies_file_management_candidates_with_held_items(tmp_path: Path) -> None:
    for directory in [
        "incoming/policies",
        "incoming/client_notes",
        "incoming/evidence",
        "logs/raw",
        "exports",
    ]:
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "incoming/policies/retention_policy.md").write_text("policy", encoding="utf-8")
    (tmp_path / "incoming/client_notes/client_alpha_notes.md").write_text("note", encoding="utf-8")
    (tmp_path / "incoming/client_notes/client_gamma_hold.md").write_text("hold this one", encoding="utf-8")
    (tmp_path / "incoming/evidence/access_review.json").write_text("{}", encoding="utf-8")
    (tmp_path / "logs/raw/audit_2026_06.log").write_text("audit", encoding="utf-8")
    (tmp_path / "exports/owner_map.csv").write_text("owner,path\n", encoding="utf-8")

    task = _task(
        worker_type="repo_worker",
        expected_outputs=["file_classification_report"],
        permissions={"read_files": True, "write_files": False, "run_commands": False, "web_research": False},
    ).model_copy(
        update={
            "instruction": (
                "Move markdown to records/policies, JSON evidence to records/evidence, "
                "logs to records/logs, CSV exports to records/exports, and write archive index."
            ),
            "metadata": {
                "required_json_keys": [
                    "moved_documents",
                    "moved_evidence",
                    "moved_logs",
                    "moved_exports",
                    "held_items",
                    "total_moved",
                ]
            },
        }
    )

    result = WorkerToolbox(WorkerToolConfig(root_path=tmp_path)).execute(
        task=task,
        tool_name="classify_file_management_candidates",
        arguments={"path": "."},
    )

    sources = {item["source"] for item in result["candidates"]}
    destinations = {item["destination"] for item in result["candidates"]}
    assert "incoming/client_notes/client_alpha_notes.md" in sources
    assert "incoming/evidence/access_review.json" in sources
    assert "logs/raw/audit_2026_06.log" in sources
    assert "exports/owner_map.csv" in sources
    assert "records/policies/client_alpha_notes.md" in destinations
    assert "records/evidence/access_review.json" in destinations
    assert "records/logs/audit_2026_06.log" in destinations
    assert "records/exports/owner_map.csv" in destinations
    assert result["manifest_payload_seed"]["total_moved"] == 5
    assert result["held_items"][0]["path"] == "incoming/client_notes/client_gamma_hold.md"


def test_toolbox_verify_file_state_against_manifest_detects_source_and_count_failures(tmp_path: Path) -> None:
    (tmp_path / "incoming").mkdir()
    (tmp_path / "records/policies").mkdir(parents=True)
    (tmp_path / "incoming/client_alpha_notes.md").write_text("note", encoding="utf-8")
    (tmp_path / "incoming/client_hold.md").write_text("hold", encoding="utf-8")
    (tmp_path / "records/policies/client_alpha_notes.md").write_text("note", encoding="utf-8")
    (tmp_path / "records/archive_index.json").write_text(
        json.dumps(
            {
                "moved_documents": ["client_alpha_notes.md"],
                "held_items": ["client_hold.md"],
                "total_moved": 2,
            }
        ),
        encoding="utf-8",
    )
    task = _task(
        worker_type="verify_worker",
        expected_outputs=["file_state_verification"],
        permissions={"read_files": True, "write_files": False, "run_commands": False, "web_research": False},
    ).model_copy(
        update={
            "metadata": {"required_json_keys": ["moved_documents", "held_items", "total_moved"]},
            "input_artifacts": [
                ArtifactPayload(
                    id="file_classification_report",
                    content={
                        "candidates": [
                            {
                                "source": "incoming/client_alpha_notes.md",
                                "destination": "records/policies/client_alpha_notes.md",
                            }
                        ],
                        "held_items": [{"path": "incoming/client_hold.md"}],
                    },
                )
            ],
        }
    )
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))

    failed = toolbox.execute(
        task=task,
        tool_name="verify_file_state_against_manifest",
        arguments={"manifest_path": "records/archive_index.json"},
    )
    assert failed["status"] == "failed"
    assert {error["code"] for error in failed["errors"]} == {
        "manifest_count_mismatch",
        "move_state_mismatch",
    }

    (tmp_path / "incoming/client_alpha_notes.md").unlink()
    payload = json.loads((tmp_path / "records/archive_index.json").read_text(encoding="utf-8"))
    payload["total_moved"] = 1
    (tmp_path / "records/archive_index.json").write_text(json.dumps(payload), encoding="utf-8")
    passed = toolbox.execute(
        task=task,
        tool_name="verify_file_state_against_manifest",
        arguments={"manifest_path": "records/archive_index.json"},
    )
    assert passed["status"] == "passed"
    assert passed["counts_match"] is True


def test_toolbox_resume_from_kernel_memory_returns_next_action_plan(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/finished.md").write_text("done", encoding="utf-8")
    task = _task(
        worker_type="filesystem_worker",
        permissions={"read_files": True, "write_files": True, "run_commands": False, "web_research": False},
    ).model_copy(
        update={
            "metadata": {
                "kernel_memory": {
                    "step_id": "mutate",
                    "attempt_count": 2,
                    "successful_write_operations": [
                        {
                            "tool_name": "apply_file_operations",
                            "action": "move",
                            "status": "applied",
                            "paths": ["docs/finished.md"],
                        }
                    ],
                    "pending_required_write_paths": ["docs/archive_index.json"],
                    "denied_operations": [{"tool_name": "write_json_manifest", "denial": {"code": "bad_total"}}],
                    "retry_guidance": ["finish pending manifest"],
                }
            }
        }
    )

    result = WorkerToolbox(WorkerToolConfig(root_path=tmp_path)).execute(
        task=task,
        tool_name="resume_from_kernel_memory",
        arguments={},
    )

    assert result["already_completed_paths"] == ["docs/finished.md"]
    assert result["pending_required_write_paths"] == ["docs/archive_index.json"]
    assert result["path_state"]["docs/finished.md"]["exists"] is True
    assert "write_json_manifest" in result["recommended_next_tools"]


def test_toolbox_run_required_verification_returns_artifact_ready_test_results(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    task = _task(
        worker_type="verify_worker",
        permissions={"read_files": False, "write_files": False, "run_commands": True, "web_research": False},
    )

    result = WorkerToolbox(WorkerToolConfig(root_path=tmp_path)).execute(
        task=task,
        tool_name="run_required_verification",
        arguments={"paths": ["tests/test_sample.py"]},
    )

    assert result["status"] == "passed"
    assert result["commands"][0]["returncode"] == 0
    assert result["failed_commands"] == []


def test_apply_file_operations_uses_step_blast_radius_not_write_batch_limit(tmp_path: Path) -> None:
    (tmp_path / "incoming").mkdir()
    (tmp_path / "docs").mkdir()
    for index in range(4):
        (tmp_path / "incoming" / f"File {index}.md").write_text(f"file {index}\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        worker_type="filesystem_worker",
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=1,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_policy": {"batch_max_files": 1, "step_max_files": 8},
            }
        }
    )

    result = toolbox.execute(
        task=task,
        tool_name="apply_file_operations",
        arguments={
            "operations": [
                {
                    "action": "move",
                    "source": f"incoming/File {index}.md",
                    "destination": f"docs/file_{index}.md",
                }
                for index in range(4)
            ]
        },
    )

    assert result["applied_count"] == 4
    assert sorted(path.name for path in (tmp_path / "docs").glob("*.md")) == [
        "file_0.md",
        "file_1.md",
        "file_2.md",
        "file_3.md",
    ]


def test_toolbox_reports_empty_repo_snapshot(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    snapshot = toolbox.execute(task=_task(), tool_name="repo_snapshot", arguments={"path": tmp_path.name})

    assert snapshot["path"] == "."
    assert snapshot["is_empty"] is True
    assert snapshot["files"] == []
    assert snapshot["directories"] == []


def test_readonly_tools_return_missing_path_observations(tmp_path: Path) -> None:
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))

    snapshot = toolbox.execute(task=_task(), tool_name="repo_snapshot", arguments={"path": "missing"})
    listing = toolbox.execute(task=_task(), tool_name="list_dir", arguments={"path": "missing"})
    file_result = toolbox.execute(task=_task(), tool_name="read_file", arguments={"path": "missing/report.md"})

    assert snapshot["exists"] is False
    assert snapshot["error"] == "not_found"
    assert listing == {"path": "missing", "exists": False, "entries": [], "error": "not_found"}
    assert file_result["exists"] is False
    assert file_result["error"] == "not_found"
    assert file_result["content"] == ""


def test_diff_summary_and_scope_check_include_untracked_new_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={"target_paths": ["src/app.py"], "reason": "new file", "max_files": 1},
                )
            ]
        }
    )

    diff = toolbox.execute(task=task, tool_name="diff_summary", arguments={"path": "src/app.py"})
    scope = toolbox.execute(task=task, tool_name="mutation_scope_check", arguments={})

    assert diff["changed_files"] == ["src/app.py"]
    assert "+++ src/app.py" in diff["diff"]
    assert scope["passed"] is True
    assert scope["in_scope"] == ["src/app.py"]


def test_mutation_scope_check_accepts_rehydrated_scope_artifact_id(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="rehydrated_mutation_scope",
                    content={"target_paths": ["src/app.py"], "reason": "replan recovered scope", "max_files": 1},
                )
            ]
        }
    )

    scope = toolbox.execute(task=task, tool_name="mutation_scope_check", arguments={})

    assert scope["scope_available"] is True
    assert scope["passed"] is True
    assert scope["in_scope"] == ["src/app.py"]


def test_mutation_scope_check_uses_move_endpoints_even_when_max_files_hint_is_low(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "notes" / "drafts").mkdir(parents=True)
    source = tmp_path / "notes" / "drafts" / "task.md"
    source.write_text("notes\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "seed",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "docs").mkdir()
    source.replace(tmp_path / "docs" / "task.md")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={
                        "move_pairs": [
                            {"source": "notes/drafts/task.md", "destination": "docs/task.md"},
                        ],
                        "reason": "move notes into docs",
                        "max_files": 1,
                    },
                )
            ]
        }
    )

    scope = toolbox.execute(task=task, tool_name="mutation_scope_check", arguments={})

    assert scope["scope_available"] is True
    assert scope["passed"] is True
    assert scope["out_of_scope"] == []
    assert set(scope["write_scope_paths"]) >= {"notes/drafts/task.md", "docs/task.md"}


def test_toolbox_extracts_nested_write_scope_paths_from_artifacts(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "checkout.py"
    target.write_text('key = "charge:{order_id}:retry:{retry_count}"\n', encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths_from_artifacts": ["mutation_scope"],
        }
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={
                        "evidence": [
                            {
                                "file": "src/checkout.py",
                                "change_type": "modify_string_formatting",
                            }
                        ],
                        "notes": "Only mutate the scoped source file.",
                    },
                )
            ]
        }
    )

    result = toolbox.execute(
        task=task,
        tool_name="replace_in_file",
        arguments={
            "path": "src/checkout.py",
            "old": "retry:{retry_count}",
            "new": "stable",
        },
    )

    assert result["replacements"] == 1
    assert "stable" in target.read_text(encoding="utf-8")


def test_mutation_scope_accepts_structured_target_paths() -> None:
    scope = MutationScope.model_validate(
        {
            "target_paths": ["src/fulfillment/events.py"],
            "test_paths": ["tests/test_webhook.py"],
            "forbidden_paths": ["src/fulfillment/secrets.py"],
            "reason": "only webhook idempotency code should change",
            "max_files": 2,
        }
    )

    assert scope.target_paths == ["src/fulfillment/events.py"]
    assert scope.test_paths == ["tests/test_webhook.py"]
    assert scope.write_scope_paths == ["src/fulfillment/events.py"]


def test_mutation_scope_accepts_write_paths_alias() -> None:
    scope = MutationScope.model_validate(
        {
            "write_paths": ["pyproject.toml", "app/main.py", "tests/test_api.py"],
            "reason": "greenfield scaffold write set",
        }
    )

    assert scope.target_paths == ["pyproject.toml", "app/main.py", "tests/test_api.py"]
    assert scope.write_scope_paths == ["pyproject.toml", "app/main.py", "tests/test_api.py"]


def test_mutation_scope_move_pairs_authorize_source_and_destination(tmp_path: Path) -> None:
    (tmp_path / "artifacts" / "tmp").mkdir(parents=True)
    (tmp_path / "artifacts" / "logs").mkdir(parents=True)
    source = tmp_path / "artifacts" / "tmp" / "error_dump.json"
    source.write_text('{"error": true}\n', encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        worker_type="filesystem_worker",
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths_from_artifacts": ["mutation_scope"],
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={
                        "moves": [
                            {
                                "source": "artifacts/tmp/error_dump.json",
                                "destination": "artifacts/logs/error_dump.json",
                            }
                        ],
                        "reason": "workspace cleanup moves generated artifacts into logs",
                        "max_files": 2,
                    },
                )
            ]
        }
    )

    scope = resolve_mutation_scope_proposal(task.input_artifacts[0].content)
    result = toolbox.execute(
        task=task,
        tool_name="move_file",
        arguments={
            "source": "artifacts/tmp/error_dump.json",
            "destination": "artifacts/logs/error_dump.json",
        },
    )

    assert set(scope.write_scope_paths) == {
        "artifacts/tmp/error_dump.json",
        "artifacts/logs/error_dump.json",
    }
    assert result["destination"] == "artifacts/logs/error_dump.json"
    assert not source.exists()
    assert (tmp_path / "artifacts" / "logs" / "error_dump.json").is_file()


def test_mutation_scope_accepts_move_pairs_with_spaces() -> None:
    scope = MutationScope.model_validate(
        {
            "move_pairs": [
                {
                    "source": "incoming/desk dump/Kickoff Notes.md",
                    "destination": "handoff/documents/kickoff_notes.md",
                }
            ],
            "reason": "real user file names may contain spaces",
            "max_files": 2,
        }
    )

    assert set(scope.write_scope_paths) == {
        "incoming/desk dump/Kickoff Notes.md",
        "handoff/documents/kickoff_notes.md",
    }


def test_mutation_scope_normalizes_forbidden_globs() -> None:
    scope = MutationScope.model_validate(
        {
            "target_paths": ["src/fulfillment/events.py"],
            "forbidden_paths": ["**/*.py"],
            "reason": "source code should remain untouched",
            "max_files": 1,
        }
    )

    assert scope.forbidden_paths == []
    assert scope.forbidden_globs == ["**/*.py"]


def test_mutation_scope_accepts_legacy_file_label() -> None:
    scope = MutationScope.model_validate(
        {
            "evidence": [
                "File: src/fulfillment/events.py",
                "Insertion point: after the ignored event branch.",
            ],
            "notes": "Strictly limited to `src/fulfillment/events.py`.",
        }
    )

    assert scope.target_paths == ["src/fulfillment/events.py"]


def test_mutation_scope_rejects_escaping_path() -> None:
    with pytest.raises(ValueError, match="invalid repo-relative path"):
        MutationScope.model_validate(
            {
                "target_paths": ["../secret.py"],
                "reason": "bad scope",
            }
        )


def test_mutation_scope_widens_low_max_files_hint() -> None:
    scope = MutationScope.model_validate(
        {
            "target_paths": ["src/a.py", "src/b.py", "src/c.py"],
            "reason": "broad but concrete scope",
            "max_files": 2,
        }
    )

    assert scope.max_files == 3
    assert scope.metadata["declared_max_files"] == 2
    assert scope.metadata["validation_warnings"][0]["code"] == "max_files_widened"


def test_mutation_scope_extracts_move_endpoints_and_skips_manifest_noise() -> None:
    scope = MutationScope.model_validate(
        {
            "moves": [
                {"source": "notes/drafts/task_notes.md", "destination": "docs/task_notes.md"},
                {"source": "tmp/tmp_report.md", "destination": "docs/tmp_report.md"},
            ],
            "manifest_target": "docs/workspace_manifest.json",
            "excluded": [
                {"file": "notes/raw/old_blob.txt", "reason": "not markdown"},
                "misc/legacy.txt",
            ],
            "missing_sources": ["misc"],
        }
    )

    assert scope.target_paths == [
        "docs/task_notes.md",
        "notes/drafts/task_notes.md",
        "docs/tmp_report.md",
        "tmp/tmp_report.md",
        "docs/workspace_manifest.json",
    ]
    assert scope.max_files == 5


def test_mutation_scope_extracts_operation_paths_for_greenfield_scaffold() -> None:
    scope = MutationScope.model_validate(
        {
            "operations": [
                {"action": "create", "path": "pyproject.toml"},
                {"action": "create", "path": ".dockerignore"},
                {"action": "create", "path": "calculator/main.py"},
                {"action": "create", "path": "tests/test_api.py"},
            ]
        }
    )

    assert scope.target_paths == ["pyproject.toml", ".dockerignore", "calculator/main.py", "tests/test_api.py"]
    assert scope.max_files == 4


def test_mutation_scope_resolver_marks_proposal_source() -> None:
    scope = resolve_mutation_scope_proposal(
        {"target_paths": [".gitignore", ".prettierrc", "app/main.py"]},
        source_artifact_id="mutation_scope",
    )

    assert scope.target_paths == [".gitignore", ".prettierrc", "app/main.py"]
    assert scope.metadata["resolver"] == "mutation_scope_proposal_v1"
    assert scope.metadata["source_artifact_id"] == "mutation_scope"


def test_toolbox_rejects_write_outside_approved_scope(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    allowed = tmp_path / "src" / "allowed.py"
    denied = tmp_path / "src" / "denied.py"
    allowed.write_text("value = 'old'\n", encoding="utf-8")
    denied.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/allowed.py"],
        }
    )

    with pytest.raises(ToolPermissionError, match="outside allowed scope"):
        toolbox.execute(
            task=task,
            tool_name="replace_in_file",
            arguments={"path": "src/denied.py", "old": "old", "new": "new"},
        )


def test_toolbox_rejects_forbidden_subpath(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "secret.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src"],
        },
    ).model_copy(
        update={
            "metadata": {
                "write_scope": {
                    "target_paths": ["src"],
                    "forbidden_paths": ["src/secret.py"],
                    "reason": "directory scope with explicit exclusion",
                    "max_files": 5,
                }
            }
        }
    )

    with pytest.raises(ToolPermissionError, match="forbidden scope"):
        toolbox.execute(
            task=task,
            tool_name="replace_in_file",
            arguments={"path": "src/secret.py", "old": "old", "new": "new"},
        )


def test_toolbox_rejects_forbidden_glob(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/app.py"],
        },
    ).model_copy(
        update={
            "metadata": {
                "write_scope": {
                    "target_paths": ["src/app.py"],
                    "forbidden_paths": ["**/*.py"],
                    "reason": "glob exclusion",
                    "max_files": 1,
                }
            }
        }
    )

    with pytest.raises(ToolPermissionError, match="forbidden scope"):
        toolbox.execute(
            task=task,
            tool_name="replace_in_file",
            arguments={"path": "src/app.py", "old": "old", "new": "new"},
        )


def test_toolbox_normalizes_root_basename_in_write_scope(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": [f"{tmp_path.name}/src/app.py"],
        }
    )

    result = toolbox.execute(
        task=task,
        tool_name="replace_in_file",
        arguments={"path": f"{tmp_path.name}/src/app.py", "old": "old", "new": "new"},
    )

    assert result["path"] == "src/app.py"
    assert "new" in target.read_text(encoding="utf-8")


def test_toolbox_rejects_invalid_strict_scope_artifact_without_fallback(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths_from_artifacts": ["mutation_scope"],
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={"target_paths": ["../secret.py"], "reason": "bad scope"},
                )
            ]
        }
    )

    with pytest.raises(ToolPermissionError, match="invalid write scope artifact mutation_scope"):
        toolbox.validate_write_scope(task)


def test_task_compiler_merge_scope_ceiling_tracks_merged_paths() -> None:
    step = PlanStep(
        step_id="mutate",
        worker_type="code_worker",
        phase="MUTATE",
        mode="bounded_mutation",
        instruction="apply scoped edit",
        input_artifacts=["mutation_scope"],
        output_artifacts=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/b.py"],
            "write_paths_from_artifacts": ["mutation_scope"],
        },
    )
    artifact_store = {
        "mutation_scope": ArtifactPayload(
            id="mutation_scope",
            content={"target_paths": ["src/a.py"], "reason": "one file", "max_files": 1},
        )
    }

    task = TaskCompiler().compile("run", step, artifact_store)

    assert task.permissions.write_paths == ["src/b.py", "src/a.py"]
    assert task.metadata["write_scope"]["max_files"] == 2
    assert task.metadata["write_scope"]["metadata"]["resolver"] == "mutation_scope_proposal_v1"
    assert task.metadata["write_scope"]["metadata"]["source_artifact_ids"] == ["mutation_scope"]


def test_task_compiler_propagates_literal_contract_to_worker_metadata() -> None:
    step = PlanStep(
        step_id="finalize",
        worker_type="filesystem_worker",
        phase="FINALIZE",
        mode="summarize_only",
        instruction="summarize manifest",
        output_artifacts=["final_report"],
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    )
    envelope = Envelope(
        request_id="req_literal",
        raw_input="write manifest with moved_logs and total_artifacts",
        normalized_input="write manifest",
        user_goal="write manifest",
        input_type="file_management_request",
        literal_contract=[
            {"value": "moved_logs", "kind": "json_key", "source": "user_input"},
            {"value": "total_artifacts", "kind": "json_key", "source": "user_input"},
        ],
    )

    task = TaskCompiler().compile("run", step, {}, envelope=envelope)

    assert task.metadata["required_json_keys"] == ["moved_logs", "total_artifacts"]
    assert task.metadata["literal_contract"][0]["value"] == "moved_logs"


def test_task_compiler_merges_input_allowed_write_paths_for_bounded_mutation() -> None:
    step = PlanStep(
        step_id="mutate",
        worker_type="filesystem_worker",
        phase="MUTATE",
        mode="bounded_mutation",
        instruction="apply scoped moves",
        input_artifacts=["mutation_scope", "allowed_write_paths"],
        output_artifacts=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths_from_artifacts": ["mutation_scope"],
        },
    )
    artifact_store = {
        "mutation_scope": ArtifactPayload(
            id="mutation_scope",
            content={
                "target_paths": ["docs/task_notes.md"],
                "move_pairs": [{"source": "notes/task_notes.md", "destination": "docs/task_notes.md"}],
                "reason": "document move",
                "max_files": 1,
            },
        ),
        "allowed_write_paths": ArtifactPayload(
            id="allowed_write_paths",
            content=["artifacts/tmp/old_build.log", "artifacts/logs/old_build.log"],
        ),
    }

    task = TaskCompiler().compile("run", step, artifact_store)

    assert "artifacts/tmp/old_build.log" in task.permissions.write_paths
    assert "artifacts/logs/old_build.log" in task.permissions.write_paths
    assert task.metadata["write_policy"]["metadata"]["write_paths_from_artifacts"] == [
        "mutation_scope",
        "allowed_write_paths",
    ]


def test_agentic_group_blocks_missing_write_scope_before_model_call(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "should not be called",
                    "artifacts": [{"id": "change_summary", "content": "bad"}],
                }
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("replace_in_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
    )

    result = runner.run(task)

    assert result.status == "blocked"
    assert result.metadata["issue_code"] == "invalid_write_scope"
    assert client.prompts == []


def test_bounded_mutation_denial_observation_allows_repaired_write(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {"path": "src/a.py", "content": "A\n"},
                                {"path": "src/b.py", "content": "B\n"},
                            ]
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "tool_name": "write_file",
                        "arguments": {"path": "src/a.py", "content": "A\n"},
                    }
                ]
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "wrote narrowed file",
                        "artifacts": [{"id": "change_summary", "content": {"summary": "wrote src/a.py"}}],
                }
            },
        ]
    )
    trace = RuntimeMatrixLogger()
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("write_many_files", "write_file"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=3,
        max_model_calls=3,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_policy": {"batch_max_files": 1, "step_max_files": 5, "repair_attempts": 1},
            }
        }
    )

    result = runner.run(task, trace=trace)

    assert result.status == "completed"
    assert (tmp_path / "src" / "a.py").read_text(encoding="utf-8") == "A\n"
    assert not (tmp_path / "src" / "b.py").exists()
    assert any(row["event"] == "worker_tool_call_denied" for row in trace.snapshot()["rows"])
    assert "denied" in client.prompts[1]


def test_bounded_mutation_strict_scope_denial_can_be_repaired(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "allowed.py").write_text("value = 'old'\n", encoding="utf-8")
    (tmp_path / "src" / "denied.py").write_text("value = 'old'\n", encoding="utf-8")
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "replace_in_file",
                        "arguments": {"path": "src/denied.py", "old": "old", "new": "new"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "tool_name": "replace_in_file",
                        "arguments": {"path": "src/allowed.py", "old": "old", "new": "new"},
                    }
                ]
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "repaired strict scope",
                        "artifacts": [{"id": "change_summary", "content": {"summary": "updated allowed file"}}],
                }
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("replace_in_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=2,
        max_model_calls=3,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_policy": {
                    "strict_allowed_paths": ["src/allowed.py"],
                    "repair_attempts": 1,
                },
            }
        }
    )

    result = runner.run(task)

    assert result.status == "completed"
    assert (tmp_path / "src" / "allowed.py").read_text(encoding="utf-8") == "value = 'new'\n"
    assert (tmp_path / "src" / "denied.py").read_text(encoding="utf-8") == "value = 'old'\n"


def test_bounded_mutation_move_destination_exists_can_be_repaired(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "notes" / "task.md").write_text("new\n", encoding="utf-8")
    (tmp_path / "docs" / "task.md").write_text("old\n", encoding="utf-8")
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "move_file",
                        "arguments": {"source": "notes/task.md", "destination": "docs/task.md"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "tool_name": "move_file",
                        "arguments": {"source": "notes/task.md", "destination": "docs/task.md", "overwrite": True},
                    }
                ]
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "moved with explicit overwrite",
                        "artifacts": [{"id": "change_summary", "content": {"summary": "moved docs/task.md"}}],
                }
            },
        ]
    )
    trace = RuntimeMatrixLogger()
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("move_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=2,
        max_model_calls=3,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_policy": {"repair_attempts": 1},
            }
        }
    )

    result = runner.run(task, trace=trace)

    assert result.status == "completed"
    assert (tmp_path / "docs" / "task.md").read_text(encoding="utf-8") == "new\n"
    assert not (tmp_path / "notes" / "task.md").exists()
    assert any(row["event"] == "worker_tool_call_denied" for row in trace.snapshot()["rows"])
    assert "move_destination_exists" in client.prompts[1]


def test_non_mutation_move_destination_exists_remains_tool_error(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "notes" / "task.md").write_text("new\n", encoding="utf-8")
    (tmp_path / "docs" / "task.md").write_text("old\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        worker_type="filesystem_worker",
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["notes/task.md", "docs/task.md"],
        },
    )

    with pytest.raises(ToolExecutionError, match="destination exists"):
        toolbox.execute(
            task=task,
            tool_name="move_file",
            arguments={"source": "notes/task.md", "destination": "docs/task.md"},
        )

    assert (tmp_path / "docs" / "task.md").read_text(encoding="utf-8") == "old\n"
    assert (tmp_path / "notes" / "task.md").exists()


def test_bounded_mutation_small_batch_denial_exhaustion_is_retryable_instance_failure(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {"path": "src/a.py", "content": "A\n"},
                                {"path": "src/b.py", "content": "B\n"},
                            ]
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {"path": "src/a.py", "content": "A\n"},
                                {"path": "src/b.py", "content": "B\n"},
                            ]
                        },
                    }
                ]
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("write_many_files",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=2,
        max_model_calls=2,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_policy": {"batch_max_files": 1, "repair_attempts": 1},
            }
        }
    )

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["issue_code"] == "write_operation_denied_after_repair"
    assert result.metadata["retryable"] is True
    assert not (tmp_path / "src" / "a.py").exists()
    assert not (tmp_path / "src" / "b.py").exists()


def test_code_worker_synthesizes_mutation_artifacts_after_write_budget_exhaustion(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "checkout.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, text=True)
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/checkout.py"}}]},
            {
                "tool_calls": [
                    {
                        "tool_name": "replace_in_file",
                        "arguments": {"path": "src/checkout.py", "old": "old", "new": "new"},
                    }
                ]
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("read_file", "replace_in_file", "git_diff"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary", "rollback_patch"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/checkout.py"],
        },
        max_tool_calls=2,
        max_model_calls=2,
    )

    result = runner.run(task)

    assert result.status == "completed"
    assert "new" in target.read_text(encoding="utf-8")
    artifact_ids = {artifact.id for artifact in result.artifacts}
    assert {"change_summary", "rollback_patch", "patch_diff"} <= artifact_ids
    assert result.metadata["fallback"] == "mutation_observation_synthesis"


def test_mutation_completion_requires_scoped_create_paths_to_be_written(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "notes" / "task_notes.md").write_text("notes\n", encoding="utf-8")
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "apply_file_operations",
                        "arguments": {
                            "operations": [
                                {
                                    "action": "move",
                                    "source": "notes/task_notes.md",
                                    "destination": "docs/task_notes.md",
                                }
                            ]
                        },
                    }
                ]
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "moved file and created manifest",
                    "artifacts": [
                        {"id": "change_summary", "content": {"summary": "done"}},
                        {"id": "manifest_update_result", "content": {"status": "created"}},
                    ],
                }
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("apply_file_operations",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary", "manifest_update_result"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["notes/task_notes.md", "docs/task_notes.md", "docs/workspace_manifest.json"],
        },
        max_tool_calls=1,
        max_model_calls=2,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_scope": {
                    "target_paths": ["notes/task_notes.md", "docs/task_notes.md", "docs/workspace_manifest.json"],
                    "create_paths": ["docs/workspace_manifest.json"],
                    "reason": "move plus manifest",
                    "max_files": 3,
                },
            }
        }
    )

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["issue_code"] == "mutation_completed_missing_required_writes"
    assert result.metadata["issues"][0]["metadata"]["missing_required_write_paths"] == [
        "docs/workspace_manifest.json"
    ]
    assert not (tmp_path / "docs" / "workspace_manifest.json").exists()


def test_filesystem_worker_synthesizes_mutation_artifacts_after_batch_write_budget_exhaustion(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {"path": "src/calculator.py", "content": "def add(a, b):\n    return a + b\n"},
                                {
                                    "path": "tests/test_calculator.py",
                                    "content": "from src.calculator import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                                },
                                {"path": "README.md", "content": "# Calculator API\n"},
                            ]
                        },
                    }
                ]
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="scaffold",
                system_prompt="scaffold",
                allowed_tools=("write_many_files", "diff_summary"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary", "rollback_patch"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src", "tests", "README.md"],
        },
        max_tool_calls=1,
        max_model_calls=1,
    )

    result = runner.run(task)

    assert result.status == "completed"
    assert (tmp_path / "src" / "calculator.py").exists()
    artifact_ids = {artifact.id for artifact in result.artifacts}
    assert {"change_summary", "rollback_patch", "patch_diff"} <= artifact_ids
    patch_diff = next(artifact for artifact in result.artifacts if artifact.id == "patch_diff")
    assert "src/calculator.py" in patch_diff.content["diff"]
    change_summary = next(artifact for artifact in result.artifacts if artifact.id == "change_summary")
    assert change_summary.content["summary"]
    assert result.metadata["fallback"] == "mutation_observation_synthesis"


def test_mutation_fallback_rejects_domain_artifact_without_manifest_tool_evidence(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {
                                    "path": "docs/workspace_manifest.json",
                                    "content": '{"moved_documents": [], "total_artifacts": 0}\n',
                                }
                            ]
                        },
                    }
                ]
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="manifest",
                system_prompt="manifest",
                allowed_tools=("write_many_files", "diff_summary"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["moved_items_record"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["docs/workspace_manifest.json"],
        },
        max_tool_calls=1,
        max_model_calls=1,
    ).model_copy(update={"metadata": {"phase": "MUTATE", "mode": "bounded_mutation"}})

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["issue_code"] == "artifact_synthesis_incomplete"
    assert result.metadata["issues"][0]["metadata"]["unsynthesizable_artifacts"] == ["moved_items_record"]


def test_bounded_mutation_denial_is_observed_and_repaired(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {"path": "a.txt", "content": "a\n"},
                                {"path": "b.txt", "content": "b\n"},
                                {"path": "c.txt", "content": "c\n"},
                            ]
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {"path": "a.txt", "content": "a\n"},
                                {"path": "b.txt", "content": "b\n"},
                            ]
                        },
                    }
                ]
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Wrote narrowed batch.",
                    "artifacts": [{"id": "change_summary", "content": {"summary": "wrote two files"}}],
                }
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("write_many_files",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=2,
        max_model_calls=3,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_policy": {"batch_max_files": 2, "step_max_files": 5, "repair_attempts": 1},
            }
        }
    )
    trace = RuntimeMatrixLogger()

    result = runner.run(task, trace=trace)

    assert result.status == "completed"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b\n"
    assert not (tmp_path / "c.txt").exists()
    rows = trace.snapshot()["rows"]
    assert any(row["event"] == "worker_tool_call_denied" for row in rows)
    denial_artifact = next(artifact for artifact in result.artifacts if artifact.kind == "tool_denial")
    assert denial_artifact.content["observation"]["denial"]["code"] == "write_batch_too_broad"


def test_bounded_mutation_denial_exhaustion_is_retryable_instance_failure(tmp_path: Path) -> None:
    too_broad_call = {
        "tool_calls": [
            {
                "tool_name": "write_many_files",
                "arguments": {
                    "files": [
                        {"path": "a.txt", "content": "a\n"},
                        {"path": "b.txt", "content": "b\n"},
                        {"path": "c.txt", "content": "c\n"},
                    ]
                },
            }
        ]
    }
    client = QueueClient([too_broad_call, too_broad_call])
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("write_many_files",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
        max_tool_calls=2,
        max_model_calls=2,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "write_policy": {"batch_max_files": 2, "step_max_files": 5, "repair_attempts": 1},
            }
        }
    )

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["issue_code"] == "write_operation_denied_after_repair"
    assert result.metadata["retryable"] is True
    assert not any(tmp_path.glob("*.txt"))


def test_code_worker_rejects_completed_mutation_without_write_observation(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("value = 'old'\n", encoding="utf-8")
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/checkout.py"}}]},
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Patch applied.",
                    "artifacts": [
                        {"id": "change_summary", "content": "claimed change"},
                        {"id": "rollback_patch", "content": "claimed rollback"},
                    ],
                }
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("read_file", "replace_in_file"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary", "rollback_patch"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/checkout.py"],
        },
        max_tool_calls=1,
        max_model_calls=2,
    )

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["issue_code"] == "mutation_completed_without_write"
    assert result.metadata["retryable"] is True
    assert "old" in (tmp_path / "src" / "checkout.py").read_text(encoding="utf-8")


def test_completed_mutation_without_current_write_allows_same_step_kernel_memory(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Finalized resumed mutation.",
                    "artifacts": [
                            {"id": "change_summary", "content": {"summary": "finished from prior writes"}},
                                {
                                    "id": "rollback_patch",
                                    "content": {
                                        "changed_paths": ["src/checkout.py"],
                                        "diff": "prior attempt supplied rollback evidence",
                                    },
                                },
                    ],
                }
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("read_file", "replace_in_file"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary", "rollback_patch"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/checkout.py"],
        },
        max_tool_calls=0,
        max_model_calls=1,
    ).model_copy(
        update={
            "metadata": {
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "kernel_memory": {
                    "step_id": "step_1",
                    "successful_write_count": 1,
                    "successful_write_operations": [
                        {
                            "tool_name": "replace_in_file",
                            "action": "replace",
                            "status": "applied",
                            "paths": ["src/checkout.py"],
                        }
                    ],
                },
            }
        }
    )

    result = runner.run(task)

    assert result.status == "completed"
    assert result.metadata["artifact_quality"]["missing_count"] == 0


def test_verify_worker_synthesizes_failed_verification_after_model_budget_exhaustion(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "run_readonly_command",
                        "arguments": {"command": ["python", "-m", "pytest", "missing_test.py"]},
                    }
                ]
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="verify_worker",
        templates=[
            WorkerInstanceTemplate(
                name="verification_runner",
                role="verify",
                system_prompt="verify",
                allowed_tools=("run_readonly_command",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="verify_worker",
        expected_outputs=["test_results", "verification_results"],
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": True,
            "web_research": False,
        },
        max_tool_calls=1,
        max_model_calls=1,
    )

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["fallback"] == "verification_observation_synthesis"
    artifact_ids = {artifact.id for artifact in result.artifacts}
    assert {"test_results", "verification_results"} <= artifact_ids


def test_agentic_worker_group_fanout_and_artifact_handoff(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "sources found",
                    "artifacts": [{"id": "source_links", "content": ["https://example.test/a"]}],
                }
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "citations formatted",
                    "artifacts": [{"id": "final_artifact", "content": "cited result"}],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="web_research_worker",
        templates=[
            WorkerInstanceTemplate(name="source_discovery", role="find sources"),
            WorkerInstanceTemplate(name="citation_formatter", role="format citations"),
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path, web_search_provider="disabled")),
    )

    result = group.run(
        _task(
            worker_type="web_research_worker",
            expected_outputs=["final_artifact"],
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            },
            max_model_calls=2,
        )
    )

    assert result.status == "completed"
    assert "source_links" in client.prompts[1]
    assert {artifact.id for artifact in result.artifacts} >= {"source_links", "final_artifact"}
    assert len(result.metadata["worker_group_results"]) == 2


def test_agentic_worker_group_skips_later_instances_when_outputs_are_done(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "done early",
                    "artifacts": [{"id": "final_artifact", "content": "complete"}],
                }
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(name="repo_locator", role="locate"),
            WorkerInstanceTemplate(name="repo_reader", role="read"),
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", expected_outputs=["final_artifact"], max_model_calls=2))

    assert result.status == "completed"
    assert len(client.prompts) == 1
    assert len(result.metadata["worker_group_results"]) == 1
    assert result.metadata["skipped_worker_instances"] == ["repo_reader"]


def test_repo_worker_does_not_skip_reader_for_source_evidence_outputs(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "located candidates",
                    "artifacts": [
                        {"id": "candidate_paths", "content": ["src/app.py"]},
                        {"id": "api_surface_map", "content": {"paths": ["src/app.py"]}},
                    ],
                }
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "read candidates",
                    "artifacts": [
                        {"id": "candidate_paths", "content": ["src/app.py"]},
                        {"id": "api_surface_map", "content": {"functions": ["handle"]}},
                    ],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(name="repo_locator", role="locate"),
            WorkerInstanceTemplate(name="repo_reader", role="read"),
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(
        _task(
            worker_type="repo_worker",
            expected_outputs=["candidate_paths", "api_surface_map"],
            max_model_calls=2,
        )
    )

    assert result.status == "completed"
    assert len(client.prompts) == 2
    assert len(result.metadata["worker_group_results"]) == 2
    assert result.metadata["skipped_worker_instances"] == []


def test_agentic_group_rejects_null_expected_artifact_content(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "designed scope",
                    "artifacts": [{"id": "mutation_scope", "content": None}],
                }
            }
        ]
    )
    trace = RuntimeMatrixLogger()
    group = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[WorkerInstanceTemplate(name="code_agent", role="design")],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(
        _task(
            worker_type="code_worker",
            expected_outputs=["mutation_scope"],
            max_model_calls=1,
        ),
        trace=trace,
    )

    assert result.status == "failed"
    assert result.metadata["issue_code"] == "worker_artifact_content_empty"
    assert result.metadata["retryable"] is True
    assert result.metadata["artifact_quality"]["empty_artifacts"] == ["mutation_scope"]
    assert any(row["event"] == "worker_artifact_quality_checked" for row in trace.snapshot()["rows"])


def test_agentic_group_repairs_metadata_only_expected_artifact_content(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "located repo targets",
                    "artifacts": [
                        {
                            "id": "repo_inventory",
                            "content": None,
                            "metadata": {"files": ["src/checkout.py", "tests/test_checkout.py"]},
                        }
                    ],
                }
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[WorkerInstanceTemplate(name="repo_locator", role="locate")],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(
        _task(
            worker_type="repo_worker",
            expected_outputs=["repo_inventory"],
            max_model_calls=1,
        )
    )

    assert result.status == "completed"
    assert result.artifacts[0].content == {"files": ["src/checkout.py", "tests/test_checkout.py"]}
    assert result.artifacts[0].metadata["content_repaired_from_metadata"] is True
    assert result.metadata["artifact_quality"]["empty_artifacts"] == []


def test_verify_worker_reserves_finalization_model_call(tmp_path: Path) -> None:
    group = AgenticWorkerGroupRunner(
        worker_type="verify_worker",
        templates=[
            WorkerInstanceTemplate(
                name="verification_runner",
                role="verify",
                allowed_tools=("runtime_capabilities", "run_project_tests"),
            )
        ],
        controller=WorkerLLMController(QueueClient([])),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    step = PlanStep(
        step_id="verify",
        worker_type="verify_worker",
        phase="VERIFY",
        mode="verify_only",
        instruction="verify",
        max_tool_calls=4,
        max_model_calls=1,
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": True,
            "web_research": False,
        },
    )

    assert group.minimum_model_calls(step) == 3


def test_agentic_worker_group_records_model_and_tool_matrix_rows(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/app.py"}}]},
            {
                "final_result": {
                    "status": "completed",
                    "summary": "read app",
                    "artifacts": [{"id": "final_artifact", "content": "value = 1"}],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_reader",
                role="read",
                allowed_tools=("read_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    trace = RuntimeMatrixLogger()

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=1, max_model_calls=2), trace=trace)
    events = [row["event"] for row in trace.snapshot()["rows"]]

    assert result.status == "completed"
    assert "worker_group_started" in events
    assert "worker_instance_started" in events
    assert events.count("worker_model_call_started") == 2
    assert "worker_tool_call_started" in events
    assert "worker_tool_call_completed" in events
    assert "worker_group_completed" in events


def test_worker_llm_controller_normalizes_common_tool_call_aliases() -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {"name": "list_dir", "args": {"path": "."}},
                    {"name": "text_search", "arguments": {"pattern": "idempotency"}},
                ]
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.tool_calls[0].tool_name == "list_dir"
    assert decision.tool_calls[0].arguments == {"path": "."}
    assert decision.tool_calls[1].tool_name == "text_search"


def test_worker_llm_controller_normalizes_provider_tool_call_variants() -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "repo_snapshot",
                        "tool_args": {"path": ".", "max_depth": 2},
                        "call_id": "snapshot_1",
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "list_dir",
                        "input": {"path": "src"},
                    },
                    {
                        "toolName": "text_search",
                        "parameters": {"pattern": "idempotency"},
                    },
                ]
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.tool_calls[0].tool_name == "repo_snapshot"
    assert decision.tool_calls[0].arguments == {"path": ".", "max_depth": 2}
    assert decision.tool_calls[1].tool_name == "list_dir"
    assert decision.tool_calls[1].arguments == {"path": "src"}
    assert decision.tool_calls[2].tool_name == "text_search"
    assert decision.tool_calls[2].arguments == {"pattern": "idempotency"}


def test_worker_llm_controller_normalizes_root_level_tool_call() -> None:
    client = QueueClient([{"name": "list_dir", "arguments": {"path": "."}}])

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].tool_name == "list_dir"


def test_worker_llm_controller_normalizes_openai_function_tool_call() -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"path\": \"src/checkout.py\"}",
                        },
                    }
                ]
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.tool_calls[0].tool_name == "read_file"
    assert decision.tool_calls[0].arguments == {"path": "src/checkout.py"}


def test_worker_llm_controller_normalizes_root_level_final_status() -> None:
    client = QueueClient(
        [
            {
                "status": "needs_replan",
                "reason": "Discovery did not produce target files.",
                "missing_artifacts": ["target_files", "test_command"],
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="research_worker_context_synthesizer", prompt="{}")

    assert decision.final_result is not None
    assert decision.final_result.status == "needs_replan"
    assert decision.final_result.summary == "Discovery did not produce target files."
    assert decision.final_result.issues[0].issue_type == "plan_failure"
    assert decision.final_result.issues[0].metadata["missing_artifacts"] == ["target_files", "test_command"]


def test_worker_llm_controller_normalizes_stringified_final_result() -> None:
    client = QueueClient(
        [
            {
                "final_result": (
                    '{"status":"completed","summary":"Report written.",'
                    '"artifacts":[{"id":"bug_report","content":{"path":"REPORT.md"}}]}'
                )
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="filesystem_worker_filesystem_operator", prompt="{}")

    assert decision.final_result is not None
    assert decision.final_result.status == "completed"
    assert decision.final_result.artifacts[0].id == "bug_report"


def test_worker_llm_controller_normalizes_stringified_tool_calls() -> None:
    client = QueueClient(
        [
            {
                "tool_calls": (
                    '[{"tool_name":"read_file","arguments":{"path":"src/app.py"}}]'
                )
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_reader", prompt="{}")

    assert decision.tool_calls[0].tool_name == "read_file"
    assert decision.tool_calls[0].arguments == {"path": "src/app.py"}


def test_worker_llm_controller_converts_final_result_fields_to_artifacts() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "success",
                    "summary": "Discovery complete.",
                    "repo_inventory": ["README.md", "src/checkout.py"],
                    "candidate_retry_locations": ["src/checkout.py::build_charge_headers"],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.final_result is not None
    assert decision.final_result.status == "completed"
    artifact_ids = {artifact.id for artifact in decision.final_result.artifacts}
    assert {"repo_inventory", "candidate_retry_locations"} <= artifact_ids


def test_worker_llm_controller_normalizes_bare_artifact_ids_as_placeholders() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Declared outputs.",
                    "artifacts": ["repo_inventory"],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.final_result is not None
    artifact = decision.final_result.artifacts[0]
    assert artifact.id == "repo_inventory"
    assert artifact.content is None
    assert artifact.metadata["worker_returned_bare_artifact_id"] is True


def test_worker_llm_controller_normalizes_string_issues() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "needs_replan",
                    "summary": "Need source evidence.",
                    "issues": ["Missing source code content for src/checkout.py."],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="research_worker_context_synthesizer", prompt="{}")

    assert decision.final_result is not None
    assert decision.final_result.issues[0].issue_type == "plan_failure"
    assert decision.final_result.issues[0].message == "Missing source code content for src/checkout.py."


def test_worker_llm_controller_normalizes_type_and_detail_issue_fields() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "needs_replan",
                    "summary": "Need more source evidence.",
                    "issues": [
                        {
                            "type": "plan_failure",
                            "code": "missing_source_contents",
                            "detail": "Cannot analyze root_cause without file contents.",
                            "artifact": "candidate_paths",
                        }
                    ],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="research_worker_context_synthesizer", prompt="{}")

    assert decision.final_result is not None
    issue = decision.final_result.issues[0]
    assert issue.issue_type == "plan_failure"
    assert issue.code == "missing_source_contents"
    assert issue.message == "Cannot analyze root_cause without file contents."
    assert issue.metadata["artifact"] == "candidate_paths"


def test_research_worker_template_can_use_readonly_tools_when_permitted(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "name": "read_file",
                        "arguments": {"path": "src/checkout.py"},
                    }
                ],
                "final_result": {
                    "status": "completed",
                    "summary": "This same-turn final result must wait for the next model turn.",
                    "artifacts": [{"id": "ignored_same_turn_artifact", "content": "ignored"}],
                },
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Read source.",
                    "artifacts": [{"id": "final_artifact", "content": "source read"}],
                },
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="research_worker",
        templates=[
            WorkerInstanceTemplate(
                name="context_synthesizer",
                role="synthesize",
                allowed_tools=("read_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path, web_search_provider="disabled")),
    )

    result = group.run(_task(worker_type="research_worker", max_tool_calls=1, max_model_calls=2))

    assert result.status == "completed"
    assert result.usage["model_calls"] == 2
    assert any(artifact.id == "final_artifact" for artifact in result.artifacts)
    assert all(artifact.id != "ignored_same_turn_artifact" for artifact in result.artifacts)


def test_agentic_prompt_uses_worker_system_prompt_and_function_tool_specs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Located repo context.",
                    "artifacts": [{"id": "final_artifact", "content": "repo context"}],
                }
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_locator",
                role="locate files",
                system_prompt="You are the repository discovery worker.",
                allowed_tools=("list_dir", "read_file"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=1, max_model_calls=1))
    payload = json.loads(client.prompts[0])

    assert result.status == "completed"
    assert payload["instance"]["system_prompt"] == "You are the repository discovery worker."
    assert payload["available_tools"][0]["type"] == "function"
    assert payload["available_tools"][0]["function"]["name"] == "list_dir"
    assert any("expected_output_contract.artifact_shape" in item for item in payload["instructions"])
    assert any("issue_type must be exactly" in item for item in payload["instructions"])


def test_agentic_prompt_hides_tools_after_tool_budget_is_spent(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/checkout.py"}}]},
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Finalized from observations.",
                    "artifacts": [{"id": "final_artifact", "content": "done"}],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_locator",
                role="locate files",
                system_prompt="You are the repository discovery worker.",
                allowed_tools=("read_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=1, max_model_calls=2))
    second_prompt = json.loads(client.prompts[1])

    assert result.status == "completed"
    assert second_prompt["runtime_budget"]["remaining_tool_calls"] == 0
    assert second_prompt["available_tools"] == []


def test_agentic_group_does_not_count_bare_artifact_ids_as_completed_outputs(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Only declared artifact names.",
                    "artifacts": ["final_artifact"],
                }
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[WorkerInstanceTemplate(name="repo_locator", role="locate files")],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=0, max_model_calls=1))

    assert result.status == "failed"
    assert result.metadata["issues"][0]["code"] == "worker_output_contract_miss"
    assert result.metadata["issues"][0]["retryable"] is True
    assert result.metadata["issues"][0]["metadata"]["missing_artifacts"] == ["final_artifact"]


def test_normalize_worker_decision_repairs_embedded_tool_arguments() -> None:
    decision = _normalize_worker_decision(
        {
            "tool_calls": [
                {
                    "tool_name": "tool read_many_files', 'arguments': {'paths': ['src/checkout.py', 'tests/test_checkout.py']}}}",
                }
            ]
        }
    )

    assert decision["tool_calls"][0]["tool_name"] == "read_many_files"
    assert decision["tool_calls"][0]["arguments"] == {
        "paths": ["src/checkout.py", "tests/test_checkout.py"]
    }


def test_tool_observation_without_final_model_budget_is_kernel_budget_issue(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient([{"tool_calls": [{"name": "list_dir", "arguments": {"path": "."}}]}])
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_locator",
                role="locate files",
                allowed_tools=("list_dir",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(max_tool_calls=1, max_model_calls=1))

    assert result.status == "budget_exceeded"
    assert result.metadata["issues"][0]["issue_type"] == "instance_failure"
    assert result.metadata["issues"][0]["code"] == "model_budget_exhausted_before_final_result"
    assert any(artifact.kind == "tool_observation_summary" for artifact in result.artifacts)


def test_worker_llm_controller_raises_model_decision_error_for_invalid_json() -> None:
    controller = WorkerLLMController(RawQueueClient(["not-json"]))

    with pytest.raises(WorkerModelDecisionError):
        controller.decide(stage="repo_worker_repo_locator", prompt="{}")


def test_agent_run_loop_repairs_one_malformed_model_decision_locally(tmp_path: Path) -> None:
    client = RawQueueClient(
        [
            "not-json",
            json.dumps(
                {
                    "final_result": {
                        "status": "completed",
                        "summary": "Recovered after schema repair feedback.",
                        "artifacts": [{"id": "final_artifact", "content": {"ok": True}}],
                    }
                }
            ),
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[WorkerInstanceTemplate(name="repo_locator", role="locate files")],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    trace = RuntimeMatrixLogger()

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=0, max_model_calls=2), trace=trace)

    assert result.status == "completed"
    assert result.usage["model_calls"] == 2
    assert len(client.prompts) == 2
    assert "model_behavior_error" in client.prompts[1]
    assert any(
        row["event"] == "worker_model_decision_repair_scheduled"
        for row in trace.snapshot()["rows"]
    )


def test_artifact_contract_quality_reports_invalid_core_artifact_separately() -> None:
    quality = evaluate_artifact_quality(
        expected_outputs=["mutation_scope", "final_report"],
        artifacts=[
            ArtifactPayload(id="mutation_scope", content={"reason": "missing target paths"}),
            ArtifactPayload(id="final_report", content="Human-readable report is valid."),
        ],
    )

    assert quality["missing_artifacts"] == []
    assert quality["empty_artifacts"] == []
    assert quality["invalid_count"] == 1
    assert quality["invalid_artifacts"][0]["artifact_id"] == "mutation_scope"
    assert quality["invalid_artifacts"][0]["field"] == "target_paths"


def test_file_management_artifact_contracts_preserve_manifest_schema() -> None:
    manifest_contract = artifact_contract("manifest_file")
    update_contract = artifact_contract("manifest_update_record")
    record_contract = artifact_contract("moved_items_record")

    manifest_payload = manifest_contract["artifact_shape"]["content"]["payload"]
    update_payload = update_contract["artifact_shape"]["content"]["payload"]
    record_payload = record_contract["artifact_shape"]["content"]
    assert "moved_logs" in manifest_payload
    assert "moved_logs" in update_payload
    assert "moved_build_logs" not in manifest_payload
    assert "moved_json_artifacts" in record_payload
    assert "moved_json_files" not in record_payload

    quality = evaluate_artifact_quality(
        expected_outputs=["manifest_file"],
        artifacts=[ArtifactPayload(id="manifest_file", content={"payload": {"moved_logs": []}})],
    )

    assert quality["invalid_artifacts"][0]["field"] == "manifest_path"

    missing_log_quality = evaluate_artifact_quality(
        expected_outputs=["moved_items_record"],
        artifacts=[
            ArtifactPayload(
                id="moved_items_record",
                content={
                    "moved_documents": ["task_notes.md"],
                    "moved_json_artifacts": ["error_dump.json"],
                    "total_artifacts": 2,
                },
            )
        ],
    )
    assert missing_log_quality["invalid_artifacts"][0]["field"] == "moved_logs"

    bad_total_quality = evaluate_artifact_quality(
        expected_outputs=["manifest_update_record"],
        artifacts=[
            ArtifactPayload(
                id="manifest_update_record",
                content={
                    "manifest_path": "docs/workspace_manifest.json",
                    "payload": {
                        "moved_documents": ["task_notes.md"],
                        "moved_logs": ["old_build.log"],
                        "moved_json_artifacts": [],
                        "total_artifacts": 1,
                    },
                    "fields_present": ["moved_documents", "moved_logs", "moved_json_artifacts", "total_artifacts"],
                    "missing_fields": [],
                    "counts_match": True,
                    "total_artifacts": 1,
                },
            )
        ],
    )
    assert any(item["code"] == "artifact_total_mismatch" for item in bad_total_quality["invalid_artifacts"])

    alias_quality = evaluate_artifact_quality(
        expected_outputs=["manifest_update_result"],
        artifacts=[
            ArtifactPayload(
                id="manifest_update_result",
                content={
                    "manifest_path": "docs/workspace_manifest.json",
                    "payload": {
                        "moved_documents": ["task_notes.md"],
                        "moved_logs": ["old_build.log"],
                        "moved_json_artifacts": ["error_dump.json"],
                        "total_artifacts": 3,
                    },
                    "fields_present": ["moved_documents", "moved_logs", "moved_json_artifacts", "total_artifacts"],
                    "missing_fields": [],
                    "counts_match": True,
                    "total_artifacts": 3,
                },
            )
        ],
    )
    assert alias_quality["missing_artifacts"] == []
    assert alias_quality["invalid_artifacts"] == []
    assert alias_quality["canonical_aliases"]["expected_outputs"][0]["canonical"] == "manifest_update_record"
    assert alias_quality["canonical_aliases"]["produced_artifacts"][0]["canonical"] == "manifest_update_record"


def test_verification_artifact_contracts_require_provenance_for_passing_status() -> None:
    context = {"phase": "VERIFY", "mode": "verify_only", "worker_type": "verify_worker"}
    weak_quality = evaluate_artifact_quality(
        expected_outputs=["verification_results", "test_results"],
        artifacts=[
            ArtifactPayload(id="verification_results", content={"status": "passed"}),
            ArtifactPayload(id="test_results", content={"status": "passed", "failed_commands": []}),
        ],
        contract_context=context,
    )

    codes = {item["code"] for item in weak_quality["invalid_artifacts"]}
    assert "artifact_missing_verification_evidence" in codes
    assert "artifact_missing_command_evidence" in codes

    strong_quality = evaluate_artifact_quality(
        expected_outputs=["verification_results", "test_results"],
        artifacts=[
            ArtifactPayload(
                id="verification_results",
                content={
                    "status": "passed",
                    "file_state_verification": {"status": "passed", "errors": []},
                },
            ),
            ArtifactPayload(
                id="test_results",
                content={
                    "status": "passed",
                    "commands": [{"command": ["pytest", "-q"], "returncode": 0}],
                    "failed_commands": [],
                },
            ),
        ],
        contract_context=context,
    )

    assert strong_quality["invalid_artifacts"] == []


def test_file_management_artifact_contract_uses_literal_manifest_schema() -> None:
    context = {
        "required_json_keys": [
            "moved_documents",
            "moved_evidence",
            "moved_logs",
            "moved_exports",
            "held_items",
            "total_moved",
        ]
    }
    record_contract = artifact_contract("moved_items_record", contract_context=context)

    assert "moved_evidence" in record_contract["artifact_shape"]["content"]
    assert "moved_json_artifacts" not in record_contract["artifact_shape"]["content"]

    quality = evaluate_artifact_quality(
        expected_outputs=["moved_items_record", "manifest_update_record"],
        artifacts=[
            ArtifactPayload(
                id="moved_items_record",
                content={
                    "moved_documents": ["client_alpha_notes.md", "client_beta_followup.md", "retention_policy.md"],
                    "moved_evidence": ["access_review.json", "vendor_exception.json"],
                    "moved_logs": ["audit_2026_06.log"],
                    "moved_exports": ["owner_map.csv", "policy_matrix.csv"],
                    "held_items": ["client_gamma_hold.md", "debug_keep.log"],
                    "total_moved": 8,
                },
            ),
            ArtifactPayload(
                id="manifest_update_record",
                content={
                    "manifest_path": "records/archive_index.json",
                    "payload": {
                        "moved_documents": ["client_alpha_notes.md", "client_beta_followup.md", "retention_policy.md"],
                        "moved_evidence": ["access_review.json", "vendor_exception.json"],
                        "moved_logs": ["audit_2026_06.log"],
                        "moved_exports": ["owner_map.csv", "policy_matrix.csv"],
                        "held_items": ["client_gamma_hold.md", "debug_keep.log"],
                        "total_moved": 8,
                    },
                    "fields_present": [
                        "held_items",
                        "moved_documents",
                        "moved_evidence",
                        "moved_exports",
                        "moved_logs",
                        "total_moved",
                    ],
                    "missing_fields": [],
                    "counts_match": True,
                    "total_value": 8,
                },
            ),
        ],
        contract_context=context,
    )

    assert quality["invalid_artifacts"] == []

    bad_quality = evaluate_artifact_quality(
        expected_outputs=["moved_items_record"],
        artifacts=[
            ArtifactPayload(
                id="moved_items_record",
                content={
                    "moved_documents": [],
                    "moved_json_artifacts": ["access_review.json"],
                    "moved_logs": [],
                    "moved_exports": [],
                    "held_items": [],
                    "total_moved": 1,
                },
            )
        ],
        contract_context=context,
    )

    assert bad_quality["invalid_artifacts"][0]["field"] == "moved_evidence"


def test_manifest_literal_contract_is_stage_aware_for_analysis_evidence() -> None:
    analyze_context = {
        "phase": "ANALYZE",
        "mode": "observe_only",
        "worker_type": "research_worker",
        "required_json_keys": [
            "moved_documents",
            "moved_evidence",
            "moved_logs",
            "moved_exports",
            "held_items",
            "total_moved",
        ],
    }
    artifacts = [
        ArtifactPayload(
            id="moved_items_record",
            content={
                "moved_documents": ["client_alpha_notes.md", "client_beta_followup.md", "retention_policy.md"],
                "moved_json_artifacts": ["access_review.json", "vendor_exception.json"],
                "moved_logs": ["audit_2026_06.log"],
                "total_artifacts": 6,
            },
        ),
        ArtifactPayload(
            id="manifest_validation",
            content={
                "manifest_exists": True,
                "fields_present": [
                    "moved_documents",
                    "moved_logs",
                    "moved_json_artifacts",
                    "total_artifacts",
                ],
                "counts_match": True,
                "total_artifacts": 6,
            },
        ),
    ]

    analyze_quality = evaluate_artifact_quality(
        expected_outputs=["moved_items_record", "manifest_validation"],
        artifacts=artifacts,
        contract_context=analyze_context,
    )
    mutate_quality = evaluate_artifact_quality(
        expected_outputs=["moved_items_record", "manifest_validation"],
        artifacts=artifacts,
        contract_context={**analyze_context, "phase": "MUTATE", "worker_type": "filesystem_worker"},
    )

    assert analyze_quality["invalid_artifacts"] == []
    assert {item["field"] for item in mutate_quality["invalid_artifacts"]} >= {
        "moved_evidence",
        "held_items",
    }


def test_mutation_quality_repair_synthesizes_manifest_artifacts_from_tool_output(tmp_path: Path) -> None:
    manifest_payload = {
        "held_items": ["client_gamma_hold.md", "debug_keep.log"],
        "moved_documents": ["client_alpha_notes.md"],
        "moved_evidence": ["access_review.json"],
        "moved_exports": ["owner_map.csv"],
        "moved_logs": ["audit_2026_06.log"],
        "total_moved": 4,
    }
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "write_json_manifest",
                        "arguments": {
                            "path": "records/archive_index.json",
                            "payload": manifest_payload,
                            "required_keys": [
                                "held_items",
                                "moved_documents",
                                "moved_evidence",
                                "moved_exports",
                                "moved_logs",
                            ],
                            "total_key": "total_moved",
                            "count_keys": [
                                "moved_documents",
                                "moved_evidence",
                                "moved_exports",
                                "moved_logs",
                            ],
                        },
                    }
                ]
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "legacy summary",
                    "artifacts": [
                        {
                            "id": "moved_items_record",
                            "content": {
                                "moved_documents": ["client_alpha_notes.md"],
                                "moved_json_artifacts": ["access_review.json"],
                                "moved_logs": ["audit_2026_06.log"],
                                "total_artifacts": 3,
                            },
                        },
                        {
                            "id": "manifest_update_record",
                            "content": {
                                "manifest_path": "records/archive_index.json",
                                "payload": manifest_payload,
                                "fields_present": sorted(manifest_payload),
                                "missing_fields": [],
                                "counts_match": True,
                                "total_artifacts": 4,
                            },
                        },
                        {"id": "change_summary", "content": {"summary": "changed"}},
                        {"id": "rollback_patch", "content": {"diff": ""}},
                        {"id": "patch_diff", "content": {"diff": ""}},
                    ],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="mutate files",
                allowed_tools=("write_json_manifest",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = Task(
        task_id="task_1",
        run_id="run_1",
        step_id="mutate",
        worker_type="filesystem_worker",
        instruction="write archive manifest",
        expected_outputs=[
            "change_summary",
            "moved_items_record",
            "manifest_update_record",
        ],
        max_tool_calls=2,
        max_model_calls=2,
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["records/archive_index.json"],
            "write_paths_from_artifacts": [],
        },
        metadata={
            "phase": "MUTATE",
            "mode": "bounded_mutation",
            "required_json_keys": [
                "held_items",
                "moved_documents",
                "moved_evidence",
                "moved_exports",
                "moved_logs",
                "total_moved",
            ],
        },
    )

    result = group.run(task)
    by_id = {artifact.id: artifact for artifact in result.artifacts}

    assert result.status == "completed"
    assert by_id["moved_items_record"].content == manifest_payload
    assert by_id["manifest_update_record"].content["payload"] == manifest_payload
    assert result.metadata["artifact_quality_repaired_after_model_output"] is True
    assert evaluate_artifact_quality(
        expected_outputs=task.expected_outputs,
        artifacts=result.artifacts,
        contract_context={
            "phase": "MUTATE",
            "mode": "bounded_mutation",
            "worker_type": "filesystem_worker",
            "required_json_keys": task.metadata["required_json_keys"],
        },
    )["invalid_artifacts"] == []


def test_agentic_worker_rejects_disallowed_tool_as_retryable_instance_failure(tmp_path: Path) -> None:
    client = QueueClient([{"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "missing.txt"}}]}])
    group = AgenticWorkerGroupRunner(
        worker_type="direct_worker",
        templates=[WorkerInstanceTemplate(name="direct_responder", role="answer", allowed_tools=())],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(
        _task(
            worker_type="direct_worker",
            permissions={
                "read_files": True,
                "write_files": False,
                "run_commands": False,
                "web_research": False,
            },
        )
    )

    assert result.status == "failed"
    assert result.metadata["issues"][0]["issue_type"] == "instance_failure"
    assert result.metadata["issues"][0]["retryable"] is True


def test_agentic_web_search_without_provider_is_kernel_blocked(tmp_path: Path) -> None:
    client = QueueClient([{"tool_calls": [{"tool_name": "web_search", "arguments": {"query": "worker runtime"}}]}])
    group = AgenticWorkerGroupRunner(
        worker_type="web_research_worker",
        templates=[
            WorkerInstanceTemplate(
                name="source_discovery",
                role="find sources",
                allowed_tools=("web_search",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path, web_search_provider="disabled")),
    )

    result = group.run(
        _task(
            worker_type="web_research_worker",
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            },
            max_tool_calls=1,
            max_model_calls=1,
        )
    )

    assert result.status == "blocked"
    assert result.metadata["issues"][0]["issue_type"] == "kernel_failure"
    assert result.metadata["issues"][0]["code"] == "tool_unavailable"
