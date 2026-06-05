import pytest

from appV2.schemas import (
    ArtifactRecord,
    Envelope,
    MutationPolicy,
    PhaseOutputProposal,
    PhasePlan,
    PhaseStep,
    VerificationPolicy,
    WorkerDecision,
)
from appV2.validator import AppV2ValidationError, AppV2Validator


def _envelope() -> Envelope:
    return Envelope(
        request_id="req_001",
        raw_input="clean this repo",
        normalized_input="Clean and verify the repository.",
        user_goal="Clean and verify the repository.",
        input_type="file_management_request",
        intents=["file.manage"],
        domains=["files", "code"],
        risks=["file_mutation", "needs_verification"],
        confidence=0.9,
    )


def _phase(
    phase_id: str,
    phase: str,
    *,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    tool_groups: list[str] | None = None,
    mutation: bool = False,
    verify: bool = False,
) -> dict:
    payload = {
        "phase_id": phase_id,
        "phase": phase,
        "goal": f"{phase} goal",
        "instructions": ["do the phase"],
        "input_artifacts": inputs or [],
        "output_artifacts": outputs or [],
        "allowed_tool_groups": tool_groups or [],
        "acceptance_checks": ["artifact complete"],
        "max_tool_calls": 3,
        "max_model_calls": 2,
    }
    if mutation:
        payload["mutation_policy"] = {"mode": "advisory", "max_files": 5}
    if verify:
        payload["verification_policy"] = {"required": True, "require_evidence": True}
    return payload


def _valid_plan() -> PhasePlan:
    return PhasePlan.model_validate(
        {
            "plan_id": "plan_req_001",
            "request_id": "req_001",
            "objective": "Clean repo and verify.",
            "strategy": "discover_design_mutate_verify_finalize",
            "phases": [
                _phase("discover", "DISCOVER", outputs=["repo_inventory"], tool_groups=["repo_read"]),
                _phase("design", "DESIGN", inputs=["repo_inventory"], outputs=["operation_design"]),
                _phase(
                    "mutate",
                    "MUTATE",
                    inputs=["operation_design"],
                    outputs=["change_summary"],
                    tool_groups=["repo_read", "file_write"],
                    mutation=True,
                ),
                _phase(
                    "verify",
                    "VERIFY",
                    inputs=["change_summary"],
                    outputs=["verification_results"],
                    tool_groups=["repo_read", "verify"],
                    verify=True,
                ),
                _phase("finalize", "FINALIZE", inputs=["verification_results"], outputs=["final_report"]),
            ],
            "budgets": {"max_model_calls": 8, "max_tool_calls": 12},
            "artifact_contracts": [
                {"id": "repo_inventory"},
                {"id": "operation_design"},
                {"id": "change_summary"},
                {"id": "verification_results"},
                {"id": "final_report"},
            ],
        }
    )


def test_phase_plan_is_constructed_without_worker_types() -> None:
    plan = _valid_plan()

    assert plan.phases[0].phase == "DISCOVER"
    assert not hasattr(plan.phases[0], "worker_type")
    assert AppV2Validator().validate_phase_plan(plan, envelope=_envelope()) == []


def test_phase_step_rejects_worker_type_leak() -> None:
    with pytest.raises(Exception):
        PhaseStep.model_validate({**_phase("discover", "DISCOVER"), "worker_type": "repo_worker"})


def test_validator_catches_missing_artifact_producer() -> None:
    plan = _valid_plan().model_copy(
        update={
            "phases": [
                _valid_plan().phases[0],
                _valid_plan().phases[1].model_copy(update={"input_artifacts": ["missing_artifact"]}),
            ]
        }
    )

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert any(issue.code == "missing_artifact_producer" for issue in issues)


def test_validator_allows_runtime_scope_input_when_envelope_is_available() -> None:
    plan = _valid_plan().model_copy(
        update={
            "phases": [
                _valid_plan().phases[0].model_copy(update={"input_artifacts": ["request_envelope"]}),
                *_valid_plan().phases[1:],
            ]
        }
    )

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert not any(issue.code == "missing_artifact_producer" for issue in issues)


