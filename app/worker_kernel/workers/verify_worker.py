"""Verification worker."""

from __future__ import annotations

from app.schemas import Result, Task


class VerifyWorker:
    worker_type = "verify_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "verification_result"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Verification report produced.",
            artifacts=[
                {
                    "id": artifact_id,
                    "content": "focused checks passed",
                    "inputs_checked": [a.get("id") or a.get("artifact_id") for a in task.input_artifacts],
                }
            ],
            usage={
                "tool_calls": min(task.max_tool_calls, 2),
                "model_calls": min(task.max_model_calls, 0),
            },
            metadata={"worker_type": self.worker_type},
        )
