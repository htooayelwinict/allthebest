import json
from types import SimpleNamespace
from typing import Any

from appV2.decomposer.prompt_chain import DecomposerPromptChain
from appV2.planner.contracts import ArtifactContractBundle, PhaseSkeleton
from appV2.planner.prompt_chain import PhasePlannerPromptChain
from appV2.schemas import PhasePlan, PhaseReplanRequest
from appV2.worker.agent_loop import AgentLoop
from appV2.worker.tools import ToolRegistry
from tests.test_appv2_phase_planner import _envelope


class DummyClient:
    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        raise AssertionError("prompt quality tests should not call the model")


def test_decomposer_prompt_has_production_boundary_and_schema_contract() -> None:
    chain = DecomposerPromptChain(model_client=DummyClient())

    payload = json.loads(chain._decompose_prompt("move logs to archive and keep total_artifacts"))

    contract = payload["prompt_contract"]
    assert payload["schema_contract"]["schema_name"] == "Envelope"
    assert "Return JSON only" in " ".join(contract["global_runtime_principles"])
    assert any("Do not create a phase plan" in item for item in contract["non_goals"])
    assert any("Preserve exact" in item for item in contract["global_runtime_principles"])
    assert "deterministic_literal_contract" in payload
    assert "redacted_user_input" in payload["input_delimiters"]


def test_planner_prompts_enforce_phase_artifacts_and_no_worker_types() -> None:
    chain = PhasePlannerPromptChain(model_client=DummyClient())
    envelope = _envelope()
    skeleton = PhaseSkeleton(objective="Clean files", strategy="discover_mutate_verify", phases=["DISCOVER", "MUTATE", "VERIFY", "FINALIZE"])
    contracts = ArtifactContractBundle(
        artifact_contracts=[{"id": "repo_inventory"}, {"id": "change_summary"}, {"id": "verification_results"}],
        global_invariants=["preserve literals"],
        success_criteria=["verified result"],
    )

    skeleton_payload = json.loads(chain._skeleton_prompt(envelope))
    contract_payload = json.loads(chain._artifact_contract_prompt(envelope=envelope, skeleton=skeleton))
    plan_payload = json.loads(chain._phase_plan_prompt(envelope=envelope, skeleton=skeleton, contracts=contracts))
    replan_payload = json.loads(
        chain._replan_prompt(
            envelope=envelope,
            current_plan=PhasePlan.model_validate(
                {
                    "plan_id": "p",
                    "request_id": envelope.request_id,
                    "objective": "Clean files",
                    "strategy": "discover_mutate_verify",
                    "phases": [
                        {
                            "phase_id": "finalize",
                            "phase": "FINALIZE",
                            "goal": "finish",
                            "output_artifacts": ["final_report"],
                        }
                    ],
                    "artifact_contracts": [{"id": "final_report"}],
                }
            ),
            replan_request=PhaseReplanRequest(
                request_id=envelope.request_id,
                plan_id="p",
                run_id="r",
                failed_phase_id="finalize",
                reason="semantic plan issue",
            ),
        )
    )

    assert "DISCOVER" in skeleton_payload["allowed_phases"]
    assert any("No worker" in item or "worker_type" in item for item in skeleton_payload["prompt_contract"]["non_goals"])
    assert "artifact_rules" in contract_payload["prompt_contract"]
    assert any("kind='input'" in item for item in contract_payload["prompt_contract"]["artifact_rules"])
    assert any("MUTATE" in item for item in plan_payload["prompt_contract"]["phase_step_rules"])
    assert any("request_envelope is built-in runtime scope" in item for item in plan_payload["prompt_contract"]["phase_step_rules"])
    assert "worker_type" in plan_payload["system_prompt"]
    assert any("ordinary tool denials" in item for item in replan_payload["prompt_contract"]["non_goals"])


def test_worker_prompt_explains_feedback_budget_and_replan_boundary(tmp_path) -> None:
    loop = AgentLoop(model_client=DummyClient(), tools=ToolRegistry(root_path=tmp_path))
    frame = SimpleNamespace(
        phase={"phase_id": "mutate", "phase": "MUTATE", "max_model_calls": 3, "max_tool_calls": 2},
        objective="Organize files",
        pending_outputs=["change_summary"],
        resolved_inputs=[{"id": "operation_design", "content": {"target_paths": ["README.md"]}}],
        input_artifact_contracts=[],
        output_artifact_contracts=[{"id": "change_summary", "kind": "phase_output", "content_schema": {"type": "object"}}],
        available_tools=[{"name": "write_file", "group": "file_write", "required_arguments": ["path", "content"], "argument_rules": ["Use one repo-relative file path."], "example_call": {"path": "README.md", "content": "hi", "overwrite": True}}],
        artifact_ledger={},
        mutation_ledger={},
        retry_memory={"previous_denials": ["path_not_in_strict_policy"]},
    )
    prompt = json.loads(
        loop._prompt(
            frame=frame,
            observations=[
                {
                    "status": "failed",
                    "code": "path_not_in_strict_policy",
                    "message": "Path not allowed",
                    "repairable": True,
                    "next_action": "Repair the next WorkerDecision using this feedback.",
                    "tool_name": "write_file",
                }
            ],
            model_calls=2,
            tool_calls=1,
        )
    )

    contract = prompt["prompt_contract"]
    assert prompt["schema_contract"]["schema_name"] == "WorkerDecision"
    assert contract["decision_protocol"]["exactly_one_branch"] == ["tool_calls", "mutation", "final_phase_output", "planner_replan_signal"]
    assert contract["decision_protocol"]["nested_branch_shape"] == "Nested branch payloads must be real JSON objects or arrays, never JSON-encoded strings."
    assert prompt["feedback_summary"][0]["code"] == "path_not_in_strict_policy"
    assert prompt["budget"]["remaining_model_calls_after_this_turn"] == 1
    assert prompt["budget"]["effective_model_calls"] == 3
    assert prompt["budget_pressure"]["level"] == "warning"
    assert "planner-quality" in prompt["runtime_authority"]["planner_replan_rule"]
    assert any("Do not repeat" in item for item in contract["budget_policy"])
    assert any("resolved_inputs" in item for item in contract["turn_algorithm"])
    assert any("ArtifactRecord exactly" in item for item in contract["artifact_quality_bar"])
    assert prompt["artifact_record_rules"]["forbidden_top_level_fields"] == ["summary", "status", "required", "schema"]
    assert prompt["phase_frame"]["output_artifact_contracts"][0]["id"] == "change_summary"
    assert "tool_calls: [ ... ]" in prompt["tool_call_contract"]["top_level_rule"]
    assert prompt["tool_call_contract"]["purpose_rule"] == "purpose should be short, concrete, and under 20 words."
