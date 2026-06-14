from pathlib import Path

from appV2.schemas import (
    ArtifactRecord,
    MutationPolicy,
    PhaseOutputProposal,
    PhaseStep,
    ToolCallProposal,
    VerificationPolicy,
)
from appV2.worker.context import ContextController
from appV2.worker.ledgers import ArtifactLedger, MutationLedger
from appV2.worker.policy_gate import PolicyGate
from appV2.worker.tools import ToolRegistry
from appV2.worker.verification_gate import VerificationGate
from tests.test_appv2_phase_planner import _envelope, _plan
from appV2.schemas import PhasePlan


def test_policy_gate_rejects_tool_group_not_allowed(tmp_path: Path) -> None:
    phase = PhaseStep(
        phase_id="discover",
        phase="DISCOVER",
        goal="inspect",
        allowed_tool_groups=["repo_read"],
    )
    decision = PolicyGate(root_path=tmp_path).validate_tool_call(
        phase=phase,
        proposal=ToolCallProposal(call_id="c1", tool_name="write_file", arguments={"path": "x"}, purpose="write"),
    )

    assert decision.allowed is False
    assert decision.code == "tool_group_not_allowed"
    assert decision.repairable is True


def test_policy_gate_rejects_forbidden_path(tmp_path: Path) -> None:
    phase = PhaseStep(
        phase_id="mutate",
        phase="MUTATE",
        goal="mutate",
        allowed_tool_groups=["file_write"],
        mutation_policy=MutationPolicy(mode="advisory", forbidden_paths=["secrets.env"]),
    )
    result = ToolRegistry(root_path=tmp_path).execute(
        phase=phase,
        tool_name="write_file",
        arguments={"path": "secrets.env", "content": "x"},
    )

    assert result["status"] == "denied"
    assert result["code"] == "forbidden_path"


def test_verification_gate_requires_runtime_evidence_for_pass() -> None:
    phase = PhaseStep(
        phase_id="verify",
        phase="VERIFY",
        goal="verify",
        output_artifacts=["verification_results"],
        allowed_tool_groups=["verify"],
        verification_policy=VerificationPolicy(required=True, require_evidence=True),
    )
    output = PhaseOutputProposal(
        status="completed",
        summary="passed",
        artifacts=[
            ArtifactRecord(
                id="verification_results",
                kind="phase_output",
                content={"status": "passed"},
                producer="model",
                phase_id="verify",
            )
        ],
    )

    issues = VerificationGate().validate_phase_output(phase=phase, output=output, evidence=[])

    assert any(issue.code == "verification_missing_runtime_evidence" for issue in issues)


def test_context_frame_is_compact(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(_plan())
    ledger = ArtifactLedger(
        [
            ArtifactRecord(
                id="repo_inventory",
                kind="phase_output",
                content={"files": ["a.py"]},
                producer="test",
                phase_id="discover",
            )
        ]
    )
    frame = ContextController().build_phase_frame(
        envelope=_envelope(),
        plan=plan,
        phase=plan.phases[1],
        artifacts=ledger,
        mutations=MutationLedger(),
        tools=ToolRegistry(root_path=tmp_path),
    )

    assert frame.pending_outputs == ["operation_design"]
    assert "recent" in frame.artifact_ledger
    assert frame.phase["phase"] == "DESIGN"
    assert frame.resolved_inputs[0]["id"] == "repo_inventory"
    assert frame.resolved_inputs[0]["content"]["files"] == ["a.py"]
    assert frame.input_artifact_contracts[0]["id"] == "repo_inventory"
    assert frame.output_artifact_contracts[0]["id"] == "operation_design"


def test_context_frame_exposes_runtime_scope_inputs(tmp_path: Path) -> None:
    plan = PhasePlan.model_validate(
        {
            **_plan(),
            "phases": [
                {
                    "phase_id": "discover",
                    "phase": "DISCOVER",
                    "goal": "Inspect repository root.",
                    "instructions": ["Use runtime scope."],
                    "input_artifacts": ["runtime_scope_repository_root", "request_envelope"],
                    "output_artifacts": ["repo_inventory"],
                    "allowed_tool_groups": ["repo_read"],
                }
            ],
            "artifact_contracts": [
                {
                    "id": "runtime_scope_repository_root",
                    "kind": "input",
                    "content_schema": {
                        "type": "object",
                        "properties": {"repository_root": {"const": "live_appv2_probe_repo"}},
                    },
                },
                {"id": "repo_inventory"},
            ],
        }
    )

    frame = ContextController().build_phase_frame(
        envelope=_envelope(),
        plan=plan,
        phase=plan.phases[0],
        artifacts=ArtifactLedger(),
        mutations=MutationLedger(),
        tools=ToolRegistry(root_path=tmp_path),
    )

    by_id = {record["id"]: record for record in frame.resolved_inputs}
    assert by_id["runtime_scope_repository_root"]["content"]["repository_root"] == "live_appv2_probe_repo"
    assert by_id["request_envelope"]["content"]["normalized_input"] == _envelope().normalized_input


def test_available_tool_specs_include_examples_and_rules(tmp_path: Path) -> None:
    phase = PhaseStep(
        phase_id="mutate",
        phase="MUTATE",
        goal="Mutate one file safely.",
        allowed_tool_groups=["repo_read", "file_write"],
        mutation_policy=MutationPolicy(mode="advisory"),
    )

    tools = ToolRegistry(root_path=tmp_path).available_tools(phase)
    specs_by_name = {tool["name"]: tool for tool in tools}

    read_file = specs_by_name["read_file"]
    assert read_file["required_arguments"] == ["path"]
    assert read_file["example_call"]["path"] == "README.md"
    assert any("repo-relative" in rule for rule in read_file["argument_rules"])

    write_file = specs_by_name["write_file"]
    assert "content" in write_file["required_arguments"]
    assert write_file["example_call"]["overwrite"] is True
