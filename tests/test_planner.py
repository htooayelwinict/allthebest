import json
from typing import Any

import pytest

from app.planner.prompt_chain import LLMPlanCompiler, PlannerPromptChainError
from app.planner.runtime import PlannerRuntime
from app.planner.validator import PlannerPlanValidator
from app.schemas import Envelope, Plan


class FakePlannerClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.calls.append({"stage": stage, "prompt": prompt, "schema": schema})
        response = self._responses[stage]
        if isinstance(response, str):
            return response
        return json.dumps(response)


def _envelope(**overrides: Any) -> Envelope:
    payload = {
        "request_id": "req_123",
        "raw_input": "integrate the sdk with async transaction apis and fix lag",
        "normalized_input": "Integrate the SDK with async transaction APIs and resolve performance lag.",
        "user_goal": "Determine SDK availability, integrate async transaction flow, and fix lag.",
        "input_type": "async_sdk_performance_refactor_request",
        "intents": ["sdk.integration", "code.fix", "performance.investigate", "research.lookup"],
        "domains": ["code", "research"],
        "risks": ["performance_cause_unknown", "ambiguous_scope", "needs_verification", "mutation_requested"],
        "artifacts": [
            {"name": "target SDK", "type": "sdk"},
            {"name": "transaction APIs", "type": "api"},
            {"name": "async function", "type": "code_pattern"},
        ],
        "context_needed": ["dependency_manifest", "repo_tree", "performance_evidence", "target_file"],
        "constraints": [
            "target_locations_must_be_identified_before_mutation",
            "performance_claims_require_evidence",
            "mutation_requires_verification",
        ],
        "complexity_hint": "high",
        "confidence": 0.6,
        "ambiguity": ["SDK package identity unspecified"],
        "assumptions": ["Async pattern is viable"],
        "metadata": {},
    }
    payload.update(overrides)
    return Envelope.model_validate(payload)


