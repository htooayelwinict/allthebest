"""Compiles plan steps into worker tasks."""

from __future__ import annotations

from app.schemas import PlanStep, Task


class TaskCompiler:
    def compile(
        self,
        run_id: str,
        step: PlanStep,
        artifact_store: dict[str, dict],
    ) -> Task:
        input_artifacts: list[dict] = []
        for artifact_id in step.input_artifacts:
            if artifact_id in artifact_store:
                input_artifacts.append(artifact_store[artifact_id])

        task_metadata: dict[str, str] = {}
        if step.phase is not None:
            task_metadata["phase"] = step.phase
        if step.mode is not None:
            task_metadata["mode"] = step.mode
        if step.task_id is not None:
            task_metadata["task_id"] = step.task_id

        return Task(
            task_id=f"task_{step.step_id}",
            run_id=run_id,
            step_id=step.step_id,
            worker_type=step.worker_type,
            instruction=step.instruction,
            input_artifacts=input_artifacts,
            expected_outputs=step.output_artifacts,
            max_tool_calls=step.max_tool_calls,
            max_model_calls=step.max_model_calls,
            permissions=step.permissions,
            metadata=task_metadata,
        )
