"""Worker-kernel runtime for plan execution."""

from __future__ import annotations

import inspect
from typing import Any

from app.planner.contracts import PlannerValidationError
from app.planner.validator import PlannerPlanValidator
from app.runtime_matrix import RuntimeMatrixLogger, attach_runtime_matrix, coerce_runtime_matrix
from app.schemas import ArtifactPayload, Envelope, Plan, ReplanRequest, Result, Task, WorkerIssue
from app.worker_kernel.budget import BudgetExceeded, BudgetGate
from app.worker_kernel.compiler import InvalidWriteScope, MissingInputArtifacts, TaskCompiler
from app.worker_kernel.dispatcher import WorkerDispatcher
from app.worker_kernel.registry import WorkerRegistry, build_default_registry


class WorkerKernelRuntime:
    def __init__(
        self,
        registry: WorkerRegistry | None = None,
        compiler: TaskCompiler | None = None,
        validator: PlannerPlanValidator | None = None,
        planner_runtime: Any | None = None,
        allow_replan: bool = True,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._compiler = compiler or TaskCompiler()
        self._validator = validator or PlannerPlanValidator()
        self._dispatcher = WorkerDispatcher(self._registry)
        self._planner_runtime = planner_runtime
        self._allow_replan = allow_replan

    @classmethod
    def from_env(
        cls,
        dotenv_path: str = ".env",
        *,
        planner_runtime: Any | None = None,
        client_factory: Any | None = None,
        fallback_to_stub_workers: bool = True,
        root_path: str = ".",
        allow_replan: bool = True,
    ) -> "WorkerKernelRuntime":
        from app.worker_kernel.agentic import build_agentic_worker_registry
        from app.worker_kernel.env_config import build_worker_model_client, load_worker_runtime_config

        config = load_worker_runtime_config(dotenv_path)
        client_options = {"client_factory": client_factory} if client_factory is not None else {}
        model_client = build_worker_model_client(dotenv_path, **client_options)
        if model_client is None:
            if not fallback_to_stub_workers:
                raise ValueError("LLM worker runtime is not configured. Set WORKER_LLM_ENABLED=true.")
            registry = build_default_registry()
        else:
            registry = build_agentic_worker_registry(
                model_client=model_client,
                config=config,
                root_path=root_path,
            )
        return cls(
            registry=registry,
            planner_runtime=planner_runtime,
            allow_replan=allow_replan,
        )

    def run(
        self,
        plan: Plan,
        *,
        envelope: Envelope | None = None,
        trace: RuntimeMatrixLogger | None = None,
        _replan_depth: int = 0,
    ) -> Result:
        trace = coerce_runtime_matrix(
            trace,
            plan.metadata,
            envelope.metadata if envelope is not None else None,
        )
        plan, control_plane_adjustments = self._normalize_execution_plan(plan)
        run_id = f"run_{plan.plan_id}"
        self._trace(
            trace,
            event="run_started",
            status="started",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            details={
                "planner": plan.planner,
                "step_count": len(plan.steps),
                "replan_depth": _replan_depth,
            },
        )
        if control_plane_adjustments:
            self._trace(
                trace,
                event="plan_normalized",
                status="completed",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                details={"adjustments": control_plane_adjustments},
            )
        try:
            budget_gate = BudgetGate(plan.budget)
            if envelope is not None:
                self._validator.validate(envelope, plan)
            budget_gate.check_plan(plan)
        except BudgetExceeded as exc:
            self._trace(
                trace,
                event="preflight_failed",
                status="budget_exceeded",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                details={"error": str(exc)},
            )
            return self._finalize_result(self._budget_result(run_id=run_id, exc=exc), trace)
        except (PlannerValidationError, ValueError) as exc:
            self._trace(
                trace,
                event="preflight_failed",
                status="kernel_error",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                details={"error": str(exc)},
            )
            return self._finalize_result(
                self._kernel_error_result(
                    run_id=run_id,
                    summary="Plan failed worker-kernel preflight validation.",
                    issue=self._kernel_issue(code="invalid_plan", message=str(exc)),
                ),
                trace,
            )

        self._trace(
            trace,
            event="preflight_completed",
            status="completed",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            details={"budget": dict(plan.budget)},
        )

        completed_artifacts: dict[str, ArtifactPayload] = {}
        partial_artifacts: list[ArtifactPayload] = []
        failed_step_artifacts: list[ArtifactPayload] = []
        worker_results: list[Result] = []
        completed_step_ids: list[str] = []
        completed_mutation_step_ids: list[str] = []
        issues: list[WorkerIssue] = []
        instance_attempts_used = 0

        for step in plan.steps:
            self._trace(
                trace,
                stage=step.phase or "EXECUTE",
                event="step_started",
                status="started",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                step_id=step.step_id,
                worker_type=step.worker_type,
                details={
                    "mode": step.mode,
                    "input_artifacts": list(step.input_artifacts),
                    "output_artifacts": list(step.output_artifacts),
                },
            )
            try:
                task = self._compiler.compile(
                    run_id=run_id,
                    step=step,
                    artifact_store=completed_artifacts,
                    plan=plan,
                    envelope=envelope,
                )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="task_compiled",
                    status="completed",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={
                        "input_count": len(task.input_artifacts),
                        "expected_outputs": list(task.expected_outputs),
                    },
                )
            except MissingInputArtifacts as exc:
                issue = WorkerIssue(
                    issue_type="plan_failure",
                    code="missing_input_artifacts",
                    message=str(exc),
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    retryable=False,
                    metadata={"missing_artifacts": exc.missing_artifacts},
                )
                issues.append(issue)
                result = Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status="needs_replan" if self._can_replan(envelope, _replan_depth) else "blocked",
                    summary=str(exc),
                    errors=[str(exc)],
                    metadata={
                        "missing_artifacts": exc.missing_artifacts,
                        "issues": [issue.model_dump(mode="json")],
                        "recommended_action": "request a fresh plan that produces the missing artifacts first",
                    },
                )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="task_compile_failed",
                    status=result.status,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"missing_artifacts": exc.missing_artifacts},
                )
                if result.status == "needs_replan":
                    return self._handle_replan(
                        envelope=envelope,
                        plan=plan,
                        run_id=run_id,
                        failed_step_id=step.step_id,
                        result=result,
                        budget_gate=budget_gate,
                        completed_artifacts=completed_artifacts,
                        completed_step_ids=completed_step_ids,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        worker_results=worker_results,
                        issues=issues,
                        instance_attempts_used=instance_attempts_used,
                        replan_depth=_replan_depth,
                        trace=trace,
                    )
                finalized = result.model_copy(
                    update={
                        "artifacts": list(completed_artifacts.values()),
                        "metadata": self._metadata(
                            worker_results=worker_results,
                            issues=issues,
                            partial_artifacts=partial_artifacts,
                            failed_step_artifacts=failed_step_artifacts,
                            budget_gate=budget_gate,
                            instance_attempts_used=instance_attempts_used,
                            extra={**result.metadata, **self._control_plane_metadata(control_plane_adjustments)},
                        ),
                    }
                )
                return self._finalize_result(finalized, trace)
            except InvalidWriteScope as exc:
                issue = WorkerIssue(
                    issue_type="kernel_failure",
                    code="invalid_write_scope",
                    message=str(exc),
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    retryable=False,
                    metadata=dict(exc.metadata),
                )
                issues.append(issue)
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="task_compile_failed",
                    status="blocked",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"error": str(exc), "issue_code": issue.code, **dict(exc.metadata)},
                )
                terminal_result = Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status="blocked",
                    summary=f"Execution stopped at step {step.step_id}: {exc}",
                    artifacts=list(completed_artifacts.values()),
                    errors=[str(exc)],
                    metadata=self._metadata(
                        worker_results=worker_results,
                        issues=issues,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        budget_gate=budget_gate,
                        instance_attempts_used=instance_attempts_used,
                        extra=self._control_plane_metadata(control_plane_adjustments),
                    ),
                )
                return self._finalize_result(terminal_result, trace)

            try:
                budget_gate.before_task(task)
            except BudgetExceeded as exc:
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_budget_blocked",
                    status="budget_exceeded",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"error": str(exc)},
                )
                return self._finalize_result(
                    self._budget_result(
                        run_id=run_id,
                        exc=exc,
                        artifacts=list(completed_artifacts.values()),
                        worker_results=worker_results,
                        issues=issues,
                        budget_gate=budget_gate,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        instance_attempts_used=instance_attempts_used,
                    ),
                    trace,
                )

            result: Result | None = None
            attempt_number = 0
            while True:
                attempt_number += 1
                instance_attempts_used += 1
                attempt_id = f"{step.step_id}_attempt_{attempt_number}"
                attempt_task = self._with_attempt_metadata(task, attempt_id=attempt_id)
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="attempt_started",
                    status="started",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                    worker_type=step.worker_type,
                    details={"attempt_number": attempt_number},
                )

                try:
                    result = self._dispatcher.dispatch(attempt_task, trace=trace)
                    result = self._with_attempt_metadata_on_result(result, attempt_id=attempt_id)
                    budget_gate.after_result(result)
                except BudgetExceeded as exc:
                    issue = WorkerIssue(
                        issue_type="instance_failure",
                        code="budget_exceeded",
                        message=str(exc),
                        step_id=step.step_id,
                        worker_type=step.worker_type,
                        attempt_id=attempt_id,
                        retryable=False,
                    )
                    issues.append(issue)
                    self._trace(
                        trace,
                        stage=step.phase or "EXECUTE",
                        event="attempt_failed",
                        status="budget_exceeded",
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        run_id=run_id,
                        step_id=step.step_id,
                        attempt_id=attempt_id,
                        worker_type=step.worker_type,
                        details={"error": str(exc)},
                    )
                    return self._finalize_result(
                        self._budget_result(
                            run_id=run_id,
                            exc=exc,
                            artifacts=list(completed_artifacts.values()),
                            worker_results=worker_results,
                            issues=issues,
                            budget_gate=budget_gate,
                            partial_artifacts=partial_artifacts,
                            failed_step_artifacts=failed_step_artifacts,
                            instance_attempts_used=instance_attempts_used,
                        ),
                        trace,
                    )
                except Exception as exc:
                    if isinstance(exc, ValueError) and "Unknown worker_type" in str(exc):
                        issue = self._kernel_issue(
                            code="unknown_worker_group",
                            message=str(exc),
                            step_id=step.step_id,
                            worker_type=step.worker_type,
                        )
                        issues.append(issue)
                        self._trace(
                            trace,
                            stage=step.phase or "EXECUTE",
                            event="attempt_failed",
                            status="kernel_error",
                            request_id=plan.request_id,
                            plan_id=plan.plan_id,
                            run_id=run_id,
                            step_id=step.step_id,
                            attempt_id=attempt_id,
                            worker_type=step.worker_type,
                            details={"error": str(exc)},
                        )
                        return self._finalize_result(
                            self._kernel_error_result(
                                run_id=run_id,
                                summary="Worker kernel could not resolve worker group.",
                                issue=issue,
                            ),
                            trace,
                        )
                    issue = WorkerIssue(
                        issue_type="instance_failure",
                        code="worker_exception",
                        message=str(exc),
                        step_id=step.step_id,
                        worker_type=step.worker_type,
                        attempt_id=attempt_id,
                        retryable=True,
                    )
                    issues.append(issue)
                    will_retry = self._retry_instance_failure(
                        budget_gate,
                        step_retries_used=attempt_number - 1,
                    )
                    self._trace(
                        trace,
                        stage=step.phase or "EXECUTE",
                        event="attempt_failed",
                        status="instance_failure",
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        run_id=run_id,
                        step_id=step.step_id,
                        attempt_id=attempt_id,
                        worker_type=step.worker_type,
                        details={"error": str(exc), "retrying": will_retry},
                    )
                    if will_retry:
                        continue
                    return self._finalize_result(
                        self._failed_instance_result(
                            run_id=run_id,
                            step_id=step.step_id,
                            summary=f"Worker instance failed at step {step.step_id}: {exc}",
                            artifacts=list(completed_artifacts.values()),
                            worker_results=worker_results,
                            issues=issues,
                            budget_gate=budget_gate,
                            partial_artifacts=partial_artifacts,
                            failed_step_artifacts=failed_step_artifacts,
                            instance_attempts_used=instance_attempts_used,
                        ),
                        trace,
                    )

                worker_results.append(result)
                result_issues = self._issues_from_result(
                    result,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                )
                issues.extend(result_issues)
                retryable_instance_failure = any(
                    issue.issue_type == "instance_failure" and issue.retryable
                    for issue in result_issues
                )
                worker_runtime_failure = self._is_worker_runtime_owned_failure(
                    result=result,
                    issues=result_issues,
                )
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="attempt_completed",
                    status=result.status,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    attempt_id=attempt_id,
                    worker_type=result.producer,
                    details={
                        "artifact_count": len(result.artifacts),
                        "tool_calls": result.usage.get("tool_calls"),
                        "model_calls": result.usage.get("model_calls"),
                    },
                )
                if (
                    result.status in {"failed", "needs_replan", "budget_exceeded", "blocked"}
                    and (retryable_instance_failure or worker_runtime_failure)
                    and self._retry_instance_failure(
                        budget_gate,
                        step_retries_used=attempt_number - 1,
                    )
                ):
                    previous_task = task
                    task, retry_adjustments = self._adjust_task_for_local_retry(
                        task=task,
                        result=result,
                        issues=result_issues,
                    )
                    self._trace(
                        trace,
                        stage=step.phase or "EXECUTE",
                        event="attempt_retry_scheduled",
                        status="retrying",
                        request_id=plan.request_id,
                        plan_id=plan.plan_id,
                        run_id=run_id,
                        step_id=step.step_id,
                        attempt_id=attempt_id,
                        worker_type=step.worker_type,
                        details={
                            "reason": "worker_runtime_failure"
                            if worker_runtime_failure
                            else "retryable_instance_failure",
                            "task_recompiled": task != previous_task,
                            "adjustments": retry_adjustments,
                        },
                    )
                    failed_step_artifacts.extend(
                        self._annotate_artifacts(
                            result.artifacts,
                            result=result,
                            step_id=step.step_id,
                            attempt_id=attempt_id,
                        )
                    )
                    continue
                break

            if result is None:
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_failed",
                    status="kernel_error",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"error": "missing worker result"},
                )
                return self._finalize_result(
                    self._kernel_error_result(
                        run_id=run_id,
                        summary=f"Step {step.step_id} did not produce a result.",
                        issue=self._kernel_issue(
                            code="missing_worker_result",
                            message=f"Step {step.step_id} did not produce a result.",
                            step_id=step.step_id,
                            worker_type=step.worker_type,
                        ),
                    ),
                    trace,
                )

            annotated_artifacts = self._annotate_artifacts(
                result.artifacts,
                result=result,
                step_id=step.step_id,
                attempt_id=str(result.metadata.get("attempt_id") or f"{step.step_id}_attempt_{attempt_number}"),
            )

            if result.status == "completed":
                for artifact in annotated_artifacts:
                    completed_artifacts[artifact.id] = artifact
                completed_step_ids.append(step.step_id)
                if self._is_mutation_step(step):
                    completed_mutation_step_ids.append(step.step_id)
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_completed",
                    status="completed",
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"artifact_ids": [artifact.id for artifact in annotated_artifacts]},
                )
                continue

            if result.status == "needs_replan":
                partial_artifacts.extend(annotated_artifacts)
                failed_step_artifacts.extend(annotated_artifacts)
                return self._handle_replan(
                    envelope=envelope,
                    plan=plan,
                    run_id=run_id,
                    failed_step_id=step.step_id,
                    result=result,
                    budget_gate=budget_gate,
                    completed_artifacts=completed_artifacts,
                    completed_step_ids=completed_step_ids,
                    partial_artifacts=partial_artifacts,
                    failed_step_artifacts=failed_step_artifacts,
                    worker_results=worker_results,
                    issues=issues,
                    instance_attempts_used=instance_attempts_used,
                    replan_depth=_replan_depth,
                    trace=trace,
                )

            if result.status in ["failed", "blocked", "budget_exceeded", "kernel_error"]:
                failed_step_artifacts.extend(annotated_artifacts)
                terminal_status = result.status
                if (
                    result.status == "failed"
                    and self._is_verification_step(step)
                    and completed_mutation_step_ids
                ):
                    terminal_status = "completed_with_failed_verification"
                self._trace(
                    trace,
                    stage=step.phase or "EXECUTE",
                    event="step_terminal",
                    status=terminal_status,
                    request_id=plan.request_id,
                    plan_id=plan.plan_id,
                    run_id=run_id,
                    step_id=step.step_id,
                    worker_type=step.worker_type,
                    details={"summary": result.summary},
                )
                terminal_result = Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status=terminal_status,
                    summary=f"Execution stopped at step {step.step_id}: {result.summary}",
                    artifacts=list(completed_artifacts.values()),
                    errors=result.errors,
                    warnings=result.warnings,
                    metadata=self._metadata(
                        worker_results=worker_results,
                        issues=issues,
                        partial_artifacts=partial_artifacts,
                        failed_step_artifacts=failed_step_artifacts,
                        budget_gate=budget_gate,
                        instance_attempts_used=instance_attempts_used,
                        extra={**result.metadata, **self._control_plane_metadata(control_plane_adjustments)},
                    ),
                )
                return self._finalize_result(terminal_result, trace)

        self._trace(
            trace,
            event="run_completed",
            status="completed",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            details={
                "completed_steps": list(completed_step_ids),
                "artifact_count": len(completed_artifacts),
            },
        )
        completed_result = Result(
            run_id=run_id,
            producer="worker_kernel",
            status="completed",
            summary="Plan executed successfully.",
            artifacts=list(completed_artifacts.values()),
            usage={
                "tool_calls": budget_gate.tool_calls_used,
                "model_calls": budget_gate.model_calls_used,
                "workers": budget_gate.workers_used,
                "retries": budget_gate.retries_used,
                "instance_attempts": instance_attempts_used,
            },
            metadata=self._metadata(
                worker_results=worker_results,
                issues=issues,
                partial_artifacts=partial_artifacts,
                failed_step_artifacts=failed_step_artifacts,
                budget_gate=budget_gate,
                instance_attempts_used=instance_attempts_used,
                extra=self._control_plane_metadata(control_plane_adjustments),
            ),
        )
        return self._finalize_result(completed_result, trace)

    def _is_mutation_step(self, step: Any) -> bool:
        return step.phase == "MUTATE" or bool(step.permissions.write_files)

    def _is_verification_step(self, step: Any) -> bool:
        return step.phase == "VERIFY" or step.worker_type == "verify_worker"

    def _is_worker_runtime_owned_failure(self, *, result: Result, issues: list[WorkerIssue]) -> bool:
        if result.status == "budget_exceeded":
            return True
        if any(issue.issue_type == "instance_failure" and issue.retryable for issue in issues):
            return True
        issue_codes = {issue.code for issue in issues}
        non_retryable_kernel_codes = {
            "invalid_write_scope",
            "tool_unavailable",
            "unknown_worker_group",
        }
        if issue_codes & non_retryable_kernel_codes:
            return False
        runtime_codes = {
            "budget_exceeded",
            "empty_worker_decision",
            "model_budget_exceeded",
            "model_budget_exhausted_before_final_result",
            "tool_budget_exceeded",
            "tool_execution_error",
            "tool_not_allowed_for_instance",
            "tool_permission_denied",
            "tool_unavailable",
            "worker_llm_error",
            "worker_exception",
        }
        if issue_codes & runtime_codes:
            return True

        text = " ".join(
            [
                result.summary or "",
                " ".join(result.errors or []),
                str(result.metadata.get("issue_code") or ""),
                str(result.metadata.get("recommended_action") or ""),
            ]
        ).lower()
        runtime_fragments = (
            "remaining_tool_calls",
            "remaining_model_calls",
            "tool budget",
            "model budget",
            "budget exhausted",
            "tool observations",
            "tool call",
            "worker model call budget",
            "validation errors for workerllmdecision",
        )
        return any(fragment in text for fragment in runtime_fragments)

    def _adjust_task_for_local_retry(
        self,
        *,
        task: Task,
        result: Result,
        issues: list[WorkerIssue],
    ) -> tuple[Task, list[dict[str, Any]]]:
        adjustments: list[dict[str, Any]] = []
        usage = result.usage or {}
        text = " ".join(
            [
                result.summary or "",
                " ".join(result.errors or []),
                " ".join(issue.code for issue in issues),
            ]
        ).lower()

        max_tool_calls = task.max_tool_calls
        if (
            "tool" in text
            or "remaining_tool_calls" in text
            or int(usage.get("tool_calls", 0) or 0) >= task.max_tool_calls
        ):
            max_tool_calls = max(task.max_tool_calls + 2, task.max_tool_calls * 2, 2)
            adjustments.append(
                {
                    "field": "max_tool_calls",
                    "from": task.max_tool_calls,
                    "to": max_tool_calls,
                    "reason": "local retry after worker/tool budget or tool-call failure",
                }
            )

        max_model_calls = task.max_model_calls
        if (
            "model" in text
            or "final" in text
            or "workerllmdecision" in text
            or int(usage.get("model_calls", 0) or 0) >= task.max_model_calls
        ):
            max_model_calls = max(task.max_model_calls + 1, task.max_model_calls * 2, 2)
            adjustments.append(
                {
                    "field": "max_model_calls",
                    "from": task.max_model_calls,
                    "to": max_model_calls,
                    "reason": "local retry after worker/model/finalization failure",
                }
            )

        if not adjustments:
            metadata = dict(task.metadata)
            retries = list(metadata.get("local_retry_adjustments") or [])
            retries.append({"reason": "local retry without budget adjustment"})
            metadata["local_retry_adjustments"] = retries
            return task.model_copy(update={"metadata": metadata}), []

        metadata = dict(task.metadata)
        retries = list(metadata.get("local_retry_adjustments") or [])
        retries.extend(adjustments)
        metadata["local_retry_adjustments"] = retries
        return task.model_copy(
            update={
                "max_tool_calls": max_tool_calls,
                "max_model_calls": max_model_calls,
                "metadata": metadata,
            }
        ), adjustments

    def _normalize_execution_plan(self, plan: Plan) -> tuple[Plan, list[dict[str, Any]]]:
        adjustments: list[dict[str, Any]] = []
        normalized_steps = []
        budget = dict(plan.budget)
        current_retry_budget = int(budget.get("max_retries", 0) or 0)
        if current_retry_budget < 2:
            adjustments.append(
                {
                    "field": "budget.max_retries",
                    "from": current_retry_budget,
                    "to": 2,
                    "reason": "worker runtime retries are capped per stage, with two instance retries per stage",
                }
            )
            budget["max_retries"] = 2
        for step in plan.steps:
            minimum_model_calls = self._minimum_model_calls_for_step(step)
            if step.max_model_calls < minimum_model_calls:
                adjustments.append(
                    {
                        "step_id": step.step_id,
                        "worker_type": step.worker_type,
                        "field": "max_model_calls",
                        "from": step.max_model_calls,
                        "to": minimum_model_calls,
                        "reason": (
                            "agentic tool workers need a model action turn and a "
                            "post-observation final-result turn"
                        ),
                    }
                )
                step = step.model_copy(update={"max_model_calls": minimum_model_calls})
            normalized_steps.append(step)

        retry_limit = int(budget.get("max_retries", 0) or 0)
        required_tool_calls = sum(
            self._retry_envelope_call_budget(step.max_tool_calls, retry_limit, kind="tool")
            for step in normalized_steps
        )
        required_model_calls = sum(
            self._retry_envelope_call_budget(step.max_model_calls, retry_limit, kind="model")
            for step in normalized_steps
        )
        current_tool_budget = int(budget.get("max_tool_calls", 0) or 0)
        if current_tool_budget < required_tool_calls:
            adjustments.append(
                {
                    "field": "budget.max_tool_calls",
                    "from": current_tool_budget,
                    "to": required_tool_calls,
                    "reason": "budget must cover kernel-owned per-stage retry tool-call envelope",
                }
            )
            budget["max_tool_calls"] = required_tool_calls

        current_model_budget = int(budget.get("max_model_calls", 0) or 0)
        if current_model_budget < required_model_calls:
            adjustments.append(
                {
                    "field": "budget.max_model_calls",
                    "from": current_model_budget,
                    "to": required_model_calls,
                    "reason": "budget must cover kernel-owned per-stage retry model-call envelope",
                }
            )
            budget["max_model_calls"] = required_model_calls

        return plan.model_copy(update={"steps": normalized_steps, "budget": budget}), adjustments

    def _retry_envelope_call_budget(self, initial_limit: int, retry_limit: int, *, kind: str) -> int:
        if initial_limit <= 0:
            return 0

        total = initial_limit
        attempt_limit = initial_limit
        for _ in range(max(0, retry_limit)):
            if kind == "tool":
                attempt_limit = max(attempt_limit + 2, attempt_limit * 2, 2)
            else:
                attempt_limit = max(attempt_limit + 1, attempt_limit * 2, 2)
            total += attempt_limit
        return total

    def _minimum_model_calls_for_step(self, step: Any) -> int:
        try:
            group = self._registry.get(step.worker_type)
        except ValueError:
            return step.max_model_calls

        minimum_model_calls = getattr(group, "minimum_model_calls", None)
        if callable(minimum_model_calls):
            return int(minimum_model_calls(step))
        if step.max_tool_calls > 0 and self._step_uses_tools(step):
            return 2
        return step.max_model_calls

    def _step_uses_tools(self, step: Any) -> bool:
        permissions = step.permissions
        return any(
            [
                permissions.read_files,
                permissions.write_files,
                permissions.run_commands,
                permissions.web_research,
            ]
        )

    def _control_plane_metadata(self, adjustments: list[dict[str, Any]]) -> dict[str, Any]:
        if not adjustments:
            return {}
        return {"control_plane_adjustments": adjustments}

    def _handle_replan(
        self,
        *,
        envelope: Envelope | None,
        plan: Plan,
        run_id: str,
        failed_step_id: str,
        result: Result,
        budget_gate: BudgetGate,
        completed_artifacts: dict[str, ArtifactPayload],
        completed_step_ids: list[str],
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
        worker_results: list[Result],
        issues: list[WorkerIssue],
        instance_attempts_used: int,
        replan_depth: int,
        trace: RuntimeMatrixLogger,
    ) -> Result:
        self._trace(
            trace,
            event="replan_requested",
            status="needs_replan",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            step_id=failed_step_id,
            details={"reason": result.summary},
        )
        replan_request = self._build_replan_request(
            plan=plan,
            run_id=run_id,
            failed_step_id=failed_step_id,
            result=result,
            completed_artifacts=completed_artifacts,
            completed_step_ids=completed_step_ids,
            budget_gate=budget_gate,
            issues=issues,
            partial_artifacts=partial_artifacts,
            failed_step_artifacts=failed_step_artifacts,
        )
        if not self._can_replan(envelope, replan_depth):
            self._trace(
                trace,
                event="replan_deferred",
                status="needs_replan",
                request_id=plan.request_id,
                plan_id=plan.plan_id,
                run_id=run_id,
                step_id=failed_step_id,
            )
            deferred_result = Result(
                run_id=run_id,
                producer="worker_kernel",
                status="needs_replan",
                summary=f"Execution stopped at step {failed_step_id}: {result.summary}",
                artifacts=list(completed_artifacts.values()),
                errors=result.errors,
                warnings=result.warnings,
                metadata=self._metadata(
                    worker_results=worker_results,
                    issues=issues,
                    partial_artifacts=partial_artifacts,
                    failed_step_artifacts=failed_step_artifacts,
                    budget_gate=budget_gate,
                    instance_attempts_used=instance_attempts_used,
                    extra={"replan_request": replan_request.model_dump(mode="json")},
                ),
            )
            return self._finalize_result(deferred_result, trace)

        self._trace(
            trace,
            event="replan_started",
            status="started",
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            step_id=failed_step_id,
        )
        replacement_plan = self._planner_replan(
            envelope=envelope,
            current_plan=plan,
            replan_request=replan_request,
            trace=trace,
        )
        replacement_result = self.run(
            replacement_plan,
            envelope=envelope,
            trace=trace,
            _replan_depth=replan_depth + 1,
        )
        metadata = dict(replacement_result.metadata)
        metadata["replan"] = {
            "request": replan_request.model_dump(mode="json"),
            "replacement_plan": replacement_plan.model_dump(mode="json"),
            "original_worker_results": [r.model_dump(mode="json") for r in worker_results],
            "original_issues": [issue.model_dump(mode="json") for issue in issues],
            "partial_artifacts": [artifact.model_dump(mode="json") for artifact in partial_artifacts],
            "failed_step_artifacts": [
                artifact.model_dump(mode="json") for artifact in failed_step_artifacts
            ],
            "depth": replan_depth + 1,
        }
        self._trace(
            trace,
            event="replan_completed",
            status=replacement_result.status,
            request_id=plan.request_id,
            plan_id=replacement_plan.plan_id,
            run_id=run_id,
            step_id=failed_step_id,
        )
        finalized = replacement_result.model_copy(
            update={"metadata": attach_runtime_matrix(metadata, trace)}
        )
        return self._finalize_result(finalized, trace)

    def _build_replan_request(
        self,
        *,
        plan: Plan,
        run_id: str,
        failed_step_id: str,
        result: Result,
        completed_artifacts: dict[str, ArtifactPayload],
        completed_step_ids: list[str],
        budget_gate: BudgetGate,
        issues: list[WorkerIssue],
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
    ) -> ReplanRequest:
        reason = result.summary or "worker requested replan"
        if result.errors:
            reason = f"{reason}: {'; '.join(result.errors)}"

        return ReplanRequest(
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            failed_step_id=failed_step_id,
            reason=reason,
            worker_result=result.model_dump(mode="json"),
            completed_artifacts=list(completed_artifacts.values()),
            completed_step_ids=list(completed_step_ids),
            remaining_budget={
                "max_tool_calls": max(0, budget_gate.max_tool_calls - budget_gate.tool_calls_used),
                "max_model_calls": max(0, budget_gate.max_model_calls - budget_gate.model_calls_used),
                "max_workers": max(0, budget_gate.max_workers - budget_gate.workers_used),
                "max_retries": budget_gate.max_retries,
                "max_retries_per_stage": budget_gate.max_retries,
                "retry_count_used": budget_gate.retries_used,
            },
            recommended_action=self._recommended_action(result),
            issues=issues,
            partial_artifacts=partial_artifacts,
            failed_step_artifacts=failed_step_artifacts,
        )

    def _recommended_action(self, result: Result) -> str | None:
        value = (result.metadata or {}).get("recommended_action")
        if isinstance(value, str) and value.strip():
            return value
        return None

    def _can_replan(self, envelope: Envelope | None, replan_depth: int) -> bool:
        return (
            self._allow_replan
            and self._planner_runtime is not None
            and envelope is not None
            and replan_depth < 1
        )

    def _retry_instance_failure(self, budget_gate: BudgetGate, *, step_retries_used: int) -> bool:
        if not budget_gate.can_retry(step_retries_used=step_retries_used):
            return False
        try:
            budget_gate.record_retry(step_retries_used=step_retries_used)
        except BudgetExceeded:
            return False
        return True

    def _with_attempt_metadata(self, task: Task, *, attempt_id: str) -> Task:
        metadata = dict(task.metadata)
        metadata["attempt_id"] = attempt_id
        return task.model_copy(update={"metadata": metadata})

    def _with_attempt_metadata_on_result(self, result: Result, *, attempt_id: str) -> Result:
        metadata = dict(result.metadata)
        metadata.setdefault("attempt_id", attempt_id)
        return result.model_copy(update={"metadata": metadata})

    def _annotate_artifacts(
        self,
        artifacts: list[ArtifactPayload],
        *,
        result: Result,
        step_id: str,
        attempt_id: str,
    ) -> list[ArtifactPayload]:
        annotated = []
        for artifact in artifacts:
            updates = {}
            if artifact.producer is None:
                updates["producer"] = result.producer
            if artifact.step_id is None:
                updates["step_id"] = step_id
            if artifact.attempt_id is None:
                updates["attempt_id"] = attempt_id
            annotated.append(artifact.model_copy(update=updates))
        return annotated

    def _issues_from_result(self, result: Result, *, step_id: str, attempt_id: str) -> list[WorkerIssue]:
        raw_issues = result.metadata.get("issues", [])
        issues: list[WorkerIssue] = []
        if isinstance(raw_issues, list):
            for raw_issue in raw_issues:
                if isinstance(raw_issue, WorkerIssue):
                    issues.append(raw_issue)
                elif isinstance(raw_issue, dict):
                    issues.append(WorkerIssue.model_validate(raw_issue))

        issue_type = result.metadata.get("issue_type")
        if isinstance(issue_type, str):
            issues.append(
                WorkerIssue(
                    issue_type=issue_type,
                    code=str(result.metadata.get("issue_code") or result.status),
                    message=result.summary,
                    step_id=step_id,
                    worker_type=result.producer,
                    attempt_id=attempt_id,
                    retryable=bool(result.metadata.get("retryable", False)),
                    metadata=dict(result.metadata),
                )
            )
        return issues

    def _metadata(
        self,
        *,
        worker_results: list[Result],
        issues: list[WorkerIssue],
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
        budget_gate: BudgetGate,
        instance_attempts_used: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "worker_results": [r.model_dump(mode="json") for r in worker_results],
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "partial_artifacts": [artifact.model_dump(mode="json") for artifact in partial_artifacts],
            "failed_step_artifacts": [
                artifact.model_dump(mode="json") for artifact in failed_step_artifacts
            ],
            "retry_count": budget_gate.retries_used,
            "instance_attempts_used": instance_attempts_used,
        }
        if extra:
            metadata.update(extra)
        return metadata

    def _planner_replan(
        self,
        *,
        envelope: Envelope,
        current_plan: Plan,
        replan_request: ReplanRequest,
        trace: RuntimeMatrixLogger,
    ) -> Plan:
        replan_method = self._planner_runtime.replan
        try:
            signature = inspect.signature(replan_method)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and "trace" in signature.parameters:
            return replan_method(envelope, current_plan, replan_request, trace=trace)
        return replan_method(envelope, current_plan, replan_request)

    def _trace(
        self,
        trace: RuntimeMatrixLogger,
        *,
        event: str,
        status: str,
        stage: str | None = "plan_execution",
        request_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
        worker_type: str | None = None,
        elapsed_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        trace.record(
            component="worker_kernel_runtime",
            stage=stage,
            event=event,
            status=status,
            request_id=request_id,
            plan_id=plan_id,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
            worker_type=worker_type,
            elapsed_ms=elapsed_ms,
            details=details,
        )

    def _finalize_result(self, result: Result, trace: RuntimeMatrixLogger) -> Result:
        metadata = attach_runtime_matrix(result.metadata, trace)
        return result.model_copy(update={"metadata": metadata})

    def _budget_result(
        self,
        *,
        run_id: str,
        exc: Exception,
        artifacts: list[ArtifactPayload] | None = None,
        worker_results: list[Result] | None = None,
        issues: list[WorkerIssue] | None = None,
        budget_gate: BudgetGate | None = None,
        partial_artifacts: list[ArtifactPayload] | None = None,
        failed_step_artifacts: list[ArtifactPayload] | None = None,
        instance_attempts_used: int = 0,
    ) -> Result:
        metadata: dict[str, Any] = {}
        if budget_gate is not None:
            metadata = self._metadata(
                worker_results=worker_results or [],
                issues=issues or [],
                partial_artifacts=partial_artifacts or [],
                failed_step_artifacts=failed_step_artifacts or [],
                budget_gate=budget_gate,
                instance_attempts_used=instance_attempts_used,
            )
        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="budget_exceeded",
            summary=str(exc),
            artifacts=artifacts or [],
            errors=[str(exc)],
            metadata=metadata,
        )

    def _kernel_error_result(self, *, run_id: str, summary: str, issue: WorkerIssue) -> Result:
        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="kernel_error",
            summary=summary,
            errors=[issue.message],
            metadata={"issues": [issue.model_dump(mode="json")]},
        )

    def _kernel_issue(
        self,
        *,
        code: str,
        message: str,
        step_id: str | None = None,
        worker_type: str | None = None,
    ) -> WorkerIssue:
        return WorkerIssue(
            issue_type="kernel_failure",
            code=code,
            message=message,
            step_id=step_id,
            worker_type=worker_type,
            retryable=False,
        )

    def _failed_instance_result(
        self,
        *,
        run_id: str,
        step_id: str,
        summary: str,
        artifacts: list[ArtifactPayload],
        worker_results: list[Result],
        issues: list[WorkerIssue],
        budget_gate: BudgetGate,
        partial_artifacts: list[ArtifactPayload],
        failed_step_artifacts: list[ArtifactPayload],
        instance_attempts_used: int,
    ) -> Result:
        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="failed",
            summary=summary,
            artifacts=artifacts,
            errors=[summary],
            metadata=self._metadata(
                worker_results=worker_results,
                issues=issues,
                partial_artifacts=partial_artifacts,
                failed_step_artifacts=failed_step_artifacts,
                budget_gate=budget_gate,
                instance_attempts_used=instance_attempts_used,
                extra={"failed_step_id": step_id},
            ),
        )
