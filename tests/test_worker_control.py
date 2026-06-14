from app.schemas import ArtifactPayload, PlanStep, Result, Task, WorkerIssue
from app.worker_kernel.control import LoopDecision, WorkerLoopController
from app.worker_kernel.memory import WorkerMemoryController
from app.worker_kernel.runtime import WorkerKernelRuntime


def test_controller_retries_runtime_owned_needs_replan_without_planner_replan() -> None:
    controller = WorkerLoopController()
    step = PlanStep(
        step_id="discover",
        worker_type="repo_worker",
        instruction="discover repo",
        output_artifacts=["repo_inventory"],
        max_tool_calls=1,
        max_model_calls=1,
    )
    result = Result(
        run_id="run_plan",
        producer="repo_worker",
        status="needs_replan",
        summary="Cannot finish discovery because remaining_tool_calls is 0.",
        usage={"tool_calls": 1, "model_calls": 1},
    )
    issues = [
        WorkerIssue(
            issue_type="plan_failure",
            code="insufficient_tool_budget",
            message="remaining_tool_calls is 0",
            retryable=False,
        )
    ]

    retry_decision = controller.decide_after_attempt(
        result=result,
        issues=issues,
        step=step,
        retry_available=True,
    )
    exhausted_decision = controller.decide_after_attempt(
        result=result,
        issues=issues,
        step=step,
        retry_available=False,
    )

    assert retry_decision.action == "retry_step"
    assert retry_decision.ownership == "instance"
    assert retry_decision.reason_code == "insufficient_tool_budget"
    assert exhausted_decision.action == "fail"
    assert exhausted_decision.terminal_status == "failed"


def test_controller_retry_instruction_targets_missing_required_write_paths() -> None:
    controller = WorkerLoopController()
    step = PlanStep(
        step_id="mutate",
        worker_type="filesystem_worker",
        phase="MUTATE",
        mode="bounded_mutation",
        instruction="organize files and write manifest",
        output_artifacts=["change_summary"],
        max_tool_calls=2,
        max_model_calls=2,
    )
    result = Result(
        run_id="run_plan",
        producer="filesystem_worker",
        status="failed",
        summary="bounded mutation returned completed without touching required write paths",
    )
    issues = [
        WorkerIssue(
            issue_type="instance_failure",
            code="mutation_completed_missing_required_writes",
            message="missing docs/workspace_manifest.json",
            retryable=True,
            metadata={"missing_required_write_paths": ["./docs/workspace_manifest.json"]},
        )
    ]

    decision = controller.decide_after_attempt(
        result=result,
        issues=issues,
        step=step,
        retry_available=True,
    )
    retry_task, _ = controller.build_retry_task(
        task=Task(
            task_id="task_mutate",
            run_id="run_plan",
            step_id="mutate",
            worker_type="filesystem_worker",
            instruction="organize files and write manifest",
            expected_outputs=["change_summary"],
        ),
        result=result,
        issues=issues,
        decision=decision,
    )

    assert decision.action == "retry_step"
    assert decision.retry_instruction is not None
    assert decision.retry_instruction.metadata["missing_required_write_paths"] == [
        "docs/workspace_manifest.json"
    ]
    retry_instruction = retry_task.metadata["runtime_retry_instruction"]
    assert "docs/workspace_manifest.json" in retry_instruction
    assert "Do not" in retry_instruction


def test_worker_memory_exposes_pending_required_write_paths() -> None:
    memory = WorkerMemoryController()
    step = PlanStep(
        step_id="mutate",
        worker_type="filesystem_worker",
        phase="MUTATE",
        mode="bounded_mutation",
        instruction="organize files and write manifest",
        output_artifacts=["change_summary"],
    )
    issue = WorkerIssue(
        issue_type="instance_failure",
        code="mutation_completed_missing_required_writes",
        message="missing manifest",
        retryable=True,
        metadata={"missing_required_write_paths": ["docs/workspace_manifest.json"]},
    )

    memory.record_attempt(
        step=step,
        attempt_id="attempt_1",
        result=Result(
            run_id="run_plan",
            producer="filesystem_worker",
            status="failed",
            summary="missing manifest",
        ),
        issues=[issue],
    )

    snapshot = memory.memory_for_step("mutate")
    assert snapshot is not None
    assert snapshot["pending_required_write_paths"] == ["docs/workspace_manifest.json"]
    assert "docs/workspace_manifest.json" in snapshot["retry_guidance"][-1]


