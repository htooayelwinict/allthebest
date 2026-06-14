import json
import re
from typing import Any

from appV2.graph import build_graph
from tests.test_appv2_phase_planner import _plan


class FakeDecomposerClient:
    def __init__(self, **config: Any) -> None:
        self.config = config

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(
            {
                "normalized_input": "Explain Docker.",
                "user_goal": "Understand Docker.",
                "input_type": "docker_concept_question",
                "intents": ["question.answer"],
                "domains": ["infra"],
                "risks": [],
                "artifacts": [],
                "context_needed": [],
                "constraints": [],
                "complexity_hint": "low",
                "confidence": 0.92,
                "ambiguity": [],
                "assumptions": [],
                "literal_contract": [],
                "metadata": {},
            }
        )


class FakePlannerClient:
    def __init__(self, **config: Any) -> None:
        self.config = config

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        matches = re.findall(r'"request_id":\s*"([^"]+)"', prompt)
        request_id = matches[-1] if matches else "v2_req_001"
        if stage == "draft_phase_skeleton":
            return json.dumps({"objective": "Answer directly.", "strategy": "finalize", "phases": ["FINALIZE"]})
        if stage == "draft_artifact_contracts":
            return json.dumps({"artifact_contracts": [{"id": "final_report"}], "global_invariants": [], "success_criteria": ["answered"]})
        plan = {
            **_plan(),
            "request_id": request_id,
            "phases": [
                {
                    "phase_id": "finalize",
                    "phase": "FINALIZE",
                    "goal": "Answer directly.",
                    "instructions": ["Produce final report."],
                    "input_artifacts": [],
                    "output_artifacts": ["final_report"],
                    "allowed_tool_groups": [],
                    "max_tool_calls": 0,
                    "max_model_calls": 1,
                }
            ],
            "artifact_contracts": [{"id": "final_report"}],
        }
        return json.dumps(plan)


class FakeWorkerClient:
    def __init__(self, **config: Any) -> None:
        self.config = config

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(
            {
                "final_phase_output": {
                    "status": "completed",
                    "summary": "Docker packages applications with dependencies.",
                    "artifacts": [
                        {
                            "id": "final_report",
                            "kind": "final_report",
                            "content": {"summary": "Docker packages applications with dependencies."},
                            "producer": "worker",
                        }
                    ],
                }
            }
        )


def test_appv2_graph_invocation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPV2_DECOMPOSER_LLM_ENABLED", "true")
    monkeypatch.setenv("APPV2_DECOMPOSER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("APPV2_PLANNER_LLM_ENABLED", "true")
    monkeypatch.setenv("APPV2_PLANNER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("APPV2_WORKER_LLM_ENABLED", "true")
    monkeypatch.setenv("APPV2_WORKER_LLM_API_KEY", "test-key")

    graph = build_graph(
        client_factory=FakeDecomposerClient,
        planner_client_factory=FakePlannerClient,
        worker_client_factory=FakeWorkerClient,
        root_path=str(tmp_path),
    )

    state = graph.invoke({"user_input": "what is docker", "errors": []})

    assert state["result"]["status"] == "completed"
    assert "phase_plan" in state
    components = {row["component"] for row in state["runtime_matrix"]["rows"]}
    assert {"appv2_decomposer_runtime", "appv2_phase_planner_runtime", "appv2_worker_runtime"} <= components


def test_appv2_default_models_are_openrouter_targets(monkeypatch, tmp_path) -> None:
    from appV2.env_config import load_appv2_runtime_config

    for key in (
        "APPV2_DECOMPOSER_LLM_MODEL",
        "APPV2_PLANNER_LLM_MODEL",
        "APPV2_WORKER_LLM_MODEL",
        "OPENROUTER_MODEL",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "APPV2_DECOMPOSER_LLM_ENABLED=true",
                "APPV2_PLANNER_LLM_ENABLED=true",
                "APPV2_WORKER_LLM_ENABLED=true",
                "OPENROUTER_API_KEY=test-key",
            ]
        ),
        encoding="utf-8",
    )

    assert load_appv2_runtime_config("APPV2_DECOMPOSER_LLM", dotenv).model == "openai/gpt-5.3-codex"
    assert load_appv2_runtime_config("APPV2_PLANNER_LLM", dotenv).model == "openai/gpt-5.3-codex"
    assert load_appv2_runtime_config("APPV2_WORKER_LLM", dotenv).model == "xiaomi/mimo-v2.5-pro"