def test_validator_allows_declared_runtime_scope_input_contract() -> None:
    payload = _valid_plan().model_dump(mode="json")
    payload["phases"][0]["input_artifacts"] = ["repo_scope_input"]
    payload["artifact_contracts"] = [{"id": "repo_scope_input", "kind": "input"}, *payload["artifact_contracts"]]
    plan = PhasePlan.model_validate(payload)

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert not any(issue.code == "missing_artifact_producer" for issue in issues)


def test_validator_requires_runtime_scope_input_to_have_runtime_scope() -> None:
    plan = _valid_plan().model_copy(
        update={
            "phases": [
                _valid_plan().phases[0].model_copy(update={"input_artifacts": ["request_envelope"]}),
                *_valid_plan().phases[1:],
            ]
        }
    )

    issues = AppV2Validator().validate_phase_plan(plan, envelope=None)

    assert any(issue.code == "missing_artifact_producer" for issue in issues)


def test_validator_accepts_request_envelope_as_runtime_scope_input() -> None:
    plan = _valid_plan().model_copy(
        update={
            "phases": [
                _valid_plan().phases[0].model_copy(update={"input_artifacts": ["request_envelope"]}),
                *_valid_plan().phases[1:],
            ]
        }
    )

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert issues == []


def test_validator_rejects_request_envelope_without_runtime_scope() -> None:
    plan = _valid_plan().model_copy(
        update={
            "phases": [
                _valid_plan().phases[0].model_copy(update={"input_artifacts": ["request_envelope"]}),
                *_valid_plan().phases[1:],
            ]
        }
    )

    issues = AppV2Validator().validate_phase_plan(plan, envelope=None)

    assert any(issue.code == "missing_artifact_producer" for issue in issues)


def test_validator_blocks_runtime_scope_input_with_producer_phase() -> None:
    payload = _valid_plan().model_dump(mode="json")
    payload["artifact_contracts"] = [
        {"id": "repo_scope_input", "kind": "input", "produced_by_phase": "discover"},
        *payload["artifact_contracts"],
    ]
    plan = PhasePlan.model_validate(payload)

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert any(issue.code == "runtime_scope_input_has_producer" for issue in issues)


def test_validator_catches_mutation_without_policy() -> None:
    phase = PhaseStep.model_validate(
        _phase("mutate", "MUTATE", tool_groups=["repo_read", "file_write"], outputs=["change_summary"])
    )
    plan = _valid_plan().model_copy(update={"phases": [phase]})

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert any(issue.code == "mutation_policy_required" for issue in issues)


def test_validator_catches_verify_without_policy() -> None:
    phase = PhaseStep.model_validate(_phase("verify", "VERIFY", tool_groups=["verify"], outputs=["verification_results"]))
    plan = _valid_plan().model_copy(update={"phases": [phase]})

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert any(issue.code == "verification_policy_required" for issue in issues)


def test_validator_blocks_phase_model_budget_above_three() -> None:
    phase = PhaseStep.model_validate(
        _phase("discover", "DISCOVER", outputs=["repo_inventory"], tool_groups=["repo_read"]) | {"max_model_calls": 4}
    )
    plan = _valid_plan().model_copy(update={"phases": [phase]})

    issues = AppV2Validator().validate_phase_plan(plan, envelope=_envelope())

    assert any(issue.code == "phase_model_budget_exceeds_cap" for issue in issues)


def test_worker_decision_requires_exactly_one_branch() -> None:
    with pytest.raises(Exception):
        WorkerDecision.model_validate({})
    with pytest.raises(Exception):
        WorkerDecision.model_validate(
            {
                "tool_calls": [{"call_id": "call_1", "tool_name": "repo_snapshot"}],
                "final_phase_output": {"summary": "done"},
            }
        )


def test_phase_output_must_include_expected_artifacts() -> None:
    output = PhaseOutputProposal(
        status="completed",
        summary="done",
        artifacts=[ArtifactRecord(id="other", kind="phase_output", content="x", producer="test")],
    )
    issues = AppV2Validator().validate_phase_output(output, phase=_valid_plan().phases[-1])

    assert any(issue.code == "missing_phase_output_artifact" for issue in issues)


def test_raise_if_blocking_raises_structured_error() -> None:
    validator = AppV2Validator()
    issues = validator.validate_phase_plan(_valid_plan().model_copy(update={"request_id": "wrong"}), envelope=_envelope())

    with pytest.raises(AppV2ValidationError):
        validator.raise_if_blocking(issues)
