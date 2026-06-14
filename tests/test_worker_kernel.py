import pytest

from app.repair_policy import WORKER_STAGE_REPAIR_ATTEMPTS
from app.schemas import Envelope, Plan, PlanStep, ReplanRequest, Result, Task
from app.worker_kernel.agentic import AgenticWorkerGroupRunner, WorkerInstanceTemplate, WorkerLLMController
from app.worker_kernel.group import SequentialWorkerGroupRunner
from app.worker_kernel.execution_plan import retry_envelope_call_budget
from app.worker_kernel.registry import WorkerRegistry, build_default_registry
from app.worker_kernel.runtime import WorkerKernelRuntime
from app.worker_kernel.tools import WorkerToolConfig, WorkerToolbox


class QueueClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict) -> str:
        import json

        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0))


def _envelope() -> Envelope:
    return Envelope(
        request_id="req_replan",
        raw_input="research and fix code",
        normalized_input="Research the issue and apply a scoped code fix.",
        user_goal="Fix the code after evidence-based research.",
        input_type="research_backed_code_fix",
        intents=["research.lookup", "code.fix"],
        domains=["code", "research"],
        risks=["mutation_requested", "needs_verification"],
        artifacts=[{"name": "target", "type": "code"}],
        context_needed=["target_file"],
        constraints=["mutation_requires_verification"],
        complexity_hint="high",
        confidence=0.8,
    )


def _permissions(
    *,
    read_files: bool = False,
    write_files: bool = False,
    run_commands: bool = False,
    web_research: bool = False,
    **extra,
) -> dict:
    permissions = {
        "read_files": read_files,
        "write_files": write_files,
        "run_commands": run_commands,
        "web_research": web_research,
    }
    permissions.update(extra)
    return permissions


def test_worker_kernel_direct_plan_executes() -> None:
    plan = Plan(
        plan_id="plan_req_direct",
        request_id="req_direct",
        planner="direct",
        objective="Answer a question",
        strategy="direct_answer",
        steps=[
            PlanStep(
                step_id="step-direct",
                worker_type="direct_worker",
                instruction="Answer directly",
                output_artifacts=["direct_answer"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions={"read_files": False, "write_files": False, "run_commands": False},
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime().run(plan)

    assert result.status == "completed"
    assert result.errors == []
    assert any((a.get("id") or a.get("artifact_id")) == "direct_answer" for a in result.artifacts)
    assert result.usage.get("model_calls", 0) >= 0
    assert result.metadata["runtime_matrix"]["row_count"] >= 1
    assert any(
        row["event"] == "run_completed" and row["component"] == "worker_kernel_runtime"
        for row in result.metadata["runtime_matrix"]["rows"]
    )


def test_worker_kernel_passes_structured_mutation_scope_to_mutate_step() -> None:
    class CodeWorker:
        worker_type = "code_worker"

        def run(self, task: Task) -> Result:
            if task.step_id == "design_step":
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="designed scoped mutation",
                    artifacts=[
                        {
                            "id": "mutation_scope",
                            "content": {
                                "target_paths": ["src/fulfillment/events.py"],
                                "test_paths": [],
                                "forbidden_paths": [],
                                "reason": "only webhook event processing needs mutation",
                                "max_files": 1,
                            },
                        },
                        {"id": "rollback_plan", "content": "revert one file"},
                        {"id": "fix_design", "content": "add early duplicate event guard"},
                    ],
                )
            assert task.step_id == "mutate_step"
            assert task.permissions.write_paths == ["src/fulfillment/events.py"]
            assert task.permissions.write_paths_from_artifacts == []
            assert task.metadata["write_scope"]["target_paths"] == ["src/fulfillment/events.py"]
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="patch applied",
                artifacts=[
                    {"id": "change_summary", "content": "added idempotency guard"},
                    {"id": "rollback_patch", "content": "remove guard"},
                    {"id": "patch_diff", "content": "--- a/src/fulfillment/events.py"},
                ],
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())
    plan = Plan(
        plan_id="plan_mutation_scope",
        request_id="req_mutation_scope",
        planner="test",
        objective="fix webhook duplicate handling",
        strategy="design then mutate",
        steps=[
            PlanStep(
                step_id="design_step",
                worker_type="code_worker",
                phase="DESIGN",
                mode="plan_only",
                instruction="design scoped fix",
                output_artifacts=["mutation_scope", "rollback_plan", "fix_design"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            ),
            PlanStep(
                step_id="mutate_step",
                worker_type="code_worker",
                phase="MUTATE",
                mode="bounded_mutation",
                instruction="apply scoped fix",
                input_artifacts=["mutation_scope", "rollback_plan", "fix_design"],
                output_artifacts=["change_summary", "rollback_patch"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(
                    read_files=True,
                    write_files=True,
                    write_paths_from_artifacts=["mutation_scope"],
                ),
            ),
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 2, "max_workers": 2, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert {artifact.id for artifact in result.artifacts} >= {"change_summary", "rollback_patch", "patch_diff"}


def test_worker_kernel_dispatches_bounded_mutation_with_empty_advisory_scope() -> None:
    class CodeWorker:
        worker_type = "code_worker"
        mutation_policy: dict[str, object] = {}

        def run(self, task: Task) -> Result:
            if task.step_id == "design_step":
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="designed empty mutation",
                    artifacts=[{"id": "mutation_scope", "content": {"target_paths": [], "reason": "bad"}}],
                )
            type(self).mutation_policy = task.metadata["write_policy"]
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="mutated after advisory warning",
                artifacts=[
                    {"id": "change_summary", "content": "changed"},
                    {"id": "rollback_patch", "content": "rollback"},
                ],
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())
    plan = _mutation_scope_block_plan()

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert CodeWorker.mutation_policy["mode"] == "advisory"
    assert CodeWorker.mutation_policy["validation_warnings"]
    assert result.metadata["worker_results"][0]["summary"] == "designed empty mutation"


def test_worker_kernel_dispatches_bounded_mutation_with_escaping_advisory_scope() -> None:
    class CodeWorker:
        worker_type = "code_worker"
        mutation_policy: dict[str, object] = {}

        def run(self, task: Task) -> Result:
            if task.step_id == "design_step":
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="designed unsafe mutation",
                    artifacts=[{"id": "mutation_scope", "content": {"target_paths": ["../secret.py"], "reason": "bad"}}],
                )
            type(self).mutation_policy = task.metadata["write_policy"]
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="mutated after advisory warning",
                artifacts=[
                    {"id": "change_summary", "content": "changed"},
                    {"id": "rollback_patch", "content": "rollback"},
                ],
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())

    result = WorkerKernelRuntime(registry=registry).run(_mutation_scope_block_plan())

    assert result.status == "completed"
    assert CodeWorker.mutation_policy["mode"] == "advisory"
    assert "invalid repo-relative path" in CodeWorker.mutation_policy["validation_warnings"][0]["message"]


def test_worker_kernel_dispatches_bounded_mutation_with_oversized_advisory_scope() -> None:
    class CodeWorker:
        worker_type = "code_worker"
        mutation_policy: dict[str, object] = {}

        def run(self, task: Task) -> Result:
            if task.step_id == "design_step":
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="designed broad mutation",
                    artifacts=[
                        {
                            "id": "mutation_scope",
                            "content": {
                                "target_paths": ["src/a.py", "src/b.py", "src/c.py"],
                                "reason": "too broad",
                                "max_files": 2,
                            },
                        }
                    ],
                )
            type(self).mutation_policy = task.metadata["write_policy"]
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="mutated after advisory warning",
                artifacts=[
                    {"id": "change_summary", "content": "changed"},
                    {"id": "rollback_patch", "content": "rollback"},
                ],
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())

    result = WorkerKernelRuntime(registry=registry).run(_mutation_scope_block_plan())

    assert result.status == "completed"
    assert CodeWorker.mutation_policy["mode"] == "strict"
    assert CodeWorker.mutation_policy["strict_allowed_paths"] == ["src/a.py", "src/b.py", "src/c.py"]
    assert CodeWorker.mutation_policy["advisory_paths"] == ["src/a.py", "src/b.py", "src/c.py"]
    assert "exceeding max_files" in CodeWorker.mutation_policy["validation_warnings"][0]["message"]