def test_controller_marks_failed_verify_with_command_evidence_after_mutation() -> None:
    controller = WorkerLoopController()
    step = PlanStep(
        step_id="verify",
        worker_type="verify_worker",
        phase="VERIFY",
        instruction="run tests",
        output_artifacts=["test_results"],
        max_tool_calls=1,
        max_model_calls=1,
    )
    result = Result(
        run_id="run_plan",
        producer="verify_worker",
        status="failed",
        summary="pytest failed",
        artifacts=[
            ArtifactPayload(
                id="verification_tool",
                kind="tool_observation",
                content={
                    "tool_name": "run_project_tests",
                    "observation": {"command": ["pytest", "-q"], "returncode": 1},
                },
                metadata={"tool_name": "run_project_tests"},
            )
        ],
    )

    decision = controller.decide_after_attempt(
        result=result,
        issues=[],
        step=step,
        retry_available=False,
        mutation_already_completed=True,
    )

    assert decision.action == "fail"
    assert decision.terminal_status == "completed_with_failed_verification"


def test_controller_retries_verify_when_only_plan_commands_are_present() -> None:
    controller = WorkerLoopController()
    step = PlanStep(
        step_id="verify",
        worker_type="verify_worker",
        phase="VERIFY",
        instruction="run verification",
        output_artifacts=["verification_results", "test_results"],
        max_tool_calls=1,
        max_model_calls=1,
    )
    result = Result(
        run_id="run_plan",
        producer="verify_worker",
        status="failed",
        summary="worker model returned neither tool_calls nor final_result",
        artifacts=[
            ArtifactPayload(
                id="verification_plan",
                kind="worker_output",
                content={
                    "checks": ["run tests"],
                    "commands": ["uv run pytest"],
                    "expected_outcome": "tests pass",
                },
            )
        ],
    )
    issues = [
        WorkerIssue(
            issue_type="instance_failure",
            code="empty_worker_decision",
            message="worker model returned neither tool_calls nor final_result",
            retryable=True,
        )
    ]

    decision = controller.decide_after_attempt(
        result=result,
        issues=issues,
        step=step,
        retry_available=True,
        mutation_already_completed=True,
    )
    retry_task, _ = controller.build_retry_task(
        task=Task(
            task_id="task_verify",
            run_id="run_plan",
            step_id="verify",
            worker_type="verify_worker",
            instruction="run verification",
            expected_outputs=["verification_results", "test_results"],
            max_tool_calls=1,
            max_model_calls=1,
            metadata={"phase": "VERIFY"},
        ),
        result=result,
        issues=issues,
        decision=decision,
    )

    assert not controller.has_verification_command_evidence(result)
    assert decision.action == "retry_step"
    assert decision.ownership == "verification"
    assert decision.reason_code == "verification_failed_before_command"
    assert retry_task.metadata["force_verification_command_first"] is True


def test_controller_distinguishes_passing_and_failing_verification_commands() -> None:
    controller = WorkerLoopController()
    passing = Result(
        run_id="run_plan",
        producer="verify_worker",
        status="failed",
        summary="verification artifact contract invalid",
        artifacts=[
            ArtifactPayload(
                id="verify_tool",
                kind="tool_observation",
                content={
                    "tool_name": "run_project_tests",
                    "observation": {"command": ["pytest", "-q"], "returncode": 0},
                },
                metadata={"tool_name": "run_project_tests"},
            )
        ],
    )
    failing = passing.model_copy(
        update={
            "artifacts": [
                ArtifactPayload(
                    id="verify_tool",
                    kind="tool_observation",
                    content={
                        "tool_name": "run_project_tests",
                        "observation": {"command": ["pytest", "-q"], "returncode": 1},
                    },
                    metadata={"tool_name": "run_project_tests"},
                )
            ]
        }
    )

    assert controller.has_verification_command_evidence(passing)
    assert not controller.has_failed_verification_command_evidence(passing)
    assert controller.has_failed_verification_command_evidence(failing)


def test_runtime_does_not_mutation_repair_passing_verify_artifact_contract_failure() -> None:
    runtime = WorkerKernelRuntime()
    step = PlanStep(
        step_id="verify",
        worker_type="verify_worker",
        phase="VERIFY",
        instruction="run tests",
        output_artifacts=["verification_results", "test_results", "manifest_validation"],
    )
    result = Result(
        run_id="run_plan",
        producer="verify_worker",
        status="failed",
        summary="worker group produced invalid expected artifacts: manifest_validation",
        artifacts=[
            ArtifactPayload(
                id="verify_tool",
                kind="tool_observation",
                content={
                    "tool_name": "run_project_tests",
                    "observation": {"command": ["pytest", "-q"], "returncode": 0},
                },
                metadata={"tool_name": "run_project_tests"},
            )
        ],
        metadata={"issue_code": "worker_artifact_contract_invalid"},
    )
    decision = LoopDecision(
        action="retry_step",
        ownership="instance",
        status="failed",
        reason_code="worker_artifact_contract_invalid",
        summary=result.summary,
        retryable=True,
    )

    assert not runtime._verification_feedback_is_implementation_repair(
        verify_step=step,
        verify_result=result,
        decision=decision,
    )