def _observe_only_plan(request_id: str = "req_123") -> Plan:
    return Plan.model_validate(
        {
            "plan_id": f"plan_{request_id}",
            "request_id": request_id,
            "planner": "llm_planner",
            "objective": "Observe first.",
            "strategy": "observe_first",
            "steps": [
                {
                    "step_id": "discover_repo",
                    "worker_type": "repo_worker",
                    "instruction": "Inspect repository scope.",
                    "output_artifacts": ["repo_inventory"],
                    "max_tool_calls": 2,
                    "max_model_calls": 1,
                    "permissions": {"read_files": True, "write_files": False, "run_commands": False},
                }
            ],
            "budget": {"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
            "success_criteria": ["Scope identified."],
            "metadata": {},
        }
    )


def _complex_multi_intent_plan(request_id: str = "req_123") -> dict[str, Any]:
    return {
        "plan_id": f"plan_{request_id}",
        "request_id": request_id,
        "planner": "llm_planner",
        "objective": "Determine SDK availability, integrate async transaction APIs, and verify lag fixes.",
        "strategy": "discover_research_patch_verify",
        "steps": [
            {
                "step_id": "repo_discovery",
                "worker_type": "repo_worker",
                "instruction": "Scan repo tree, dependency manifests, and candidate transaction API modules.",
                "output_artifacts": ["repo_inventory"],
                "max_tool_calls": 4,
                "max_model_calls": 1,
                "permissions": {"read_files": True, "write_files": False, "run_commands": False},
            },
            {
                "step_id": "performance_context",
                "worker_type": "repo_worker",
                "instruction": "Collect performance evidence and lag symptoms from code and logs.",
                "input_artifacts": ["repo_inventory"],
                "output_artifacts": ["performance_evidence"],
                "max_tool_calls": 4,
                "max_model_calls": 1,
                "permissions": {"read_files": True, "write_files": False, "run_commands": False},
            },
            {
                "step_id": "sdk_research",
                "worker_type": "research_worker",
                "instruction": "Determine SDK package availability and integration constraints.",
                "input_artifacts": ["repo_inventory"],
                "output_artifacts": ["sdk_notes"],
                "max_tool_calls": 3,
                "max_model_calls": 1,
                "permissions": {"read_files": True, "write_files": False, "run_commands": False},
            },
            {
                "step_id": "async_integration_patch",
                "worker_type": "code_worker",
                "instruction": "Patch async integration only where discovery and SDK notes identify targets.",
                "input_artifacts": ["repo_inventory", "performance_evidence", "sdk_notes"],
                "output_artifacts": ["patch_result"],
                "max_tool_calls": 6,
                "max_model_calls": 1,
                "permissions": {"read_files": True, "write_files": True, "run_commands": False},
            },
            {
                "step_id": "verify_integration",
                "worker_type": "verify_worker",
                "instruction": "Run focused verification checks for patched transaction integration.",
                "input_artifacts": ["patch_result"],
                "output_artifacts": ["verification_result"],
                "max_tool_calls": 3,
                "max_model_calls": 0,
                "permissions": {"read_files": True, "write_files": False, "run_commands": True},
            },
        ],
        "budget": {"max_tool_calls": 20, "max_model_calls": 4, "max_workers": 5, "max_retries": 0},
        "success_criteria": [
            "Dependency and targets discovered before mutation.",
            "Mutation verified with focused checks.",
        ],
        "metadata": {},
    }


def test_validator_accepts_observe_only_plan() -> None:
    plan = _observe_only_plan()
    envelope = _envelope(constraints=[], context_needed=[], confidence=0.9)

    validated = PlannerPlanValidator().validate(envelope, plan)
    assert validated.plan_id == "plan_req_123"


def test_validator_accepts_observe_patch_verify_plan() -> None:
    envelope = _envelope()
    plan = Plan.model_validate(_complex_multi_intent_plan())

    validated = PlannerPlanValidator().validate(envelope, plan)
    assert validated.steps[-1].worker_type == "verify_worker"


def test_validator_rejects_unknown_worker_type() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][0]["worker_type"] = "mystery_worker"

    with pytest.raises(ValueError, match="unknown worker_type"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_missing_input_artifact() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][3]["input_artifacts"] = ["does_not_exist"]

    with pytest.raises(ValueError, match="not produced by an earlier step"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_future_artifact_dependency() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][2]["input_artifacts"] = ["patch_result"]

    with pytest.raises(ValueError, match="not produced by an earlier step"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_budget_undercount() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["budget"] = {"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1, "max_retries": 0}

    with pytest.raises(ValueError, match="budget"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_write_before_discovery_when_required() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"] = [payload["steps"][3], payload["steps"][4]]
    payload["budget"] = {"max_tool_calls": 9, "max_model_calls": 1, "max_workers": 2, "max_retries": 0}

    with pytest.raises(ValueError, match="mutation requires a prior read-only discovery step"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_write_without_verify() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"] = payload["steps"][:-1]
    payload["budget"] = {"max_tool_calls": 17, "max_model_calls": 4, "max_workers": 4, "max_retries": 0}

    with pytest.raises(ValueError, match="verify_worker"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_prompt_chain_draft_valid_plan_succeeds() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})
    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert len(client.calls) == 1
    assert client.calls[0]["stage"] == "draft_plan"
    assert plan.metadata["llm_planner"]["mode"] == "completed"
    assert plan.steps[0].worker_type == "repo_worker"


def test_prompt_chain_repairs_invalid_plan_once() -> None:
    envelope = _envelope()
    invalid_draft = _complex_multi_intent_plan()
    invalid_draft["steps"][0]["worker_type"] = "unknown_worker"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_draft, "repair_plan": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan"]
    assert plan.metadata["llm_planner"]["mode"] == "repaired"
    assert plan.metadata["llm_planner"]["repair_attempted"] is True


def test_prompt_chain_repair_prompt_contains_validation_errors() -> None:
    envelope = _envelope()
    invalid_draft = _complex_multi_intent_plan()
    invalid_draft["steps"][0]["worker_type"] = "unknown_worker"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_draft, "repair_plan": repaired})

    LLMPlanCompiler(model_client=client).run(envelope)

    repair_prompt = client.calls[1]["prompt"]
    assert "validation_errors" in repair_prompt
    assert "unknown worker_type" in repair_prompt


def test_prompt_chain_fails_after_invalid_repair() -> None:
    envelope = _envelope()
    bad_payload = {"not": "a plan"}
    client = FakePlannerClient({"draft_plan": bad_payload, "repair_plan": bad_payload})

    with pytest.raises(PlannerPromptChainError):
        LLMPlanCompiler(model_client=client).run(envelope)


def test_prompt_contains_worker_catalog_and_envelope() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})
    LLMPlanCompiler(model_client=client).run(envelope)

    draft_prompt = client.calls[0]["prompt"]
    assert "worker_catalog" in draft_prompt
    assert "repo_worker" in draft_prompt
    assert "async_sdk_performance_refactor_request" in draft_prompt


def test_runtime_uses_injected_compiler() -> None:
    envelope = _envelope()

    class FakeCompiler:
        def run(self, envelope: Envelope) -> Plan:
            return Plan.model_validate(_complex_multi_intent_plan(request_id=envelope.request_id))

    runtime = PlannerRuntime(compiler=FakeCompiler())
    plan = runtime.run(envelope)

    assert plan.planner == "llm_planner"
    assert plan.metadata["planner_runtime"]["mode"] == "llm_prompt_chain"


def test_runtime_falls_back_safely_when_compiler_fails() -> None:
    envelope = _envelope()

    class ExplodingCompiler:
        def run(self, envelope: Envelope) -> Plan:
            raise RuntimeError("boom")

    runtime = PlannerRuntime(compiler=ExplodingCompiler(), fallback_on_error=True)
    plan = runtime.run(envelope)

    assert plan.planner == "fallback"
    assert plan.steps[0].worker_type == "repo_worker"
    assert plan.metadata["planner_runtime"]["fallback_reason"] == "planner_llm_error"


def test_runtime_without_compiler_uses_safe_fallback() -> None:
    envelope = _envelope()
    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "fallback"
    assert plan.metadata["planner_runtime"]["fallback_reason"] == "planner_llm_unavailable"


def test_complex_multi_intent_plan_shape() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})
    plan = PlannerRuntime(compiler=LLMPlanCompiler(model_client=client)).run(envelope)

    assert [step.step_id for step in plan.steps] == [
        "repo_discovery",
        "performance_context",
        "sdk_research",
        "async_integration_patch",
        "verify_integration",
    ]
    assert plan.steps[3].permissions.get("write_files") is True
    assert plan.steps[4].worker_type == "verify_worker"