def test_worker_kernel_marks_failed_verify_after_mutation_as_completed_with_failed_verification() -> None:
    class CodeWorker:
        worker_type = "code_worker"

        def run(self, task: Task) -> Result:
            if task.step_id == "design_step":
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="designed scoped mutation",
                    artifacts=[
                        {
                            "id": "mutation_scope",
                            "content": {"target_paths": ["src/events.py"], "reason": "one file", "max_files": 1},
                        },
                        {"id": "rollback_plan", "content": "revert"},
                        {"id": "fix_design", "content": "fix"},
                    ],
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="patched",
                artifacts=[
                    {"id": "change_summary", "content": "changed"},
                    {"id": "rollback_patch", "content": "rollback"},
                ],
            )

    class VerifyWorker:
        worker_type = "verify_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="failed",
                summary="pytest failed",
                artifacts=[
                    {
                        "id": "verify_step_verification_runner_tool_1",
                        "kind": "tool_observation",
                        "content": {
                            "tool_name": "run_project_tests",
                            "observation": {
                                "command": ["uv", "run", "--extra", "dev", "pytest", "-q"],
                                "returncode": 1,
                                "stdout": "",
                                "stderr": "failed",
                            },
                        },
                        "metadata": {"tool_name": "run_project_tests"},
                    },
                    {"id": "test_results", "content": {"returncode": 1}},
                ],
                errors=["pytest failed"],
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())
    registry.register(VerifyWorker())

    result = WorkerKernelRuntime(registry=registry).run(_mutation_with_verify_plan())

    assert result.status == "completed_with_failed_verification"
    assert result.errors == ["pytest failed"]


def test_worker_kernel_repairs_mutation_once_from_verification_feedback() -> None:
    class CodeWorker:
        worker_type = "code_worker"
        calls = 0
        saw_feedback = False

        def run(self, task: Task) -> Result:
            type(self).calls += 1
            if "verification_feedback" in task.metadata:
                type(self).saw_feedback = True
                assert task.metadata["verification_feedback"]["verify_step_id"] == "verify"
                assert task.metadata["runtime_retry_reason_code"] == "verification_feedback_repair"
                summary = "repair applied from verification feedback"
            else:
                summary = "initial mutation applied"
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary=summary,
                artifacts=[
                    {"id": "change_summary", "content": {"summary": summary}},
                    {"id": "rollback_patch", "content": {"diff": "rollback"}},
                ],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    class VerifyWorker:
        worker_type = "verify_worker"
        calls = 0

        def run(self, task: Task) -> Result:
            type(self).calls += 1
            if type(self).calls == 1:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="failed",
                    summary="pytest failed after mutation",
                    artifacts=[
                        {
                            "id": "verify_tool",
                            "kind": "tool_observation",
                            "content": {
                                "tool_name": "run_project_tests",
                                "observation": {"command": ["pytest", "-q"], "returncode": 1},
                            },
                            "metadata": {"tool_name": "run_project_tests"},
                        }
                    ],
                    usage={"tool_calls": 1, "model_calls": 1},
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="verification passed after targeted repair",
                artifacts=[
                    {
                        "id": "verification_results",
                        "content": {"status": "passed", "commands": [{"returncode": 0}]},
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 1},
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())
    registry.register(VerifyWorker())
    plan = Plan(
        plan_id="plan_verify_feedback_repair",
        request_id="req_verify_feedback_repair",
        planner="test",
        objective="mutate and verify",
        strategy="repair from failed verification once",
        steps=[
            PlanStep(
                step_id="mutate",
                worker_type="code_worker",
                phase="MUTATE",
                mode="bounded_mutation",
                instruction="apply scoped mutation",
                output_artifacts=["change_summary", "rollback_patch"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            ),
            PlanStep(
                step_id="verify",
                worker_type="verify_worker",
                phase="VERIFY",
                mode="verify_only",
                instruction="run verification",
                input_artifacts=["change_summary", "rollback_patch"],
                output_artifacts=["verification_results"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(run_commands=True),
            ),
        ],
        budget={"max_tool_calls": 6, "max_model_calls": 8, "max_workers": 2, "max_retries": 1},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert CodeWorker.calls == 2
    assert CodeWorker.saw_feedback is True
    assert VerifyWorker.calls == 2
    assert result.metadata["retry_count"] == 1
    assert any(artifact.id == "verification_results" for artifact in result.artifacts)
    events = [row["event"] for row in result.metadata["runtime_matrix"]["rows"]]
    assert "verification_feedback_repair_started" in events
    assert "verification_feedback_repair_completed" in events


def test_worker_kernel_allows_configured_verification_feedback_repairs() -> None:
    class CodeWorker:
        worker_type = "code_worker"
        repair_calls = 0

        def run(self, task: Task) -> Result:
            if "verification_feedback" not in task.metadata:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="initial mutation applied",
                    artifacts=[
                        {"id": "change_summary", "content": {"summary": "initial"}},
                        {"id": "rollback_patch", "content": {"diff": "rollback"}},
                    ],
                    usage={"tool_calls": 0, "model_calls": 1},
                )
            type(self).repair_calls += 1
            if type(self).repair_calls < WORKER_STAGE_REPAIR_ATTEMPTS:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="failed",
                    summary="repair instance missed output contract",
                    usage={"tool_calls": 0, "model_calls": 1},
                    metadata={
                        "issues": [
                            {
                                "issue_type": "instance_failure",
                                "code": "worker_output_contract_miss",
                                "message": "missing expected repair artifacts",
                                "retryable": True,
                            }
                        ]
                    },
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="third repair fixed verification failure",
                artifacts=[
                    {"id": "change_summary", "content": {"summary": "repair"}},
                    {"id": "rollback_patch", "content": {"diff": "rollback"}},
                ],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    class VerifyWorker:
        worker_type = "verify_worker"
        calls = 0

        def run(self, task: Task) -> Result:
            type(self).calls += 1
            if type(self).calls == 1:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="failed",
                    summary="pytest failed after mutation",
                    artifacts=[
                        {
                            "id": "verify_tool",
                            "kind": "tool_observation",
                            "content": {
                                "tool_name": "run_project_tests",
                                "observation": {"command": ["pytest", "-q"], "returncode": 1},
                            },
                            "metadata": {"tool_name": "run_project_tests"},
                        }
                    ],
                    usage={"tool_calls": 1, "model_calls": 1},
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="verification passed after repair",
                artifacts=[{"id": "verification_results", "content": {"status": "passed"}}],
                usage={"tool_calls": 1, "model_calls": 1},
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())
    registry.register(VerifyWorker())
    plan = Plan(
        plan_id="plan_verify_feedback_three_repairs",
        request_id="req_verify_feedback_three_repairs",
        planner="test",
        objective="mutate and verify",
        strategy="repair from failed verification until fixed",
        steps=[
            PlanStep(
                step_id="mutate",
                worker_type="code_worker",
                phase="MUTATE",
                mode="bounded_mutation",
                instruction="apply scoped mutation",
                output_artifacts=["change_summary", "rollback_patch"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            ),
            PlanStep(
                step_id="verify",
                worker_type="verify_worker",
                phase="VERIFY",
                mode="verify_only",
                instruction="run verification",
                input_artifacts=["change_summary", "rollback_patch"],
                output_artifacts=["verification_results"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(run_commands=True),
            ),
        ],
        budget={"max_tool_calls": 8, "max_model_calls": 8, "max_workers": 2, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert CodeWorker.repair_calls == WORKER_STAGE_REPAIR_ATTEMPTS
    assert VerifyWorker.calls == 2
    assert result.metadata["retry_count"] == WORKER_STAGE_REPAIR_ATTEMPTS
    assert result.metadata["instance_attempts_used"] == 3 + WORKER_STAGE_REPAIR_ATTEMPTS
    events = [row["event"] for row in result.metadata["runtime_matrix"]["rows"]]
    assert events.count("verification_feedback_repair_started") == WORKER_STAGE_REPAIR_ATTEMPTS
    assert events.count("verification_feedback_repair_failed") == WORKER_STAGE_REPAIR_ATTEMPTS - 1
    assert events.count("verification_feedback_repair_completed") == 1


def test_worker_kernel_reconciles_completed_verify_artifact_failure() -> None:
    class CodeWorker:
        worker_type = "code_worker"

        def run(self, task: Task) -> Result:
            if task.step_id == "design_step":
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="designed scoped mutation",
                    artifacts=[
                        {
                            "id": "mutation_scope",
                            "content": {"target_paths": ["src/events.py"], "reason": "one file", "max_files": 1},
                        },
                        {"id": "rollback_plan", "content": "revert"},
                        {"id": "fix_design", "content": "fix"},
                    ],
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="patched",
                artifacts=[
                    {"id": "change_summary", "content": "changed"},
                    {"id": "rollback_patch", "content": "rollback"},
                ],
            )

    class VerifyWorker:
        worker_type = "verify_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="verification artifact reports failure",
                artifacts=[
                    {
                        "id": "verification_results",
                        "content": {"status": "failed", "scope_audit": {"passed": False}},
                    },
                    {"id": "test_results", "content": {"status": "passed", "failed_commands": []}},
                ],
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())
    registry.register(VerifyWorker())

    result = WorkerKernelRuntime(registry=registry).run(_mutation_with_verify_plan())

    assert result.status == "completed_with_failed_verification"
    assert "verification artifact reports failure" in result.summary


def test_worker_kernel_retries_verify_failure_before_command_evidence() -> None:
    class CodeWorker:
        worker_type = "code_worker"

        def run(self, task: Task) -> Result:
            if task.step_id == "design_step":
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="completed",
                    summary="designed scoped mutation",
                    artifacts=[
                        {
                            "id": "mutation_scope",
                            "content": {"target_paths": ["src/events.py"], "reason": "one file", "max_files": 1},
                        },
                        {"id": "rollback_plan", "content": "revert"},
                        {"id": "fix_design", "content": "fix"},
                    ],
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="patched",
                artifacts=[
                    {"id": "change_summary", "content": "changed"},
                    {"id": "rollback_patch", "content": "rollback"},
                ],
            )

    class VerifyWorker:
        worker_type = "verify_worker"
        runs = 0

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            if type(self).runs == 1:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="failed",
                    summary="Verification failed due to instance budget exhaustion before test execution could be completed.",
                    artifacts=[
                        {
                            "id": "test_results",
                            "content": {"status": "failed", "notes": "test execution was not executed"},
                        }
                    ],
                    metadata={
                        "issues": [
                            {
                                "issue_type": "instance_failure",
                                "code": "worker_reported_issue",
                                "message": "instance budget exhaustion before test execution",
                                "retryable": False,
                            }
                        ]
                    },
                )
            assert task.metadata["force_verification_command_first"] is True
            assert task.metadata["verification_retry_reason"] == "verification_failed_before_command"
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="verification passed",
                artifacts=[
                    {
                        "id": "verify_step_verification_runner_tool_1",
                        "kind": "tool_observation",
                        "content": {
                            "tool_name": "run_project_tests",
                            "observation": {
                                "command": ["uv", "run", "--extra", "dev", "pytest", "-q"],
                                "returncode": 0,
                            },
                        },
                        "metadata": {"tool_name": "run_project_tests"},
                    },
                    {"id": "test_results", "content": {"returncode": 0}},
                ],
            )

    registry = WorkerRegistry()
    registry.register(CodeWorker())
    registry.register(VerifyWorker())

    result = WorkerKernelRuntime(registry=registry).run(_mutation_with_verify_plan())

    assert result.status == "completed"
    assert result.metadata["retry_count"] == 1
    assert result.metadata["instance_attempts_used"] == 4
    assert "replan" not in result.metadata
    retry_rows = [
        row for row in result.metadata["runtime_matrix"]["rows"] if row["event"] == "attempt_retry_scheduled"
    ]
    assert retry_rows[-1]["step_id"] == "verify_step"
    assert retry_rows[-1]["details"]["reason"] == "worker_runtime_failure"


