"""Planner for direct question-answer style requests."""

from __future__ import annotations

from app.schemas import Envelope, Plan, PlanStep


class DirectPlanner:
    planner_name = "direct"

    def create_plan(self, envelope: Envelope) -> Plan:
        plan_id = f"plan_{envelope.request_id}"
        return Plan(
            plan_id=plan_id,
            request_id=envelope.request_id,
            planner=self.planner_name,
            objective=f"Answer user question: {envelope.normalized_input}",
            strategy="direct_answer",
            steps=[
                PlanStep(
                    step_id="direct_answer",
                    worker_type="direct_worker",
                    instruction="Provide a concise direct answer without using tools.",
                    output_artifacts=["direct_answer"],
                    max_tool_calls=0,
                    max_model_calls=1,
                    permissions={
                        "read_files": False,
                        "write_files": False,
                        "run_commands": False,
                    },
                )
            ],
            budget={
                "max_tool_calls": 0,
                "max_model_calls": 1,
                "max_workers": 1,
                "max_retries": 0,
            },
            success_criteria=[
                "Direct answer is provided.",
                "No file mutation is attempted.",
            ],
        )
