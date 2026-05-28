"""Research synthesis worker."""

from __future__ import annotations

from app.schemas import Result, Task


class ResearchWorker:
    worker_type = "research_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "research_notes"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Research synthesis produced.",
            artifacts=[{"id": artifact_id, "content": "research summary"}],
            usage={
                "tool_calls": min(task.max_tool_calls, 3),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