def _mutation_scope_block_plan() -> Plan:
    return Plan(
        plan_id="plan_bad_scope",
        request_id="req_bad_scope",
        planner="test",
        objective="fix code",
        strategy="design then mutate",
        steps=[
            PlanStep(
                step_id="design_step",
                worker_type="code_worker",
                phase="DESIGN",
                mode="plan_only",
                instruction="design scoped fix",
                output_artifacts=["mutation_scope"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            ),
            PlanStep(
                step_id="mutate_step",
                worker_type="code_worker",
                phase="MUTATE",
                mode="bounded_mutation",
                instruction="mutate scoped target",
                input_artifacts=["mutation_scope"],
                output_artifacts=["change_summary", "rollback_patch"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(
                    read_files=True,
                    write_files=True,
                    write_paths_from_artifacts=["mutation_scope"],
                ),
            ),
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 2, "max_workers": 2, "max_retries": 0},
    )


def _mutation_with_verify_plan() -> Plan:
    return Plan(
        plan_id="plan_verify_failure",
        request_id="req_verify_failure",
        planner="test",
        objective="fix and verify code",
        strategy="design mutate verify",
        steps=[
            PlanStep(
                step_id="design_step",
                worker_type="code_worker",
                phase="DESIGN",
                mode="plan_only",
                instruction="design scoped fix",
                output_artifacts=["mutation_scope", "rollback_plan", "fix_design"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            ),
            PlanStep(
                step_id="mutate_step",
                worker_type="code_worker",
                phase="MUTATE",
                mode="bounded_mutation",
                instruction="apply scoped fix",
                input_artifacts=["mutation_scope", "rollback_plan", "fix_design"],
                output_artifacts=["change_summary", "rollback_patch"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(
                    read_files=True,
                    write_files=True,
                    write_paths_from_artifacts=["mutation_scope"],
                ),
            ),
            PlanStep(
                step_id="verify_step",
                worker_type="verify_worker",
                phase="VERIFY",
                mode="verify_only",
                instruction="run verification",
                input_artifacts=["change_summary", "rollback_patch", "mutation_scope"],
                output_artifacts=["test_results"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(read_files=True, run_commands=True),
            ),
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 3, "max_workers": 3, "max_retries": 0},
    )


def test_worker_kernel_returns_needs_replan_without_planner_runtime() -> None:
    class ReplanWorker:
        worker_type = "mock_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary="missing evidence for mutation",
                artifacts=[{"id": "partial_evidence", "content": "insufficient"}],
                usage={"tool_calls": 1, "model_calls": 0},
                metadata={"recommended_action": "ask planner for a fresh evidence-first plan"},
            )

    registry = WorkerRegistry()
    registry.register(ReplanWorker())
    plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Research and fix code",
        strategy="research_then_fix",
        steps=[
            PlanStep(
                step_id="research_step",
                worker_type="mock_worker",
                instruction="research evidence",
                output_artifacts=["partial_evidence"],
                max_tool_calls=2,
                max_model_calls=1,
            )
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "needs_replan"
    assert result.metadata["replan_request"]["failed_step_id"] == "research_step"
    assert result.metadata["replan_request"]["completed_step_ids"] == []
    assert result.metadata["replan_request"]["recommended_action"] == "ask planner for a fresh evidence-first plan"


def test_worker_kernel_replan_request_tracks_completed_steps_without_artifacts() -> None:
    class CompletedWorker:
        worker_type = "completed_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="completed without artifacts",
                usage={"tool_calls": 0, "model_calls": 0},
            )

    class ReplanWorker:
        worker_type = "mock_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary="planner scope does not match discovered repo",
                usage={"tool_calls": 1, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(CompletedWorker())
    registry.register(ReplanWorker())
    plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Research and fix code",
        strategy="research_then_fix",
        steps=[
            PlanStep(
                step_id="discover_step",
                worker_type="completed_worker",
                instruction="discover context",
                output_artifacts=[],
                max_tool_calls=0,
                max_model_calls=0,
            ),
            PlanStep(
                step_id="research_step",
                worker_type="mock_worker",
                instruction="research evidence",
                output_artifacts=["partial_evidence"],
                max_tool_calls=2,
                max_model_calls=1,
            ),
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 2, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    replan_request = result.metadata["replan_request"]
    assert result.status == "needs_replan"
    assert replan_request["completed_step_ids"] == ["discover_step"]
    assert replan_request["failed_step_id"] == "research_step"
    assert "research_step" not in replan_request["completed_step_ids"]


def test_worker_kernel_replans_with_fixed_new_plan() -> None:
    class DiscoverWorker:
        worker_type = "repo_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="discovered payout workflow and candidate files",
                artifacts=[
                    {
                        "id": "repo_inventory",
                        "content": {
                            "services": ["orchestrator", "webhook", "ledger"],
                            "candidate_paths": [
                                "app/worker_kernel/runtime.py",
                                "app/worker_kernel/dispatcher.py",
                            ],
                        },
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 0},
            )

    class ResearchWorker:
        worker_type = "web_research_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="mapped authoritative guidance to control points",
                artifacts=[
                    {
                        "id": "guidance_control_matrix",
                        "content": {
                            "controls": ["idempotency", "retry_backoff", "deduplication"],
                            "sources": [
                                "https://example.org/idempotency",
                                "https://example.org/retry-backoff",
                            ],
                        },
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 0},
            )

    class DesignWorker:
        worker_type = "research_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="defined scoped mutation and verification",
                artifacts=[
                    {
                        "id": "fix_design",
                        "content": {
                            "change": "tighten retry jitter bounds",
                            "rationale": "prevent retry burst on webhook timeout",
                        },
                    },
                    {
                        "id": "mutation_scope",
                        "content": {
                            "paths": ["app/worker_kernel/runtime.py"],
                            "line_hints": ["retry schedule branch"],
                        },
                    },
                    {
                        "id": "verification_plan",
                        "content": {
                            "checks": [
                                "idempotency invariant",
                                "retry backoff monotonicity",
                            ]
                        },
                    },
                ],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    class MutateWorker:
        worker_type = "code_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary="mutation scope conflicts with new evidence from runtime path mapping",
                artifacts=[
                    {
                        "id": "planner_issue_snapshot",
                        "content": {
                            "issue_class": "planner_level",
                            "signal_type": "planner_level",
                            "signals": ["artifact_chain_gap", "scope_ambiguity"],
                            "failed_step_id": "mutate_step",
                            "input_artifact_ids": [
                                "fix_design",
                                "mutation_scope",
                                "verification_plan",
                            ],
                        },
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 0},
                metadata={"recommended_action": "return a full fixed plan with a safer mutation boundary"},
            )

    class ReplacementWorker:
        worker_type = "direct_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="replacement plan completed",
                artifacts=[{"id": "final_report", "content": "fixed replacement plan result"}],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    class FakePlannerRuntime:
        last_replan_request: ReplanRequest | None = None

        def replan(self, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
            type(self).last_replan_request = replan_request
            return Plan(
                plan_id="plan_req_replan_fixed",
                request_id=envelope.request_id,
                planner="llm_planner_replan",
                objective=current_plan.objective,
                strategy="fixed_new_plan",
                execution_pattern="finalize",
                global_invariants=["replacement_plan_uses_existing_schema"],
                steps=[
                    PlanStep(
                        step_id="finalize_fixed_plan",
                        worker_type="direct_worker",
                        phase="FINALIZE",
                        mode="summarize_only",
                        task_id="replan_recovery",
                        instruction="Known facts: A replacement plan was requested. Unknowns: none. Do now: finalize. Do not do: do not mutate. Output: final_report.",
                        output_artifacts=["final_report"],
                        max_tool_calls=0,
                        max_model_calls=1,
                        permissions={
                            "read_files": False,
                            "write_files": False,
                            "run_commands": False,
                            "web_research": False,
                        },
                    )
                ],
                budget={"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
            )

    registry = WorkerRegistry()
    registry.register(DiscoverWorker())
    registry.register(ResearchWorker())
    registry.register(DesignWorker())
    registry.register(MutateWorker())
    registry.register(ReplacementWorker())
    initial_plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Research and fix retry behavior",
        strategy="discover_research_design_mutate",
        steps=[
            PlanStep(
                step_id="discover_step",
                worker_type="repo_worker",
                instruction="discover context and candidate paths",
                output_artifacts=["repo_inventory"],
                max_tool_calls=2,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            ),
            PlanStep(
                step_id="research_step",
                worker_type="web_research_worker",
                instruction="collect cited guidance for retry/idempotency",
                input_artifacts=["repo_inventory"],
                output_artifacts=["guidance_control_matrix"],
                max_tool_calls=2,
                max_model_calls=1,
                permissions=_permissions(web_research=True),
            ),
            PlanStep(
                step_id="design_step",
                worker_type="research_worker",
                instruction="define fix design, mutation scope, and verification plan",
                input_artifacts=["repo_inventory", "guidance_control_matrix"],
                output_artifacts=["fix_design", "mutation_scope", "verification_plan"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(),
            ),
            PlanStep(
                step_id="mutate_step",
                worker_type="code_worker",
                instruction="apply scoped mutation",
                input_artifacts=["fix_design", "mutation_scope", "verification_plan"],
                output_artifacts=["change_summary"],
                max_tool_calls=2,
                max_model_calls=0,
                permissions=_permissions(read_files=True),
            ),
        ],
        budget={"max_tool_calls": 7, "max_model_calls": 3, "max_workers": 4, "max_retries": 0},
    )

    result = WorkerKernelRuntime(
        registry=registry,
        planner_runtime=FakePlannerRuntime(),
    ).run(initial_plan, envelope=_envelope())

    assert result.status == "completed"
    assert FakePlannerRuntime.last_replan_request is not None
    assert FakePlannerRuntime.last_replan_request.failed_step_id == "mutate_step"
    assert FakePlannerRuntime.last_replan_request.completed_step_ids == [
        "discover_step",
        "research_step",
        "design_step",
    ]
    assert FakePlannerRuntime.last_replan_request.recommended_action == (
        "return a full fixed plan with a safer mutation boundary"
    )
    assert any(
        a.get("id") == "mutation_scope"
        for a in FakePlannerRuntime.last_replan_request.completed_artifacts
    )
    assert not any(
        a.get("id") == "planner_issue_snapshot"
        for a in FakePlannerRuntime.last_replan_request.completed_artifacts
    )
    assert any(
        a.get("id") == "planner_issue_snapshot"
        for a in FakePlannerRuntime.last_replan_request.failed_step_artifacts
    )
    assert result.metadata["replan"]["replacement_plan"]["plan_id"] == "plan_req_replan_fixed"
    assert any(artifact.id == "final_report" for artifact in result.artifacts)
    assert any(
        row["event"] == "replan_requested" and row["component"] == "worker_kernel_runtime"
        for row in result.metadata["runtime_matrix"]["rows"]
    )


def test_worker_kernel_replan_seeds_carryover_artifacts_for_replacement_plan() -> None:
    class DiscoverWorker:
        worker_type = "repo_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="repo inventory produced",
                artifacts=[{"id": "repo_inventory", "content": {"files": ["pyproject.toml"]}}],
                usage={"tool_calls": 1, "model_calls": 0},
            )

    class FailingWorker:
        worker_type = "research_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary="planner needs to resume from completed repo inventory",
                artifacts=[{"id": "planner_gap", "content": "resume with existing repo_inventory"}],
                metadata={"recommended_action": "use carryover repo_inventory directly"},
            )

    class ReplacementWorker:
        worker_type = "direct_worker"
        seen_inputs: list[str] = []

        def run(self, task: Task) -> Result:
            type(self).seen_inputs = [artifact.id for artifact in task.input_artifacts]
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="replacement consumed carryover",
                artifacts=[{"id": "final_report", "content": {"inputs": type(self).seen_inputs}}],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    class FakePlannerRuntime:
        last_replan_request: ReplanRequest | None = None

        def replan(self, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
            type(self).last_replan_request = replan_request
            return Plan(
                plan_id="plan_req_replan_carryover",
                request_id=envelope.request_id,
                planner="llm_planner_replan",
                objective=current_plan.objective,
                strategy="resume_from_carryover_artifacts",
                execution_pattern="finalize",
                global_invariants=["carryover_artifacts_are_seeded"],
                steps=[
                    PlanStep(
                        step_id="finalize_from_repo_inventory",
                        worker_type="direct_worker",
                        phase="FINALIZE",
                        mode="summarize_only",
                        task_id="replan_recovery",
                        instruction="Known facts: repo_inventory is available as carryover. Unknowns: none. Do now: summarize. Do not do: do not mutate. Output: final_report.",
                        input_artifacts=["repo_inventory"],
                        output_artifacts=["final_report"],
                        max_tool_calls=0,
                        max_model_calls=1,
                        permissions=_permissions(),
                    )
                ],
                budget={"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
            )

    registry = WorkerRegistry()
    registry.register(DiscoverWorker())
    registry.register(FailingWorker())
    registry.register(ReplacementWorker())
    plan = Plan(
        plan_id="plan_req_carryover",
        request_id="req_replan",
        planner="llm_planner",
        objective="Resume from completed repo context",
        strategy="discover_then_replan",
        execution_pattern="discover_analyze",
        global_invariants=["completed_artifacts_are_truth"],
        steps=[
            PlanStep(
                step_id="discover_repo",
                worker_type="repo_worker",
                phase="DISCOVER",
                mode="observe_only",
                task_id="carryover",
                instruction="Known facts: repo needs inventory. Unknowns: files. Do now: inspect repo. Do not do: do not mutate. Output: repo_inventory.",
                output_artifacts=["repo_inventory"],
                max_tool_calls=1,
                max_model_calls=0,
                permissions=_permissions(read_files=True),
            ),
            PlanStep(
                step_id="analyze_gap",
                worker_type="research_worker",
                phase="ANALYZE",
                mode="observe_only",
                task_id="carryover",
                instruction="Known facts: repo_inventory exists. Unknowns: recovery path. Do now: decide if replan needed. Do not do: do not mutate. Output: planner_gap.",
                input_artifacts=["repo_inventory"],
                output_artifacts=["planner_gap"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(),
            ),
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 2, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry, planner_runtime=FakePlannerRuntime()).run(
        plan,
        envelope=_envelope(),
    )

    assert result.status == "completed"
    assert ReplacementWorker.seen_inputs == ["repo_inventory"]
    assert FakePlannerRuntime.last_replan_request is not None
    assert [artifact.id for artifact in FakePlannerRuntime.last_replan_request.carryover_artifacts] == [
        "repo_inventory"
    ]
    assert FakePlannerRuntime.last_replan_request.failed_step["instruction"].startswith("Known facts:")
    assert result.metadata["replan"]["carryover_artifacts"][0]["id"] == "repo_inventory"


def test_worker_kernel_code_flow_executes() -> None:
    plan = Plan(
        plan_id="plan_req_code",
        request_id="req_code",
        planner="code",
        objective="Fix code",
        strategy="observe_then_patch",
        steps=[
            PlanStep(
                step_id="observe_target",
                worker_type="repo_worker",
                instruction="Inspect target",
                output_artifacts=["target_observation"],
                max_tool_calls=4,
                max_model_calls=1,
                permissions={"read_files": True, "write_files": False, "run_commands": False},
            ),
            PlanStep(
                step_id="patch_target",
                worker_type="code_worker",
                instruction="Apply patch",
                input_artifacts=["target_observation"],
                output_artifacts=["patch_result"],
                max_tool_calls=6,
                max_model_calls=1,
                permissions={
                    "read_files": True,
                    "write_files": True,
                    "run_commands": False,
                    "write_paths": ["src/target.py"],
                },
            ),
            PlanStep(
                step_id="verify_patch",
                worker_type="verify_worker",
                instruction="Verify patch",
                input_artifacts=["patch_result"],
                output_artifacts=["verification_result"],
                max_tool_calls=3,
                max_model_calls=0,
                permissions={"read_files": True, "write_files": False, "run_commands": True},
            ),
        ],
        budget={"max_tool_calls": 13, "max_model_calls": 3, "max_workers": 3, "max_retries": 0},
    )

    result = WorkerKernelRuntime().run(plan)

    assert result.status == "completed"
    artifact_ids = {a.get("id") or a.get("artifact_id") for a in result.artifacts}
    assert "patch_result" in artifact_ids
    assert "verification_result" in artifact_ids


def test_worker_kernel_web_research_flow_executes() -> None:
    plan = Plan(
        plan_id="plan_req_web_research",
        request_id="req_web_research",
        planner="research",
        objective="Compare external algorithm references",
        strategy="web_research_then_summarize",
        steps=[
            PlanStep(
                step_id="research_external_sources",
                worker_type="web_research_worker",
                phase="RESEARCH",
                mode="observe_only",
                task_id="external_research",
                instruction="Collect comparable algorithm references and summarize differences.",
                output_artifacts=["web_research_notes"],
                max_tool_calls=4,
                max_model_calls=1,
                permissions={"read_files": False, "write_files": False, "run_commands": True},
            )
        ],
        budget={"max_tool_calls": 4, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
        execution_pattern="research_finalize",
        global_invariants=["no_file_writes_for_web_research"],
    )

    result = WorkerKernelRuntime().run(plan)

    assert result.status == "completed"
    artifact_ids = {a.get("id") or a.get("artifact_id") for a in result.artifacts}
    assert "web_research_notes" in artifact_ids


def test_budget_rejection_before_dispatch() -> None:
    class CountingWorker:
        worker_type = "direct_worker"
        runs = 0

        def run(self, task: Task) -> Result:  # pragma: no cover - must not execute
            type(self).runs += 1
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="unexpected",
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(CountingWorker())

    plan = Plan(
        plan_id="plan_req_overflow",
        request_id="req_overflow",
        planner="direct",
        objective="Overflow budget",
        strategy="direct_answer",
        steps=[
            PlanStep(
                step_id="step-1",
                worker_type="direct_worker",
                instruction="first",
                max_tool_calls=2,
                max_model_calls=1,
            ),
            PlanStep(
                step_id="step-2",
                worker_type="direct_worker",
                instruction="second",
                max_tool_calls=2,
                max_model_calls=1,
            ),
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "budget_exceeded"
    assert CountingWorker.runs == 0


def test_budget_rejection_after_overbudget_worker_result() -> None:
    class OverBudgetWorker:
        worker_type = "direct_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="over budget",
                artifacts=[{"id": "direct_answer", "content": "x"}],
                usage={
                    "tool_calls": task.max_tool_calls + 200,
                    "model_calls": task.max_model_calls,
                },
            )

    registry = WorkerRegistry()
    registry.register(OverBudgetWorker())

    plan = Plan(
        plan_id="plan_req_post_budget",
        request_id="req_post_budget",
        planner="direct",
        objective="Trigger post-result budget gate",
        strategy="direct_answer",
        steps=[
            PlanStep(
                step_id="step-over",
                worker_type="direct_worker",
                instruction="answer",
                output_artifacts=["direct_answer"],
                max_tool_calls=1,
                max_model_calls=1,
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "budget_exceeded"
    assert result.errors
    assert "budget" in result.summary.lower() or "budget" in result.errors[0].lower()


def test_invalid_plan_handling() -> None:
    empty_plan = Plan(
        plan_id="plan_req_invalid_1",
        request_id="req_invalid_1",
        planner="fallback",
        objective="Invalid",
        strategy="observe_first",
        steps=[],
        budget={"max_tool_calls": 3, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    empty_result = WorkerKernelRuntime().run(empty_plan)
    assert empty_result.status == "kernel_error"
    assert empty_result.metadata["issues"][0]["code"] == "invalid_plan"

    malformed_budget_plan = Plan(
        plan_id="plan_req_invalid_2",
        request_id="req_invalid_2",
        planner="fallback",
        objective="Invalid",
        strategy="observe_first",
        steps=[
            PlanStep(
                step_id="bad-step",
                worker_type="direct_worker",
                instruction="invalid",
                max_tool_calls=-1,
                max_model_calls=0,
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 0, "max_workers": 1, "max_retries": 0},
    )

    malformed_result = WorkerKernelRuntime().run(malformed_budget_plan)
    assert malformed_result.status == "kernel_error"
    assert "max_tool_calls" in malformed_result.errors[0]


def test_unknown_worker_handling() -> None:
    plan = Plan(
        plan_id="plan_req_unknown",
        request_id="req_unknown",
        planner="fallback",
        objective="Unknown worker",
        strategy="observe_first",
        steps=[
            PlanStep(
                step_id="step-unknown",
                worker_type="unknown_worker",
                instruction="do unknown thing",
                max_tool_calls=1,
                max_model_calls=1,
            )
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=build_default_registry()).run(plan)
    assert result.status == "kernel_error"
    assert result.metadata["issues"][0]["code"] == "unknown_worker_group"


def test_worker_kernel_normalizes_agentic_tool_model_budget_before_dispatch() -> None:
    class AgenticLikeGroup:
        worker_type = "agentic_group"

        def minimum_model_calls(self, step: PlanStep) -> int:
            return 2

        def run(self, task: Task) -> Result:
            assert task.max_model_calls == 2
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="agent loop completed",
                artifacts=[{"id": "agent_output", "content": "done"}],
                usage={"tool_calls": 1, "model_calls": 2},
            )

    registry = WorkerRegistry()
    registry.register_group(AgenticLikeGroup())
    plan = Plan(
        plan_id="plan_agentic_budget",
        request_id="req_agentic_budget",
        planner="llm_planner",
        objective="use tools then answer",
        strategy="agentic",
        steps=[
            PlanStep(
                step_id="agent_step",
                worker_type="agentic_group",
                instruction="inspect and produce output",
                output_artifacts=["agent_output"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    adjustments = result.metadata["control_plane_adjustments"]
    step_adjustment = next(
        adjustment
        for adjustment in adjustments
        if adjustment.get("step_id") == "agent_step"
    )
    assert step_adjustment["field"] == "max_model_calls"
    assert step_adjustment["from"] == 1
    assert step_adjustment["to"] == 2
    assert any(adjustment["field"] == "budget.max_model_calls" for adjustment in adjustments)


def test_worker_kernel_normalizes_budget_for_retry_envelope_without_step_changes() -> None:
    class DirectWorker:
        worker_type = "direct_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="done",
                artifacts=[{"id": "direct_answer", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(DirectWorker())
    plan = Plan(
        plan_id="plan_retry_envelope_budget",
        request_id="req_retry_envelope_budget",
        planner="test",
        objective="answer",
        strategy="direct",
        steps=[
            PlanStep(
                step_id="answer",
                worker_type="direct_worker",
                instruction="answer",
                output_artifacts=["direct_answer"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(),
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 2},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    adjustments = result.metadata["control_plane_adjustments"]
    assert any(
        adjustment["field"] == "budget.max_retries"
        and adjustment["from"] == 2
        and adjustment["to"] == WORKER_STAGE_REPAIR_ATTEMPTS
        for adjustment in adjustments
    )
    assert any(
        adjustment["field"] == "budget.max_model_calls"
        and adjustment["from"] == 1
        and adjustment["to"] == retry_envelope_call_budget(
            1,
            WORKER_STAGE_REPAIR_ATTEMPTS,
            kind="model",
        )
        for adjustment in adjustments
    )


def test_task_compiler_propagates_phase_mode_task_id_metadata() -> None:
    class MetadataCaptureWorker:
        worker_type = "direct_worker"
        last_metadata: dict | None = None

        def run(self, task: Task) -> Result:
            type(self).last_metadata = task.metadata
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="metadata captured",
                artifacts=[{"id": "direct_answer", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(MetadataCaptureWorker())

    plan = Plan(
        plan_id="plan_req_phase_meta",
        request_id="req_phase_meta",
        planner="llm_planner",
        objective="Capture phase metadata",
        strategy="phase_metadata",
        execution_pattern="discover",
        global_invariants=["observe_before_mutate"],
        steps=[
            PlanStep(
                step_id="discover_scope",
                worker_type="direct_worker",
                phase="DISCOVER",
                mode="observe_only",
                task_id="task_a",
                instruction="collect scope",
                output_artifacts=["direct_answer"],
                max_tool_calls=0,
                max_model_calls=0,
                permissions={"read_files": True, "write_files": False, "run_commands": False},
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert MetadataCaptureWorker.last_metadata is not None
    assert MetadataCaptureWorker.last_metadata["phase"] == "DISCOVER"
    assert MetadataCaptureWorker.last_metadata["mode"] == "observe_only"
    assert MetadataCaptureWorker.last_metadata["task_id"] == "task_a"
    assert MetadataCaptureWorker.last_metadata["objective"] == "Capture phase metadata"
    assert MetadataCaptureWorker.last_metadata["strategy"] == "phase_metadata"
    assert MetadataCaptureWorker.last_metadata["attempt_id"] == "discover_scope_attempt_1"


def test_kernel_preflight_validation_rejects_invalid_planner_plan_before_dispatch() -> None:
    class CountingWorker:
        worker_type = "repo_worker"
        runs = 0

        def run(self, task: Task) -> Result:  # pragma: no cover - should not dispatch
            type(self).runs += 1
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="unexpected",
            )

    registry = WorkerRegistry()
    registry.register(CountingWorker())
    plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Invalid phase-aware plan",
        strategy="invalid",
        execution_pattern="discover",
        global_invariants=["observe_before_mutate"],
        steps=[
            PlanStep(
                step_id="discover_scope",
                worker_type="repo_worker",
                phase="DISCOVER",
                mode="observe_only",
                task_id="main",
                instruction="collect scope",
                output_artifacts=["repo_inventory"],
                max_tool_calls=1,
                max_model_calls=0,
                permissions={"read_files": True},
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 0, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan, envelope=_envelope())

    assert result.status == "kernel_error"
    assert CountingWorker.runs == 0
    assert "permissions must explicitly include" in result.errors[0]


def test_missing_runtime_artifact_blocks_without_replan_runtime() -> None:
    class EmptyProducer:
        worker_type = "repo_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="claimed output but produced no artifacts",
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(EmptyProducer())
    registry.register(EmptyProducer())
    plan = Plan(
        plan_id="plan_req_missing",
        request_id="req_missing",
        planner="llm_planner",
        objective="Consume runtime artifact",
        strategy="missing_artifact",
        steps=[
            PlanStep(
                step_id="produce",
                worker_type="repo_worker",
                instruction="produce artifact",
                output_artifacts=["repo_inventory"],
                max_tool_calls=0,
                max_model_calls=0,
                permissions=_permissions(),
            ),
            PlanStep(
                step_id="consume",
                worker_type="repo_worker",
                instruction="consume artifact",
                input_artifacts=["repo_inventory"],
                output_artifacts=["analysis"],
                max_tool_calls=0,
                max_model_calls=0,
                permissions=_permissions(),
            ),
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 2, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "blocked"
    assert result.metadata["missing_artifacts"] == ["repo_inventory"]
    assert result.metadata["issues"][0]["issue_type"] == "plan_failure"


def test_missing_runtime_artifact_requests_internal_replan_when_available() -> None:
    class EmptyProducer:
        worker_type = "repo_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="claimed output but produced no artifacts",
                usage={"tool_calls": 0, "model_calls": 0},
            )

    class FinalWorker:
        worker_type = "direct_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="replacement completed",
                artifacts=[{"id": "final_report", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 0},
            )

    class FakePlannerRuntime:
        last_replan_request: ReplanRequest | None = None

        def replan(self, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
            type(self).last_replan_request = replan_request
            return Plan(
                plan_id="plan_req_replan_missing_fixed",
                request_id=envelope.request_id,
                planner="llm_planner_replan",
                objective=current_plan.objective,
                strategy="finalize",
                steps=[
                    PlanStep(
                        step_id="finalize",
                        worker_type="direct_worker",
                        instruction="finalize replacement",
                        output_artifacts=["final_report"],
                        max_tool_calls=0,
                        max_model_calls=0,
                        permissions=_permissions(),
                    )
                ],
                budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 1, "max_retries": 0},
            )

    registry = WorkerRegistry()
    registry.register(EmptyProducer())
    registry.register(FinalWorker())
    plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Consume runtime artifact",
        strategy="missing_artifact",
        steps=[
            PlanStep(
                step_id="produce",
                worker_type="repo_worker",
                instruction="produce artifact",
                output_artifacts=["repo_inventory"],
                max_tool_calls=0,
                max_model_calls=0,
                permissions=_permissions(),
            ),
            PlanStep(
                step_id="consume",
                worker_type="repo_worker",
                instruction="consume artifact",
                input_artifacts=["repo_inventory"],
                output_artifacts=["analysis"],
                max_tool_calls=0,
                max_model_calls=0,
                permissions=_permissions(),
            ),
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 2, "max_retries": 0},
    )

    result = WorkerKernelRuntime(
        registry=registry,
        planner_runtime=FakePlannerRuntime(),
    ).run(plan, envelope=_envelope())

    assert result.status == "completed"
    assert FakePlannerRuntime.last_replan_request is not None
    assert FakePlannerRuntime.last_replan_request.issues[0].code == "missing_input_artifacts"
    assert FakePlannerRuntime.last_replan_request.issues[0].metadata["missing_artifacts"] == ["repo_inventory"]


def test_worker_exception_retries_and_records_attempts() -> None:
    class FlakyWorker:
        worker_type = "direct_worker"
        runs = 0

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            if type(self).runs == 1:
                raise RuntimeError("temporary model outage")
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="recovered",
                artifacts=[{"id": "direct_answer", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(FlakyWorker())
    plan = Plan(
        plan_id="plan_req_retry",
        request_id="req_retry",
        planner="direct",
        objective="Retry",
        strategy="retry",
        steps=[
            PlanStep(
                step_id="flaky",
                worker_type="direct_worker",
                instruction="run flaky",
                output_artifacts=["direct_answer"],
                max_tool_calls=0,
                max_model_calls=0,
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 1, "max_retries": 1},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert result.metadata["retry_count"] == 1
    assert result.metadata["instance_attempts_used"] == 2
    assert result.metadata["issues"][0]["issue_type"] == "instance_failure"
    assert result.metadata["loop_decisions"][0]["action"] == "retry_step"
    assert result.metadata["loop_decisions"][0]["reason_code"] == "worker_exception"
    assert any(row["event"] == "loop_decision" for row in result.metadata["runtime_matrix"]["rows"])


def test_worker_runtime_owned_needs_replan_retries_same_step_without_planner_replan() -> None:
    class ToolBudgetWorker:
        worker_type = "repo_worker"
        runs = 0

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            if type(self).runs == 1:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="needs_replan",
                    summary="Cannot finish discovery because remaining_tool_calls is 0.",
                    artifacts=[{"id": "partial_repo_inventory", "content": "root only"}],
                    usage={"tool_calls": task.max_tool_calls, "model_calls": 1},
                    metadata={
                        "issues": [
                            {
                                "issue_type": "plan_failure",
                                "code": "insufficient_tool_budget",
                                "message": "remaining_tool_calls is 0",
                                "retryable": False,
                            }
                        ]
                    },
                )
            assert task.step_id == "discover"
            assert task.max_tool_calls > 1
            assert task.metadata["local_retry_adjustments"]
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="discovery completed after local retry",
                artifacts=[{"id": "repo_inventory", "content": "src/app.py"}],
                usage={"tool_calls": 1, "model_calls": 1},
            )

    class PlannerRuntimeShouldNotRun:
        def replan(self, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
            raise AssertionError("planner replan must not run for worker-runtime failures")

    registry = WorkerRegistry()
    registry.register(ToolBudgetWorker())
    plan = Plan(
        plan_id="plan_local_retry",
        request_id="req_replan",
        planner="test",
        objective="discover repo",
        strategy="discover",
        steps=[
            PlanStep(
                step_id="discover",
                worker_type="repo_worker",
                phase="DISCOVER",
                mode="observe_only",
                instruction="discover",
                output_artifacts=["repo_inventory"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            )
        ],
        budget={"max_tool_calls": 4, "max_model_calls": 4, "max_workers": 1, "max_retries": 1},
    )

    result = WorkerKernelRuntime(
        registry=registry,
        planner_runtime=PlannerRuntimeShouldNotRun(),
    ).run(plan)

    assert result.status == "completed"
    assert result.metadata["retry_count"] == 1
    assert result.metadata["instance_attempts_used"] == 2
    assert any(
        row["event"] == "attempt_retry_scheduled"
        and row["details"]["reason"] == "worker_runtime_failure"
        for row in result.metadata["runtime_matrix"]["rows"]
    )
    assert "replan" not in result.metadata


def test_worker_budget_exceeded_retries_same_step_with_adjusted_task() -> None:
    class BudgetWorker:
        worker_type = "repo_worker"
        runs = 0

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            if type(self).runs == 1:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="budget_exceeded",
                    summary="worker model call budget was exhausted before completion",
                    usage={"tool_calls": 0, "model_calls": task.max_model_calls},
                    metadata={
                        "issues": [
                            {
                                "issue_type": "instance_failure",
                                "code": "model_budget_exceeded",
                                "message": "model budget exhausted",
                                "retryable": False,
                            }
                        ]
                    },
                )
            assert task.max_model_calls > 1
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="completed after adjusted model budget",
                artifacts=[{"id": "repo_inventory", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    registry = WorkerRegistry()
    registry.register(BudgetWorker())
    plan = Plan(
        plan_id="plan_budget_local_retry",
        request_id="req_budget_local_retry",
        planner="test",
        objective="discover repo",
        strategy="discover",
        steps=[
            PlanStep(
                step_id="discover",
                worker_type="repo_worker",
                phase="DISCOVER",
                mode="observe_only",
                instruction="discover",
                output_artifacts=["repo_inventory"],
                max_tool_calls=0,
                max_model_calls=1,
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 4, "max_workers": 1, "max_retries": 1},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert result.metadata["retry_count"] == 1
    assert result.metadata["instance_attempts_used"] == 2


def test_worker_kernel_injects_memory_into_retry_after_partial_write() -> None:
    class PartialMutationWorker:
        worker_type = "filesystem_worker"
        runs = 0
        retry_memory: dict | None = None

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            if type(self).runs == 1:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="budget_exceeded",
                    summary="worker requested more tool calls than the task budget allows",
                    artifacts=[
                        {
                            "id": "mutate_filesystem_operator_tool_1",
                            "kind": "tool_observation",
                            "content": {
                                "instance": "filesystem_operator",
                                "tool_name": "apply_file_operations",
                                "arguments": {
                                    "operations": [
                                        {
                                            "action": "move",
                                            "source": "incoming/A.md",
                                            "destination": "docs/a.md",
                                        }
                                    ]
                                },
                                "observation": {
                                    "operation_count": 1,
                                    "applied_count": 1,
                                    "operations": [
                                        {
                                            "action": "move",
                                            "status": "applied",
                                            "paths": ["incoming/A.md", "docs/a.md"],
                                            "summary": "file moved",
                                        }
                                    ],
                                },
                            },
                        }
                    ],
                    usage={"tool_calls": task.max_tool_calls, "model_calls": 1},
                    metadata={
                        "issues": [
                            {
                                "issue_type": "instance_failure",
                                "code": "tool_budget_exceeded",
                                "message": "tool budget exhausted",
                                "retryable": False,
                            }
                        ]
                    },
                )
            type(self).retry_memory = task.metadata.get("kernel_memory")
            assert any(artifact.id == "kernel_memory_mutate" for artifact in task.input_artifacts)
            assert type(self).retry_memory["successful_write_count"] == 1
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="completed from memory",
                artifacts=[{"id": "change_summary", "content": "done"}],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    registry = WorkerRegistry()
    registry.register(PartialMutationWorker())
    plan = Plan(
        plan_id="plan_retry_memory",
        request_id="req_retry_memory",
        planner="test",
        objective="finish partial mutation",
        strategy="retry with memory",
        steps=[
            PlanStep(
                step_id="mutate",
                worker_type="filesystem_worker",
                phase="MUTATE",
                mode="bounded_mutation",
                instruction="move docs",
                output_artifacts=["change_summary"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(read_files=True, write_files=True),
            )
        ],
        budget={"max_tool_calls": 4, "max_model_calls": 4, "max_workers": 1, "max_retries": 1},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert PartialMutationWorker.retry_memory is not None
    assert result.metadata["worker_memory"]["mutate"]["successful_write_count"] == 1
    assert any(
        row["event"] == "attempt_retry_scheduled" and row["details"]["memory_injected"] is True
        for row in result.metadata["runtime_matrix"]["rows"]
    )


def test_worker_empty_expected_artifact_retries_same_step_with_adjusted_task(tmp_path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "scope selected",
                    "artifacts": [{"id": "mutation_scope", "content": None}],
                }
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "scope repaired",
                    "artifacts": [
                        {
                            "id": "mutation_scope",
                            "content": {
                                "target_paths": ["src/app.py"],
                                "reason": "single source file owns the behavior",
                                "max_files": 1,
                            },
                        }
                    ],
                }
            },
        ]
    )
    registry = WorkerRegistry()
    registry.register_group(
        AgenticWorkerGroupRunner(
            worker_type="code_worker",
            templates=[WorkerInstanceTemplate(name="code_agent", role="design")],
            controller=WorkerLLMController(client),
            toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
        )
    )
    plan = Plan(
        plan_id="plan_empty_artifact_retry",
        request_id="req_empty_artifact_retry",
        planner="test",
        objective="design scoped mutation",
        strategy="design",
        steps=[
            PlanStep(
                step_id="design",
                worker_type="code_worker",
                phase="DESIGN",
                mode="plan_only",
                instruction="choose mutation scope",
                output_artifacts=["mutation_scope"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 5, "max_workers": 1, "max_retries": 1},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert result.metadata["retry_count"] == 1
    assert result.metadata["instance_attempts_used"] == 2
    assert result.metadata["artifact_quality"]["empty_count"] == 1
    assert "non-null, non-empty" in client.prompts[1]
    assert any(
        row["event"] == "attempt_retry_scheduled"
        and row["details"]["reason"] == "worker_runtime_failure"
        for row in result.metadata["runtime_matrix"]["rows"]
    )


def test_invalid_write_scope_block_does_not_retry_same_step() -> None:
    class InvalidScopeWorker:
        worker_type = "code_worker"
        runs = 0

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="blocked",
                summary="write_files was allowed but no write scope paths were provided",
                usage={"tool_calls": 0, "model_calls": 0},
                metadata={
                    "issues": [
                        {
                            "issue_type": "kernel_failure",
                            "code": "invalid_write_scope",
                            "message": "write_files was allowed but no write scope paths were provided",
                            "retryable": False,
                        }
                    ]
                },
            )

    registry = WorkerRegistry()
    registry.register(InvalidScopeWorker())
    plan = Plan(
        plan_id="plan_invalid_scope_no_retry",
        request_id="req_invalid_scope_no_retry",
        planner="test",
        objective="mutate with bad scope",
        strategy="mutate",
        steps=[
            PlanStep(
                step_id="mutate",
                worker_type="code_worker",
                phase="MUTATE",
                mode="bounded_mutation",
                instruction="apply scoped mutation",
                output_artifacts=["change_summary"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(read_files=True),
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1, "max_retries": 2},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "blocked"
    assert InvalidScopeWorker.runs == 1
    assert result.metadata["retry_count"] == 0
    assert not any(
        row["event"] == "attempt_retry_scheduled"
        for row in result.metadata["runtime_matrix"]["rows"]
    )


def test_worker_retries_are_per_stage_not_global() -> None:
    class StageOneWorker:
        worker_type = "stage_one_worker"
        runs = 0

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            if type(self).runs == 1:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="budget_exceeded",
                    summary="tool budget exhausted in stage one",
                    usage={"tool_calls": 0, "model_calls": 0},
                    metadata={
                        "issues": [
                            {
                                "issue_type": "instance_failure",
                                "code": "tool_budget_exceeded",
                                "message": "tool budget exhausted",
                                "retryable": False,
                            }
                        ]
                    },
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="stage one recovered",
                artifacts=[{"id": "stage_one_artifact", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 0},
            )

    class StageTwoWorker:
        worker_type = "stage_two_worker"
        runs = 0

        def run(self, task: Task) -> Result:
            type(self).runs += 1
            if type(self).runs <= 2:
                return Result(
                    run_id=task.run_id,
                    producer=self.worker_type,
                    status="budget_exceeded",
                    summary="model budget exhausted in stage two",
                    usage={"tool_calls": 0, "model_calls": 0},
                    metadata={
                        "issues": [
                            {
                                "issue_type": "instance_failure",
                                "code": "model_budget_exceeded",
                                "message": "model budget exhausted",
                                "retryable": False,
                            }
                        ]
                    },
                )
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="stage two recovered",
                artifacts=[{"id": "stage_two_artifact", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(StageOneWorker())
    registry.register(StageTwoWorker())
    plan = Plan(
        plan_id="plan_per_stage_retries",
        request_id="req_per_stage_retries",
        planner="test",
        objective="prove retries are per stage",
        strategy="two stage retry",
        steps=[
            PlanStep(
                step_id="stage_one",
                worker_type="stage_one_worker",
                phase="DISCOVER",
                mode="observe_only",
                instruction="run stage one",
                output_artifacts=["stage_one_artifact"],
                max_tool_calls=0,
                max_model_calls=0,
            ),
            PlanStep(
                step_id="stage_two",
                worker_type="stage_two_worker",
                phase="VERIFY",
                mode="verify_only",
                instruction="run stage two",
                input_artifacts=["stage_one_artifact"],
                output_artifacts=["stage_two_artifact"],
                max_tool_calls=0,
                max_model_calls=0,
            ),
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 2, "max_retries": 1},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert result.metadata["retry_count"] == 3
    assert result.metadata["instance_attempts_used"] == 5
    retry_rows = [
        row for row in result.metadata["runtime_matrix"]["rows"] if row["event"] == "attempt_retry_scheduled"
    ]
    assert [row["step_id"] for row in retry_rows] == ["stage_one", "stage_two", "stage_two"]


def test_worker_exception_exhausts_retry_budget() -> None:
    class ExplodingWorker:
        worker_type = "direct_worker"

        def run(self, task: Task) -> Result:
            raise RuntimeError("tool crashed")

    registry = WorkerRegistry()
    registry.register(ExplodingWorker())
    plan = Plan(
        plan_id="plan_req_retry_exhausted",
        request_id="req_retry_exhausted",
        planner="direct",
        objective="Retry exhausted",
        strategy="retry",
        steps=[
            PlanStep(
                step_id="explode",
                worker_type="direct_worker",
                instruction="run exploding",
                output_artifacts=["direct_answer"],
                max_tool_calls=0,
                max_model_calls=0,
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 1, "max_retries": 1},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "failed"
    assert result.metadata["retry_count"] == WORKER_STAGE_REPAIR_ATTEMPTS
    assert result.metadata["instance_attempts_used"] == WORKER_STAGE_REPAIR_ATTEMPTS + 1
    assert len(result.metadata["issues"]) == WORKER_STAGE_REPAIR_ATTEMPTS + 1


def test_sequential_worker_group_produces_one_step_result() -> None:
    class SourceWorker:
        worker_type = "source_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="sources found",
                artifacts=[{"id": "source_links", "content": ["https://example.test/a"]}],
                usage={"tool_calls": 1, "model_calls": 0},
            )

    class CitationWorker:
        worker_type = "citation_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="citations formatted",
                artifacts=[{"id": "web_research_notes", "content": "formatted"}],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    registry = WorkerRegistry()
    registry.register_group(
        SequentialWorkerGroupRunner(
            worker_type="web_research_worker",
            workers=[SourceWorker(), CitationWorker()],
        )
    )
    plan = Plan(
        plan_id="plan_req_group",
        request_id="req_group",
        planner="research",
        objective="Run group",
        strategy="group",
        steps=[
            PlanStep(
                step_id="research",
                worker_type="web_research_worker",
                instruction="research",
                output_artifacts=["web_research_notes"],
                max_tool_calls=1,
                max_model_calls=1,
                permissions=_permissions(web_research=True),
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    artifact_ids = {artifact.get("id") for artifact in result.artifacts}
    assert {"source_links", "web_research_notes"} <= artifact_ids
    assert result.metadata["worker_results"][0]["producer"] == "web_research_worker"
    assert len(result.metadata["worker_results"][0]["metadata"]["worker_group_results"]) == 2
