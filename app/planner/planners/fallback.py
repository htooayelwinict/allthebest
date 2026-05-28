"""Fallback planner when no domain-specific planner applies."""

from __future__ import annotations

from app.schemas import Envelope, Plan, PlanStep


class FallbackPlanner:
    planner_name = "fallback"

    def create_plan(self, envelope: Envelope) -> Plan:
        plan_id = f"plan_{envelope.request_id}"
        return Plan(
            plan_id=plan_id,
            request_id=envelope.request_id,
            planner=self.planner_name,
            objective=f"Clarify and observe before acting: {envelope.normalized_input}",
            strategy="observe_first",
            steps=[
                PlanStep(
                    step_id="observe_scope",
                    worker_type="repo_worker",
                    instruction=(
                        "Collect scope context and identify likely target files before any mutation. "
                        "Summarize assumptions and unknowns."
                    ),
                    output_artifacts=["scope_observation"],
                    max_tool_calls=3,
                    max_model_calls=1,
                    permissions={
                        "read_files": True,
                        "write_files": False,
                        "run_commands": False,
                    },
                )
            ],
            budget={
                "max_tool_calls": 3,
                "max_model_calls": 1,
                "max_workers": 1,
                "max_retries": 0,
            },
            success_criteria=["First step is observation only.", "No file mutation is attempted."],
        )
