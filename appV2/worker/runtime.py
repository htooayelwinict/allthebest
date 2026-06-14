"""AppV2 single-loop worker runtime."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from appV2.env_config import build_appv2_model_client, load_appv2_runtime_config
from appV2.runtime_matrix import RuntimeMatrixLogger, attach_runtime_matrix, coerce_runtime_matrix
from appV2.schemas import ArtifactRecord, Envelope, PhasePlan, PhaseReplanRequest, RuntimeResult, ValidationIssue
from appV2.validator import AppV2Validator, runtime_scope_input_ids
from appV2.worker.agent_loop import AgentLoop, WorkerModelClient
from appV2.worker.ledgers import ArtifactLedger, MutationLedger
from appV2.worker.result_reconciler import ResultReconciler
from appV2.worker.tools import ToolRegistry


class WorkerRuntime:
    def __init__(
        self,
        *,
        model_client: WorkerModelClient,
        root_path: str | Path = ".",
        planner_runtime: Any | None = None,
        validator: AppV2Validator | None = None,
        max_replans: int = 1,
        tool_timeout_seconds: float = 15.0,
        max_file_bytes: int = 200_000,
    ) -> None:
        self._model_client = model_client
        self._root_path = Path(root_path)
        self._planner_runtime = planner_runtime
        self._validator = validator or AppV2Validator()
        self._max_replans = max_replans
        self._tools = ToolRegistry(root_path=root_path, timeout_seconds=tool_timeout_seconds, max_file_bytes=max_file_bytes)
        self._reconciler = ResultReconciler()

    @classmethod
    def from_env(
        cls,
        dotenv_path: str = ".env",
        *,
        planner_runtime: Any | None = None,
        root_path: str | Path = ".",
        **client_options: Any,
    ) -> "WorkerRuntime":
        config = load_appv2_runtime_config("APPV2_WORKER_LLM", dotenv_path)
        model_client = build_appv2_model_client("APPV2_WORKER_LLM", dotenv_path, **client_options)
        if model_client is None:
            raise ValueError("AppV2 worker is not configured. Set APPV2_WORKER_LLM_ENABLED=true.")
        return cls(
            model_client=model_client,
            root_path=root_path,
            planner_runtime=planner_runtime,
            tool_timeout_seconds=config.timeout_seconds,
        )

    def run(
        self,
        phase_plan: PhasePlan,
        *,
        envelope: Envelope | None = None,
        trace: RuntimeMatrixLogger | None = None,
        _replan_depth: int = 0,
        _carryover_artifacts: list[ArtifactRecord] | None = None,
        _carryover_mutations: MutationLedger | None = None,
        _completed_phase_ids: list[str] | None = None,
        _usage: dict[str, int] | None = None,
    ) -> RuntimeResult:
        run_id = f"v2_run_{phase_plan.plan_id}"
        trace = coerce_runtime_matrix(trace, phase_plan.metadata, envelope.metadata if envelope is not None else None)
        started = time.perf_counter()
        trace.record(
            component="appv2_worker_runtime",
            stage="run",
            event="run_started",
            status="started",
            request_id=phase_plan.request_id,
            plan_id=phase_plan.plan_id,
            run_id=run_id,
            details={"phase_count": len(phase_plan.phases), "replan_depth": _replan_depth},
        )
        carryover_artifacts = list(_carryover_artifacts or [])
        issues = [
            *self._validator.validate_phase_plan(
                phase_plan,
                envelope=envelope,
                initial_artifact_ids=[artifact.id for artifact in carryover_artifacts if artifact.lifecycle == "completed"],
            ),
            *self._validate_worker_plan_invariants(phase_plan),
        ]
        blocking_issues = [issue for issue in issues if issue.severity == "blocking"]
        if blocking_issues:
            if self._can_replan(envelope=envelope, depth=_replan_depth) and self._issues_are_planner_owned(blocking_issues):
                replan_request = self._build_replan_request(
                    plan=phase_plan,
                    run_id=run_id,
                    failed_phase_id=phase_plan.phases[0].phase_id if phase_plan.phases else "plan_preflight",
                    reason="Phase plan failed worker preflight validation.",
                    artifacts=ArtifactLedger(carryover_artifacts),
                    completed_phase_ids=list(_completed_phase_ids or []),
                    issues=blocking_issues,
                    usage=_usage or {"model_calls": 0, "tool_calls": 0, "replans": _replan_depth},
                )
                return self._run_replan(
                    envelope=envelope,
                    current_plan=phase_plan,
                    replan_request=replan_request,
                    trace=trace,
                    depth=_replan_depth,
                    started=started,
                    carryover_artifacts=carryover_artifacts,
                    carryover_mutations=_carryover_mutations,
                    completed_phase_ids=list(_completed_phase_ids or []),
                    usage=_usage or {"model_calls": 0, "tool_calls": 0, "replans": _replan_depth},
                )
            return self._finish(
                run_id=run_id,
                plan=phase_plan,
                status="kernel_error",
                summary="Phase plan failed worker preflight validation.",
                artifacts=carryover_artifacts,
                issues=blocking_issues,
                usage=_usage or {},
                metadata={"completed_phase_ids": list(_completed_phase_ids or [])},
                trace=trace,
                started=started,
            )

        artifacts = ArtifactLedger(carryover_artifacts)
        mutations = _carryover_mutations or MutationLedger()
        usage = dict(_usage or {"model_calls": 0, "tool_calls": 0, "replans": _replan_depth})
        usage["replans"] = _replan_depth
        loop = AgentLoop(model_client=self._model_client, tools=self._tools, validator=self._validator)
        completed_phase_ids: list[str] = list(_completed_phase_ids or [])
        runtime_issues: list[ValidationIssue] = []

        for phase in phase_plan.phases:
            missing_inputs = self._missing_completed_inputs(
                phase.input_artifacts,
                artifacts,
                available_runtime_scope_ids=runtime_scope_input_ids(envelope, plan=phase_plan),
            )
            if missing_inputs:
                issue = _issue(
                    owner="planner",
                    severity="blocking",
                    code="missing_completed_input_artifact",
                    message=f"Phase {phase.phase_id} requires missing completed input artifacts: {', '.join(missing_inputs)}",
                    metadata={"phase_id": phase.phase_id, "missing_artifacts": missing_inputs},
                )
                runtime_issues.append(issue)
                if self._can_replan(envelope=envelope, depth=_replan_depth):
                    replan_request = self._build_replan_request(
                        plan=phase_plan,
                        run_id=run_id,
                        failed_phase_id=phase.phase_id,
                        reason=issue.message,
                        artifacts=artifacts,
                        completed_phase_ids=completed_phase_ids,
                        issues=[issue],
                        usage=usage,
                    )
                    return self._run_replan(
                        envelope=envelope,
                        current_plan=phase_plan,
                        replan_request=replan_request,
                        trace=trace,
                        depth=_replan_depth,
                        started=started,
                        carryover_artifacts=artifacts.completed(),
                        carryover_mutations=mutations,
                        completed_phase_ids=completed_phase_ids,
                        usage=usage,
                    )
                return self._finish(
                    run_id=run_id,
                    plan=phase_plan,
                    status="needs_replan",
                    summary=issue.message,
                    artifacts=artifacts.completed(),
                    issues=runtime_issues,
                    usage=usage,
                    metadata={"completed_phase_ids": completed_phase_ids, "mutation_ledger": mutations.compact_view()},
                    trace=trace,
                    started=started,
                )
            trace.record(
                component="appv2_worker_runtime",
                stage=phase.phase,
                event="phase_started",
                status="started",
                request_id=phase_plan.request_id,
                plan_id=phase_plan.plan_id,
                run_id=run_id,
                step_id=phase.phase_id,
                details={"phase_id": phase.phase_id},
            )
            outcome = loop.run_phase(
                envelope=envelope,
                plan=phase_plan,
                phase=phase,
                artifacts=artifacts,
                mutations=mutations,
                retry_memory={"completed_phase_ids": completed_phase_ids, "previous_issues": [issue.code for issue in runtime_issues[-5:]]},
                trace=trace,
            )
            usage["model_calls"] += outcome.model_calls
            usage["tool_calls"] += outcome.tool_calls
            trace.record(
                component="appv2_worker_runtime",
                stage=phase.phase,
                event="phase_completed",
                status=outcome.status,
                request_id=phase_plan.request_id,
                plan_id=phase_plan.plan_id,
                run_id=run_id,
                step_id=phase.phase_id,
                details={"summary": outcome.summary, "issues": [issue.code for issue in outcome.issues]},
            )
            if outcome.status == "completed":
                completed_phase_ids.append(phase.phase_id)
                continue
            runtime_issues.extend(outcome.issues)
            if outcome.status == "needs_replan":
                signal_issue = self._issue_from_replan_signal(phase_id=phase.phase_id, signal=outcome.replan_signal, summary=outcome.summary)
                runtime_issues.append(signal_issue)
                if self._can_replan(envelope=envelope, depth=_replan_depth) and self._issues_are_planner_owned([signal_issue, *outcome.issues]):
                    replan_request = self._build_replan_request(
                        plan=phase_plan,
                        run_id=run_id,
                        failed_phase_id=phase.phase_id,
                        reason=outcome.summary,
                        artifacts=artifacts,
                        completed_phase_ids=completed_phase_ids,
                        issues=[signal_issue, *outcome.issues],
                        usage=usage,
                        replan_signal=outcome.replan_signal,
                    )
                    return self._run_replan(
                        envelope=envelope,
                        current_plan=phase_plan,
                        replan_request=replan_request,
                        trace=trace,
                        depth=_replan_depth,
                        started=started,
                        carryover_artifacts=artifacts.completed(),
                        carryover_mutations=mutations,
                        completed_phase_ids=completed_phase_ids,
                        usage=usage,
                    )
                rejected = _issue(
                    owner="worker",
                    severity="blocking",
                    code="worker_replan_signal_rejected",
                    message="Worker requested replan for a non-planner-owned issue; runtime kept it local.",
                    metadata={"phase_id": phase.phase_id, "replan_signal": outcome.replan_signal},
                )
                runtime_issues.append(rejected)
                return self._finish(
                    run_id=run_id,
                    plan=phase_plan,
                    status="blocked",
                    summary=rejected.message,
                    artifacts=artifacts.completed(),
                    issues=runtime_issues,
                    usage=usage,
                    metadata={"completed_phase_ids": completed_phase_ids, "mutation_ledger": mutations.compact_view()},
                    trace=trace,
                    started=started,
                )
            return self._finish(
                run_id=run_id,
                plan=phase_plan,
                status=outcome.status,
                summary=outcome.summary,
                artifacts=artifacts.completed(),
                issues=runtime_issues,
                usage=usage,
                metadata={"completed_phase_ids": completed_phase_ids, "mutation_ledger": mutations.compact_view()},
                trace=trace,
                started=started,
            )

        return self._finish(
            run_id=run_id,
            plan=phase_plan,
            status="completed",
            summary="AppV2 worker completed all phases.",
            artifacts=artifacts.completed(),
            issues=runtime_issues,
            usage=usage,
            metadata={"completed_phase_ids": completed_phase_ids, "mutation_ledger": mutations.compact_view()},
            trace=trace,
            started=started,
        )

    def _finish(
        self,
        *,
        run_id: str,
        plan: PhasePlan,
        status: str,
        summary: str,
        artifacts: list,
        issues: list,
        usage: dict,
        metadata: dict,
        trace: RuntimeMatrixLogger,
        started: float,
    ) -> RuntimeResult:
        elapsed_ms = (time.perf_counter() - started) * 1000
        trace.record(
            component="appv2_worker_runtime",
            stage="run",
            event="run_completed",
            status=status,
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            elapsed_ms=elapsed_ms,
            details={"artifact_count": len(artifacts), "issue_count": len(issues)},
        )
        result = self._reconciler.reconcile(
            run_id=run_id,
            plan=plan,
            status=status,
            summary=summary,
            artifacts=artifacts,
            issues=issues,
            usage=usage,
            metadata={**metadata, "elapsed_ms": round(elapsed_ms, 3)},
        )
        return result.model_copy(update={"metadata": attach_runtime_matrix(dict(result.metadata), trace)})

    def _can_replan(self, *, envelope: Envelope | None, depth: int) -> bool:
        return envelope is not None and self._planner_runtime is not None and depth < self._max_replans

    def _run_replan(
        self,
        *,
        envelope: Envelope | None,
        current_plan: PhasePlan,
        replan_request: PhaseReplanRequest,
        trace: RuntimeMatrixLogger,
        depth: int,
        started: float,
        carryover_artifacts: list[ArtifactRecord],
        carryover_mutations: MutationLedger | None,
        completed_phase_ids: list[str],
        usage: dict[str, int],
    ) -> RuntimeResult:
        if envelope is None or self._planner_runtime is None:
            return self._finish(
                run_id=replan_request.run_id,
                plan=current_plan,
                status="needs_replan",
                summary=replan_request.reason,
                artifacts=carryover_artifacts,
                issues=replan_request.issues,
                usage=usage,
                metadata={"completed_phase_ids": completed_phase_ids},
                trace=trace,
                started=started,
            )
        trace.record(
            component="appv2_worker_runtime",
            stage="planner_replan",
            event="worker_internal_replan_requested",
            status="started",
            request_id=current_plan.request_id,
            plan_id=current_plan.plan_id,
            run_id=replan_request.run_id,
            step_id=replan_request.failed_phase_id,
            details={
                "reason": replan_request.reason,
                "issue_codes": [issue.code for issue in replan_request.issues],
                "completed_artifact_count": len(replan_request.completed_artifacts),
            },
        )
        replacement = self._planner_runtime.replan(envelope, current_plan, replan_request, trace=trace)
        usage["replans"] = depth + 1
        return self.run(
            replacement,
            envelope=envelope,
            trace=trace,
            _replan_depth=depth + 1,
            _carryover_artifacts=carryover_artifacts,
            _carryover_mutations=carryover_mutations,
            _completed_phase_ids=completed_phase_ids,
            _usage=usage,
        )

    def _build_replan_request(
        self,
        *,
        plan: PhasePlan,
        run_id: str,
        failed_phase_id: str,
        reason: str,
        artifacts: ArtifactLedger,
        completed_phase_ids: list[str],
        issues: list[ValidationIssue],
        usage: dict[str, int],
        replan_signal: dict[str, Any] | None = None,
    ) -> PhaseReplanRequest:
        completed_artifacts = artifacts.completed()
        return PhaseReplanRequest(
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            run_id=run_id,
            failed_phase_id=failed_phase_id,
            reason=reason,
            completed_artifacts=completed_artifacts,
            carryover_artifacts=completed_artifacts,
            completed_phase_ids=completed_phase_ids,
            remaining_budgets={"usage_so_far": usage},
            recommended_action=(replan_signal or {}).get("recommended_action") if isinstance(replan_signal, dict) else None,
            issues=issues,
            metadata={
                "source": "appv2_worker_runtime",
                "replan_signal": replan_signal,
                "completed_artifact_ids": [artifact.id for artifact in completed_artifacts],
            },
        )

    def _missing_completed_inputs(
        self,
        input_artifact_ids: list[str],
        artifacts: ArtifactLedger,
        *,
        available_runtime_scope_ids: set[str] | None = None,
    ) -> list[str]:
        runtime_scope_ids = available_runtime_scope_ids or set()
        return [
            artifact_id
            for artifact_id in input_artifact_ids
            if artifact_id not in runtime_scope_ids and artifacts.completed_by_id(artifact_id) is None
        ]

    def _validate_worker_plan_invariants(self, plan: PhasePlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen_outputs: dict[str, str] = {}
        for phase in plan.phases:
            for artifact_id in phase.output_artifacts:
                if artifact_id in seen_outputs:
                    issues.append(
                        _issue(
                            owner="planner",
                            severity="blocking",
                            code="duplicate_output_artifact_id",
                            message=f"Artifact '{artifact_id}' is produced by multiple phases.",
                            metadata={
                                "artifact_id": artifact_id,
                                "first_phase_id": seen_outputs[artifact_id],
                                "duplicate_phase_id": phase.phase_id,
                            },
                        )
                    )
                else:
                    seen_outputs[artifact_id] = phase.phase_id
        return issues

    def _issue_from_replan_signal(
        self,
        *,
        phase_id: str,
        signal: dict[str, Any] | None,
        summary: str,
    ) -> ValidationIssue:
        signal = signal or {}
        issue_codes = [str(code) for code in signal.get("issue_codes") or []]
        planner_owned = any(_is_planner_owned_code(code) for code in issue_codes)
        return _issue(
            owner="planner" if planner_owned else "worker",
            severity="blocking",
            code=issue_codes[0] if issue_codes else "worker_requested_replan",
            message=summary,
            metadata={"phase_id": phase_id, "issue_codes": issue_codes, "replan_signal": signal},
        )

    def _issues_are_planner_owned(self, issues: list[ValidationIssue]) -> bool:
        if not issues:
            return False
        return all(issue.owner == "planner" or _is_planner_owned_code(issue.code) for issue in issues)


def _is_planner_owned_code(code: str) -> bool:
    planner_owned_codes = {
        "missing_completed_input_artifact",
        "missing_artifact_producer",
        "duplicate_output_artifact_id",
        "phase_order_regression",
        "mutation_policy_required",
        "mutation_requires_file_write_group",
        "verification_policy_required",
        "verify_requires_verify_tool_group",
        "mutation_requires_verify_after",
        "impossible_phase_ordering",
        "missing_required_input_artifact",
        "plan_artifact_contract_drift",
        "repo_plan_drift",
        "user_intent_drift",
        "mutation_scope_missing_required_path",
        "required_evidence_unavailable",
        "semantic_plan_failure",
    }
    runtime_owned_prefixes = (
        "tool_",
        "model_",
        "mutation_execution",
        "mutation_snapshot",
        "path_",
        "forbidden_",
        "manifest_",
        "phase_output_validation",
        "worker_decision",
        "budget_",
    )
    return code in planner_owned_codes and not code.startswith(runtime_owned_prefixes)


def _issue(
    *,
    owner: str,
    severity: str,
    code: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        owner=owner,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        metadata=metadata or {},
    )
