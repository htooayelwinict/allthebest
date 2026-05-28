"""Infrastructure guidance worker."""

from __future__ import annotations

from app.schemas import Result, Task


class InfraWorker:
    worker_type = "infra_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "infra_plan"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Infrastructure guidance generated.",
            artifacts=[{"id": artifact_id, "content": "infra recommendations"}],
            usage={
                "tool_calls": min(task.max_tool_calls, 2),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
