"""Direct response worker."""

from __future__ import annotations

from app.schemas import Result, Task


class DirectWorker:
    worker_type = "direct_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "direct_answer"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Direct response generated.",
            artifacts=[{"id": artifact_id, "content": task.instruction}],
            usage={
                "tool_calls": min(task.max_tool_calls, 0),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
