"""Planner for research-oriented requests."""

from __future__ import annotations

from app.schemas import Envelope, Plan, PlanStep


class ResearchPlanner:
    planner_name = "research"

    def create_plan(self, envelope: Envelope) -> Plan:
        plan_id = f"plan_{envelope.request_id}"
        return Plan(
            plan_id=plan_id,
            request_id=envelope.request_id,
            planner=self.planner_name,
            objective=f"Research and summarize: {envelope.normalized_input}",
            strategy="research_then_summarize",
            steps=[
                PlanStep(
                    step_id="collect_research",
                    worker_type="research_worker",
                    instruction="Collect relevant context and produce a concise synthesis.",
                    output_artifacts=["research_notes"],
                    max_tool_calls=5,
                    max_model_calls=1,
                    permissions={
                        "read_files": True,
                        "write_files": False,
                        "run_commands": False,
                    },
                )
            ],
            budget={
                "max_tool_calls": 5,
                "max_model_calls": 1,
                "max_workers": 1,
                "max_retries": 0,
            },
            success_criteria=["Research summary is produced."],
        )
