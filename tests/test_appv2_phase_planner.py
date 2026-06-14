import json
from typing import Any

import pytest

from appV2.planner.runtime import PhasePlannerRuntime
from appV2.schemas import Envelope, PhasePlan, PhaseReplanRequest


class QueueClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.calls.append(stage)
        response = self.responses[stage]
        if isinstance(response, str):
            return response
        return json.dumps(response)


def _envelope() -> Envelope:
    return Envelope(
        request_id="v2_req_001",
        raw_input="clean this repo and verify",
        normalized_input="Clean this repository and verify the result.",
        user_goal="Clean repository files safely.",
        input_type="file_management_cleanup_request",
        intents=["file.manage"],
        domains=["files", "code"],
        risks=["file_mutation", "needs_verification"],
        confidence=0.9,
    )


def _plan() -> dict[str, Any]:
    return {
        "plan_id": "v2_plan_001",
        "request_id": "v2_req_001",
        "objective": "Clean repository files safely.",
        "strategy": "discover_design_mutate_verify_finalize",
        "phases": [
            {
                "phase_id": "discover",
                "phase": "DISCOVER",
                "goal": "Inspect repository.",
                "instructions": ["Read repo state."],
                "input_artifacts": [],
                "output_artifacts": ["repo_inventory"],
                "allowed_tool_groups": ["repo_read"],
                "acceptance_checks": ["repo_inventory complete"],
                "max_tool_calls": 2,
                "max_model_calls": 2,
            },
            {
                "phase_id": "design",
                "phase": "DESIGN",
                "goal": "Design safe operations.",
                "instructions": ["Create operation design."],
                "input_artifacts": ["repo_inventory"],
                "output_artifacts": ["operation_design"],
                "allowed_tool_groups": [],
                "acceptance_checks": ["operation design complete"],
                "max_tool_calls": 0,
                "max_model_calls": 1,
            },
            {
                "phase_id": "mutate",
                "phase": "MUTATE",
                "goal": "Apply safe file changes.",
                "instructions": ["Apply proposed operations."],
                "input_artifacts": ["operation_design"],
                "output_artifacts": ["change_summary"],
                "allowed_tool_groups": ["repo_read", "file_write"],
                "mutation_policy": {"mode": "advisory", "max_files": 5},
                "acceptance_checks": ["change_summary complete"],
                "max_tool_calls": 4,
                "max_model_calls": 2,
            },
            {
                "phase_id": "verify",
                "phase": "VERIFY",
                "goal": "Verify final state.",
                "instructions": ["Run verification."],
                "input_artifacts": ["change_summary"],
                "output_artifacts": ["verification_results"],
                "allowed_tool_groups": ["repo_read", "verify"],
                "verification_policy": {"required": True, "require_evidence": True},
                "acceptance_checks": ["verification_results complete"],
                "max_tool_calls": 2,
                "max_model_calls": 2,
            },
            {
                "phase_id": "finalize",
                "phase": "FINALIZE",
                "goal": "Summarize result.",
                "instructions": ["Create final report."],
                "input_artifacts": ["verification_results"],
                "output_artifacts": ["final_report"],
                "allowed_tool_groups": [],
                "acceptance_checks": ["final_report complete"],
                "max_tool_calls": 0,
                "max_model_calls": 1,
            },
        ],
        "budgets": {"max_tool_calls": 8, "max_model_calls": 6},
        "global_invariants": ["runtime_disposes_model_proposals"],
        "success_criteria": ["Repository is clean and verified."],
        "artifact_contracts": [
            {"id": "repo_inventory"},
            {"id": "operation_design"},
            {"id": "change_summary"},
            {"id": "verification_results"},
            {"id": "final_report"},
        ],
        "metadata": {},
    }


def _responses(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "draft_phase_skeleton": {
            "objective": "Clean repository files safely.",
            "strategy": "discover_design_mutate_verify_finalize",
            "phases": ["DISCOVER", "DESIGN", "MUTATE", "VERIFY", "FINALIZE"],
        },
        "draft_artifact_contracts": {
            "artifact_contracts": _plan()["artifact_contracts"],
            "global_invariants": ["runtime_disposes_model_proposals"],
            "success_criteria": ["Repository is clean and verified."],
        },
        "draft_phase_plan": plan or _plan(),
    }


def test_phase_planner_emits_phase_plan_without_worker_types() -> None:
    client = QueueClient(_responses())

    plan = PhasePlannerRuntime(model_client=client).run(_envelope())

    assert isinstance(plan, PhasePlan)
    assert [phase.phase for phase in plan.phases] == ["DISCOVER", "DESIGN", "MUTATE", "VERIFY", "FINALIZE"]
    assert not hasattr(plan.phases[0], "worker_type")
    assert client.calls == ["draft_phase_skeleton", "draft_artifact_contracts", "draft_phase_plan"]
    assert plan.metadata["appv2_phase_planner"]["model_calls"] == 3


def test_phase_planner_repairs_invalid_plan_once() -> None:
    invalid = _plan()
    invalid["phases"][2].pop("mutation_policy")
    client = QueueClient({**_responses(invalid), "repair_phase_plan": _plan()})

    plan = PhasePlannerRuntime(model_client=client).run(_envelope())

    assert plan.phases[2].mutation_policy is not None
    assert client.calls[-1] == "repair_phase_plan"


def test_phase_planner_rejects_worker_type_leak_then_repairs() -> None:
    invalid = _plan()
    invalid["phases"][0]["worker_type"] = "repo_worker"
    client = QueueClient({**_responses(invalid), "repair_phase_plan": _plan()})

    plan = PhasePlannerRuntime(model_client=client).run(_envelope())

    assert plan.plan_id == "v2_plan_001"
    assert "repair_phase_plan" in client.calls


def test_phase_planner_replan_uses_carryover_artifacts() -> None:
    replan = _plan()
    replan["plan_id"] = "v2_plan_001_replan"
    client = QueueClient({"planner_replan": replan})
    runtime = PhasePlannerRuntime(model_client=client)

    plan = runtime.replan(
        _envelope(),
        PhasePlan.model_validate(_plan()),
        PhaseReplanRequest(
            request_id="v2_req_001",
            plan_id="v2_plan_001",
            run_id="run_1",
            failed_phase_id="mutate",
            reason="planner-quality policy conflict",
            completed_phase_ids=["discover", "design"],
        ),
    )

    assert plan.plan_id == "v2_plan_001_replan"
    assert client.calls == ["planner_replan"]


def test_phase_planner_from_env_disabled_falls_back(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("APPV2_PLANNER_LLM_ENABLED=false\n", encoding="utf-8")

    plan = PhasePlannerRuntime.from_env(str(dotenv), fallback_on_error=True).run(_envelope())

    assert plan.phases[0].phase == "FINALIZE"
