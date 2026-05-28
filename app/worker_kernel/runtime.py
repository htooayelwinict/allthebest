"""Worker-kernel runtime for plan execution."""

from __future__ import annotations

from app.schemas import Plan, Result
from app.worker_kernel.budget import BudgetExceeded, BudgetGate
from app.worker_kernel.compiler import TaskCompiler
from app.worker_kernel.dispatcher import WorkerDispatcher
from app.worker_kernel.registry import WorkerRegistry, build_default_registry


class WorkerKernelRuntime:
    def __init__(
        self,
        registry: WorkerRegistry | None = None,
        compiler: TaskCompiler | None = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._compiler = compiler or TaskCompiler()
        self._dispatcher = WorkerDispatcher(self._registry)

    def run(self, plan: Plan) -> Result:
        run_id = f"run_{plan.plan_id}"
        budget_gate = BudgetGate(plan.budget)

        try:
            budget_gate.check_plan(plan)
        except BudgetExceeded as exc:
            return Result(
                run_id=run_id,
                producer="worker_kernel",
                status="budget_exceeded",
                summary=str(exc),
                errors=[str(exc)],
            )

        artifacts: dict[str, dict] = {}
        worker_results: list[Result] = []

        for step in plan.steps:
            task = self._compiler.compile(
                run_id=run_id,
                step=step,
                artifact_store=artifacts,
            )

            try:
                budget_gate.before_task(task)
            except BudgetExceeded as exc:
                return Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status="budget_exceeded",
                    summary=str(exc),
                    errors=[str(exc)],
                    metadata={"worker_results": [r.model_dump() for r in worker_results]},
                )

            result = self._dispatcher.dispatch(task)

            try:
                budget_gate.after_result(result)
            except BudgetExceeded as exc:
                worker_results.append(result)
                return Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status="budget_exceeded",
                    summary=str(exc),
                    artifacts=list(artifacts.values()),
                    errors=[str(exc)],
                    metadata={"worker_results": [r.model_dump() for r in worker_results]},
                )

            worker_results.append(result)

            for artifact in result.artifacts:
                artifact_id = artifact.get("id") or artifact.get("artifact_id")
                if artifact_id:
                    artifacts[str(artifact_id)] = artifact

            if result.status in ["failed", "blocked", "budget_exceeded"]:
                return Result(
                    run_id=run_id,
                    producer="worker_kernel",
                    status=result.status,
                    summary=f"Execution stopped at step {step.step_id}: {result.summary}",
                    artifacts=list(artifacts.values()),
                    errors=result.errors,
                    warnings=result.warnings,
                    metadata={"worker_results": [r.model_dump() for r in worker_results]},
                )

        return Result(
            run_id=run_id,
            producer="worker_kernel",
            status="completed",
            summary="Plan executed successfully.",
            artifacts=list(artifacts.values()),
            usage={
                "tool_calls": budget_gate.tool_calls_used,
                "model_calls": budget_gate.model_calls_used,
                "workers": budget_gate.workers_used,
            },
            metadata={"worker_results": [r.model_dump() for r in worker_results]},
        )
