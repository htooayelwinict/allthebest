import json
from typing import Any

import pytest

from appV2.decomposer.runtime import DecomposerRuntime
from appV2.schemas import Envelope


class QueueClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.calls.append({"stage": stage, "prompt": prompt, "schema": schema})
        response = self.responses[stage]
        if isinstance(response, str):
            return response
        return json.dumps(response)


def _simple_response() -> dict[str, Any]:
    return {
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


def test_decomposer_simple_prompt_uses_one_model_call() -> None:
    client = QueueClient({"decompose_request": _simple_response()})

    envelope = DecomposerRuntime(model_client=client).run("what is docker")

    assert envelope.request_id.startswith("v2_req_")
    assert envelope.input_type == "docker_concept_question"
    assert [call["stage"] for call in client.calls] == ["decompose_request"]
    assert envelope.metadata["appv2_decomposer"]["model_calls"] == 1


def test_decomposer_enriches_file_code_contracts_and_literals() -> None:
    response = _simple_response()
    response.update(
        {
            "normalized_input": "Clean workspace and write manifest.",
            "input_type": "file_management_cleanup_request",
            "intents": ["file.manage"],
            "domains": ["files"],
            "risks": ["file_mutation"],
            "confidence": 0.88,
        }
    )
    client = QueueClient(
        {
            "decompose_request": response,
            "enrich_file_code_contracts": {
                "artifacts": [{"name": "docs/workspace_manifest.json", "type": "manifest"}],
                "context_needed": ["repo_tree"],
                "constraints": ["preserve_manifest_keys"],
                "risks": ["needs_verification"],
                "literal_contract": [{"value": "moved_documents", "kind": "json_key", "source": "model"}],
            },
        }
    )

    envelope = DecomposerRuntime(model_client=client).run(
        "Move docs and write docs/workspace_manifest.json with moved_documents and total_artifacts"
    )

    assert [call["stage"] for call in client.calls] == ["decompose_request", "enrich_file_code_contracts"]
    literals = {literal.value: literal.kind for literal in envelope.literal_contract}
    assert literals["docs/workspace_manifest.json"] == "path"
    assert literals["moved_documents"] == "json_key"
    assert "repo_tree" in envelope.context_needed


def test_decomposer_repairs_validation_failure_once() -> None:
    invalid = _simple_response()
    invalid["metadata"] = {"phases": ["DISCOVER"]}
    repaired = _simple_response()
    repaired["metadata"] = {}
    client = QueueClient({"decompose_request": invalid, "repair_envelope": repaired})

    envelope = DecomposerRuntime(model_client=client).run("what is docker")

    assert envelope.metadata["appv2_decomposer"]["stages"] == ["decompose_request", "repair_envelope"]
    assert [call["stage"] for call in client.calls] == ["decompose_request", "repair_envelope"]


def test_decomposer_from_env_disabled_raises(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("APPV2_DECOMPOSER_LLM_ENABLED=false\n", encoding="utf-8")

    with pytest.raises(ValueError, match="AppV2 decomposer is not configured"):
        DecomposerRuntime.from_env(str(dotenv))
