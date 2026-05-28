import json
from typing import Any

from app.decompressor.runtime import DecompressorRuntime
from app.planner.runtime import PlannerRuntime


class FakePromptChainClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(self.responses[stage])


def _plan_for(text: str):
    envelope = DecompressorRuntime().run(text)
    return PlannerRuntime().run(envelope)


def _llm_code_fix_responses(planner_hint: str, planner_confidence: float) -> dict[str, Any]:
    return {
        "normalize_request": {
            "normalized_input": "fix service.py",
            "user_goal": "Repair the service.",
            "ambiguity": [],
            "assumptions": [],
        },
        "extract_artifacts": {
            "artifacts": [{"type": "file_hint", "path": "service.py", "language_hint": "python"}]
        },
        "classify_request": {
            "input_type": "mutation_request",
            "intents": ["code.fix"],
            "domains": ["code"],
            "budget_hint": "medium",
            "confidence": 0.9,
        },
        "infer_context_and_risk": {
            "risks": ["mutation_requested", "file_mutation", "needs_verification"],
            "context_needed": ["repo_tree", "target_file"],
            "execution_hints": ["inspect_target_file_before_patch", "verify_after_patch"],
            "ambiguity": [],
        },
        "recommend_planner": {
            "planner_hint": planner_hint,
            "planner_confidence": planner_confidence,
            "planner_alternatives": ["code_planner"],
        },
    }


def test_planner_selects_direct_for_question() -> None:
    plan = _plan_for("what is docker")

    assert plan.planner == "direct"
    assert len(plan.steps) == 1
    assert plan.steps[0].worker_type == "direct_worker"
    assert plan.strategy == "direct_answer"


def test_planner_selects_code_for_file_fix() -> None:
    plan = _plan_for("fix network_sniffer.py")

    assert plan.planner == "code"
    assert plan.strategy == "observe_then_patch"
    assert len(plan.steps) == 3
    assert [step.step_id for step in plan.steps] == ["observe_target", "patch_target", "verify_patch"]
    assert [step.worker_type for step in plan.steps] == ["repo_worker", "code_worker", "verify_worker"]
    assert plan.budget["max_tool_calls"] >= sum(step.max_tool_calls for step in plan.steps)


def test_planner_handles_vague_fix_with_observe_first() -> None:
    plan = _plan_for("fix the app")

    assert plan.planner == "fallback"
    assert plan.strategy == "observe_first"
    assert len(plan.steps) == 1
    assert plan.steps[0].worker_type == "repo_worker"
    assert plan.steps[0].permissions.get("write_files") is False


def test_planner_selector_honors_valid_high_confidence_hint() -> None:
    envelope = DecompressorRuntime().run("fix terraform apply error")

    assert envelope.planner_hint == "infra_planner"

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "infra"


def test_planner_honors_valid_high_confidence_hint_from_llm_envelope() -> None:
    runtime = DecompressorRuntime(
        model_client=FakePromptChainClient(_llm_code_fix_responses("infra_planner", 0.91))
    )
    envelope = runtime.run("fix service.py")

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "infra"


def test_planner_rejects_low_confidence_llm_hint_and_uses_envelope_labels() -> None:
    runtime = DecompressorRuntime(
        model_client=FakePromptChainClient(_llm_code_fix_responses("infra_planner", 0.2))
    )
    envelope = runtime.run("fix service.py")

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "code"


def test_planner_rejects_invalid_llm_hint_and_uses_envelope_labels() -> None:
    runtime = DecompressorRuntime(
        model_client=FakePromptChainClient(_llm_code_fix_responses("unknown_planner", 0.99))
    )
    envelope = runtime.run("fix service.py")

    plan = PlannerRuntime().run(envelope)

    assert envelope.planner_hint is None
    assert plan.planner == "code"
