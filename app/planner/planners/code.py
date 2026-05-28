"""Planner for code-focused requests."""

from __future__ import annotations

from app.schemas import Envelope, Plan, PlanStep


class CodePlanner:
    planner_name = "code"

    def create_plan(self, envelope: Envelope) -> Plan:
        file_hints = [a.get("path", "") for a in envelope.artifacts if a.get("type") == "file_hint"]
        file_hint_text = ", ".join(p for p in file_hints if p) or "target files"

        steps = [
            PlanStep(
                step_id="observe_target",
                worker_type="repo_worker",
                instruction=(
                    f"Locate and inspect {file_hint_text}. "
                    "Summarize likely issue and relevant code."
                ),
                output_artifacts=["target_observation"],
                max_tool_calls=4,
                max_model_calls=1,
                permissions={
                    "read_files": True,
                    "write_files": False,
                    "run_commands": False,
                },
            ),
            PlanStep(
                step_id="patch_target",
                worker_type="code_worker",
                instruction=(
                    f"Patch {file_hint_text} based on target_observation. "
                    "Make minimal safe changes only."
                ),
                input_artifacts=["target_observation"],
                output_artifacts=["patch_result"],
                max_tool_calls=6,
                max_model_calls=1,
                permissions={
                    "read_files": True,
                    "write_files": True,
                    "run_commands": False,
                },
            ),
            PlanStep(
                step_id="verify_patch",
                worker_type="verify_worker",
                instruction="Verify patch with syntax check or lightweight tests.",
                input_artifacts=["patch_result"],
                output_artifacts=["verification_result"],
                max_tool_calls=3,
                max_model_calls=0,
                permissions={
                    "read_files": True,
                    "write_files": False,
                    "run_commands": True,
                },
            ),
        ]

        plan_id = f"plan_{envelope.request_id}"
        return Plan(
            plan_id=plan_id,
            request_id=envelope.request_id,
            planner=self.planner_name,
            objective=f"Fix request safely: {envelope.normalized_input}",
            strategy="observe_then_patch",
            steps=steps,
            budget={
                "max_tool_calls": 13,
                "max_model_calls": 3,
                "max_workers": 3,
                "max_retries": 0,
            },
            success_criteria=[
                "Target is inspected before mutation.",
                "Patch is minimal.",
                "Verification is attempted.",
                "Result summarizes what changed.",
            ],
        )
