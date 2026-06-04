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
        _initial_artifacts: list[ArtifactPayload] | None = None,
        _initial_completed_step_ids: list[str] | None = None,
        _initial_completed_mutation_step_ids: list[str] | None = None,
    ) -> Result:
        initial_artifacts = self._artifact_store(_initial_artifacts or [])
        initial_artifact_ids = set(initial_artifacts)
        initial_completed_step_ids = list(_initial_completed_step_ids or [])
        initial_completed_mutation_step_ids = list(_initial_completed_mutation_step_ids or [])
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
                "initial_artifact_count": len(initial_artifacts),
                "initial_completed_step_count": len(initial_completed_step_ids),
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
                self._validator.validate(
                    envelope,
                    plan,
                    initial_artifact_ids=initial_artifact_ids,
                )
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

        completed_artifacts: dict[str, ArtifactPayload] = dict(initial_artifacts)
        partial_artifacts: list[ArtifactPayload] = []
        failed_step_artifacts: list[ArtifactPayload] = []
        worker_results: list[Result] = []
        completed_step_ids: list[str] = list(initial_completed_step_ids)
        completed_mutation_step_ids: list[str] = list(initial_completed_mutation_step_ids)
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
                    step=step,
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

    def _is_worker_runtime_owned_failure(
        self,
        *,
        result: Result,
        issues: list[WorkerIssue],
        step: Any | None = None,
    ) -> bool:
        if step is not None and self._verification_failed_before_command(step=step, result=result, issues=issues):
            return True
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
            "tool_call_contract_error",
            "tool_execution_error",
            "tool_not_allowed_for_instance",
            "tool_permission_denied",
            "tool_unavailable",
            "worker_output_contract_miss",
            "worker_artifact_content_empty",
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
            "worker output contract",
        )
        return any(fragment in text for fragment in runtime_fragments)

    def _verification_failed_before_command(
        self,
        *,
        step: Any,
        result: Result,
        issues: list[WorkerIssue],
    ) -> bool:
        if not self._is_verification_step(step):
            return False
        if result.status not in {"failed", "blocked", "budget_exceeded"}:
            return False
        if self._has_verification_command_evidence(result):
            return False

        text = self._result_issue_text(result=result, issues=issues)
        no_command_fragments = (
            "before test execution",
            "before verification",
            "could not execute verification",
            "could not be completed",
            "did not execute",
            "no verification command",
            "not executed",
            "test execution",
            "verification command",
            "verification could not",
        )
        budget_fragments = (
            "budget exhaustion",
            "budget exhausted",
            "instance budget",
            "model budget",
            "remaining_model_calls",
            "worker model call budget",
        )
        if any(fragment in text for fragment in no_command_fragments):
            return True
        if any(fragment in text for fragment in budget_fragments) and not self._has_verification_result_payload(result):
            return True
        return False

    def _has_verification_command_evidence(self, result: Result) -> bool:
        command_tools = {"run_readonly_command", "run_focused_tests", "run_project_tests"}
        for artifact in result.artifacts:
            tool_name = artifact.metadata.get("tool_name")
            if tool_name in command_tools:
                return True
            content = artifact.content
            if isinstance(content, dict):
                if content.get("tool_name") in command_tools:
                    return True
                observation = content.get("observation")
                if isinstance(observation, dict) and content.get("tool_name") in command_tools:
                    return True
                observations = content.get("observations")
                if isinstance(observations, list):
                    for item in observations:
                        if isinstance(item, dict) and item.get("tool_name") in command_tools:
                            return True
                commands = content.get("commands")
                if isinstance(commands, list) and commands:
                    return True
        for group_result in result.metadata.get("worker_group_results") or []:
            if not isinstance(group_result, dict):
                continue
            try:
                nested = Result.model_validate(group_result)
            except Exception:
                continue
            if self._has_verification_command_evidence(nested):
                return True
        return False

    def _has_verification_result_payload(self, result: Result) -> bool:
        for artifact in result.artifacts:
            content = artifact.content
            if not isinstance(content, dict):
                continue
            if content.get("commands"):
                return True
            if content.get("returncode") is not None:
                return True
            if content.get("failed_commands"):
                return True
        return False

    def _result_issue_text(self, *, result: Result, issues: list[WorkerIssue]) -> str:
        parts = [
            result.summary or "",
            " ".join(result.errors or []),
            str(result.metadata.get("issue_code") or ""),
            str(result.metadata.get("recommended_action") or ""),
        ]
        for issue in issues:
            parts.extend([issue.code, issue.message, str(issue.metadata)])
        for artifact in result.artifacts:
            if artifact.id in {"test_results", "verification_results", "verification_result"}:
                parts.append(str(artifact.content))
        return " ".join(parts).lower()

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
        verification_retry = (
            task.worker_type == "verify_worker"
            or str(task.metadata.get("phase") or "").upper() == "VERIFY"
        ) and not self._has_verification_command_evidence(result)

        max_tool_calls = task.max_tool_calls
        if (
            "tool" in text
            or "remaining_tool_calls" in text
            or verification_retry
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
            or "budget" in text
            or "worker_artifact_content_empty" in text
            or "worker_output_contract_miss" in text
            or "workerllmdecision" in text
            or verification_retry
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
        if verification_retry:
            metadata["force_verification_command_first"] = True
            metadata["verification_retry_reason"] = "verification_failed_before_command"
            metadata["runtime_retry_instruction"] = (
                "This is a replacement VERIFY instance. Run run_project_tests or an "
                "explicit verification_plan command before final_result; do not spend "
                "turns on capability discovery unless command selection is impossible."
            )
        elif (
            "worker_output_contract_miss" in text
            or "worker_artifact_content_empty" in text
            or "missing expected artifacts" in text
            or "empty expected artifacts" in text
        ):
            metadata["force_final_result_artifacts"] = True
            metadata["runtime_retry_instruction"] = (
                "This is a replacement worker instance after an output artifact quality failure. "
                "Do not call tools unless essential. Return final_result with every "
                "expected artifact id exactly once. Each artifact content must be "
                "non-null and non-empty, using the expected_output_contract schemas "
                "and concrete content from input artifacts and observations."
            )
        elif "tool_call_contract_error" in text or "tool_not_allowed_for_instance" in text:
            metadata["force_strict_tool_call_shape"] = True
            metadata["runtime_retry_instruction"] = (
                "This is a replacement worker instance after a malformed or disallowed "
                "tool-call envelope. Use only exact names from available_tools. If you "
                "need a tool, return JSON exactly as {'tool_calls':[{'tool_name':'name',"
                "'arguments':{...}}]}; otherwise return final_result."
            )
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
        carryover_artifacts = list(completed_artifacts.values())
        replacement_result = self.run(
            replacement_plan,
            envelope=envelope,
            trace=trace,
            _replan_depth=replan_depth + 1,
            _initial_artifacts=carryover_artifacts,
            _initial_completed_step_ids=list(completed_step_ids),
            _initial_completed_mutation_step_ids=self._completed_mutation_step_ids(
                plan=plan,
                completed_step_ids=completed_step_ids,
            ),
        )
        metadata = dict(replacement_result.metadata)
        metadata["replan"] = {
            "request": replan_request.model_dump(mode="json"),
            "replacement_plan": replacement_plan.model_dump(mode="json"),
            "carryover_artifacts": [artifact.model_dump(mode="json") for artifact in carryover_artifacts],
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

        completed = list(completed_artifacts.values())
        return ReplanRequest(
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            failed_step_id=failed_step_id,
            reason=reason,
            worker_result=result.model_dump(mode="json"),
            completed_artifacts=completed,
            carryover_artifacts=completed,
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
            failed_step=self._failed_step_payload(plan=plan, failed_step_id=failed_step_id),
            failure_observation=self._failure_observation(result),
        )

    def _artifact_store(self, artifacts: list[ArtifactPayload]) -> dict[str, ArtifactPayload]:
        store: dict[str, ArtifactPayload] = {}
        for artifact in artifacts:
            normalized = ArtifactPayload.model_validate(artifact)
            store[normalized.id] = normalized
        return store

    def _completed_mutation_step_ids(self, *, plan: Plan, completed_step_ids: list[str]) -> list[str]:
        completed = set(completed_step_ids)
        return [step.step_id for step in plan.steps if step.step_id in completed and self._is_mutation_step(step)]

    def _failed_step_payload(self, *, plan: Plan, failed_step_id: str) -> dict[str, Any]:
        for step in plan.steps:
            if step.step_id == failed_step_id:
                return step.model_dump(mode="json")
        return {}

    def _failure_observation(self, result: Result) -> dict[str, Any]:
        observations = []
        for artifact in result.artifacts:
            content = artifact.content if isinstance(artifact.content, dict) else {}
            if artifact.kind != "tool_observation" and not content.get("tool_name"):
                continue
            observation = content.get("observation") if isinstance(content.get("observation"), dict) else {}
            observations.append(
                {
                    "artifact_id": artifact.id,
                    "tool_name": content.get("tool_name"),
                    "command": observation.get("command"),
                    "returncode": observation.get("returncode"),
                    "stdout_tail": str(observation.get("stdout") or "")[-2000:],
                    "stderr_tail": str(observation.get("stderr") or "")[-2000:],
                }
            )
        return {
            "status": result.status,
            "summary": result.summary,
            "errors": list(result.errors),
            "warnings": list(result.warnings),
            "usage": dict(result.usage),
            "artifact_ids": [artifact.id for artifact in result.artifacts],
            "tool_observations": observations,
            "issue_codes": [
                issue.get("code")
                for issue in result.metadata.get("issues", [])
                if isinstance(issue, dict) and issue.get("code")
            ],
            "expected_artifacts": result.metadata.get("expected_artifacts") or [],
            "produced_artifacts": result.metadata.get("produced_artifacts")
            or [artifact.id for artifact in result.artifacts],
            "missing_artifacts": result.metadata.get("missing_artifacts") or [],
            "artifact_contract": result.metadata.get("artifact_contract") or [],
            "worker_group_results": self._compact_worker_group_results(
                result.metadata.get("worker_group_results") or []
            ),
        }

    def _compact_worker_group_results(self, worker_group_results: Any) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        if not isinstance(worker_group_results, list):
            return compact
        for item in worker_group_results:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            compact.append(
                {
                    "status": item.get("status"),
                    "summary": item.get("summary"),
                    "producer": item.get("producer"),
                    "usage": item.get("usage"),
                    "artifact_ids": [
                        artifact.get("id")
                        for artifact in item.get("artifacts", [])
                        if isinstance(artifact, dict)
                    ],
                    "issue_codes": [
                        issue.get("code")
                        for issue in metadata.get("issues", [])
                        if isinstance(issue, dict) and issue.get("code")
                    ],
                }
            )
        return compact

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
            "artifact_quality": self._aggregate_artifact_quality(worker_results),
        }
        if extra:
            metadata.update(extra)
        return metadata

    def _aggregate_artifact_quality(self, worker_results: list[Result]) -> dict[str, Any]:
        aggregate: dict[str, Any] = {
            "expected_count": 0,
            "missing_count": 0,
            "empty_count": 0,
            "synthesized_count": 0,
            "missing_artifacts": [],
            "empty_artifacts": [],
            "synthesized_artifacts": [],
            "steps": [],
        }
        for result in worker_results:
            quality = result.metadata.get("artifact_quality")
            if not isinstance(quality, dict):
                continue
            aggregate["expected_count"] += int(quality.get("expected_count", 0) or 0)
            aggregate["missing_count"] += int(quality.get("missing_count", 0) or 0)
            aggregate["empty_count"] += int(quality.get("empty_count", 0) or 0)
            aggregate["synthesized_count"] += int(quality.get("synthesized_count", 0) or 0)
            for key in ("missing_artifacts", "empty_artifacts", "synthesized_artifacts"):
                values = quality.get(key)
                if isinstance(values, list):
                    aggregate[key].extend(str(value) for value in values)
            aggregate["steps"].append(
                {
                    "producer": result.producer,
                    "status": result.status,
                    **quality,
                }
            )
        return aggregate

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
