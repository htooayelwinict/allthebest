"""Planner for infra-oriented requests."""

from __future__ import annotations

from app.schemas import Envelope, Plan, PlanStep


class InfraPlanner:
    planner_name = "infra"

    def create_plan(self, envelope: Envelope) -> Plan:
        plan_id = f"plan_{envelope.request_id}"
        return Plan(
            plan_id=plan_id,
            request_id=envelope.request_id,
            planner=self.planner_name,
            objective=f"Address infrastructure request: {envelope.normalized_input}",
            strategy="diagnose_then_recommend",
            steps=[
                PlanStep(
                    step_id="infra_diagnose",
                    worker_type="infra_worker",
                    instruction="Produce infra-focused guidance and next actions.",
                    output_artifacts=["infra_plan"],
                    max_tool_calls=4,
                    max_model_calls=1,
                    permissions={
                        "read_files": True,
                        "write_files": False,
                        "run_commands": True,
                    },
                )
            ],
            budget={
                "max_tool_calls": 4,
                "max_model_calls": 1,
                "max_workers": 1,
                "max_retries": 0,
            },
            success_criteria=["Infra guidance includes actionable next steps."],
        )
