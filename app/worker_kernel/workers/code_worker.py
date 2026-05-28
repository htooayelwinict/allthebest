"""Code action worker."""

from __future__ import annotations

from app.schemas import Result, Task


class CodeWorker:
    worker_type = "code_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "patch_result"
        action = "applied" if task.permissions.get("write_files") else "proposed"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary=f"Code fix {action}.",
            artifacts=[
                {
                    "id": artifact_id,
                    "content": f"code change {action}",
                    "write_files": bool(task.permissions.get("write_files")),
                }
            ],
            usage={
                "tool_calls": min(task.max_tool_calls, 3),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
