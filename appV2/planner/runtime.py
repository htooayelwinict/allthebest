"""AppV2 phase planner runtime."""

from __future__ import annotations

import time
from typing import Any

from appV2.env_config import build_appv2_model_client
from appV2.planner.contracts import PlannerModelClient
from appV2.planner.prompt_chain import PhasePlannerPromptChain
from appV2.runtime_matrix import RuntimeMatrixLogger, attach_runtime_matrix, coerce_runtime_matrix
from appV2.schemas import Envelope, PhasePlan, PhaseReplanRequest


class PhasePlannerRuntime:
    def __init__(
        self,
        *,
        model_client: PlannerModelClient | None = None,
        prompt_chain: Any | None = None,
        fallback_on_error: bool = False,
    ) -> None:
        if prompt_chain is not None:
            self._prompt_chain = prompt_chain
        elif model_client is not None:
            self._prompt_chain = PhasePlannerPromptChain(model_client=model_client)
        else:
            self._prompt_chain = None
        self._fallback_on_error = fallback_on_error

    @classmethod
    def from_env(
        cls,
        dotenv_path: str = ".env",
        *,
        fallback_on_error: bool = False,
        **client_options: Any,
    ) -> "PhasePlannerRuntime":
        model_client = build_appv2_model_client("APPV2_PLANNER_LLM", dotenv_path, **client_options)
        return cls(model_client=model_client, fallback_on_error=fallback_on_error)

    def run(self, envelope: Envelope, *, trace: RuntimeMatrixLogger | None = None) -> PhasePlan:
        trace = coerce_runtime_matrix(trace, envelope.metadata)
        started = time.perf_counter()
        trace.record(
            component="appv2_phase_planner_runtime",
            stage="draft_phase_plan",
            event="run_started",
            status="started",
            request_id=envelope.request_id,
        )
        if self._prompt_chain is None:
            plan = self._fallback_plan(envelope)
        else:
            try:
                plan = self._prompt_chain.run(envelope, trace=trace)
            except Exception:
                if not self._fallback_on_error:
                    raise
                plan = self._fallback_plan(envelope)
        elapsed_ms = (time.perf_counter() - started) * 1000
        trace.record(
            component="appv2_phase_planner_runtime",
            stage="draft_phase_plan",
            event="run_completed",
            status="completed",
            request_id=envelope.request_id,
            plan_id=plan.plan_id,
            elapsed_ms=elapsed_ms,
            details={"phase_count": len(plan.phases)},
        )
        metadata = dict(plan.metadata)
        metadata["appv2_phase_planner_runtime"] = {"elapsed_ms": round(elapsed_ms, 3)}
        metadata = attach_runtime_matrix(metadata, trace)
        return plan.model_copy(update={"metadata": metadata})

    def replan(
        self,
        envelope: Envelope,
        current_plan: PhasePlan,
        replan_request: PhaseReplanRequest,
        *,
        trace: RuntimeMatrixLogger | None = None,
    ) -> PhasePlan:
        trace = coerce_runtime_matrix(trace, current_plan.metadata, envelope.metadata)
        trace.record(
            component="appv2_phase_planner_runtime",
            stage="planner_replan",
            event="replan_started",
            status="started",
            request_id=envelope.request_id,
            plan_id=current_plan.plan_id,
            details={"failed_phase_id": replan_request.failed_phase_id, "reason": replan_request.reason},
        )
        if self._prompt_chain is None:
            return self._fallback_plan(envelope).model_copy(update={"plan_id": f"{current_plan.plan_id}_replan_01"})
        plan = self._prompt_chain.replan(envelope=envelope, current_plan=current_plan, replan_request=replan_request, trace=trace)
        trace.record(
            component="appv2_phase_planner_runtime",
            stage="planner_replan",
            event="replan_completed",
            status="completed",
            request_id=envelope.request_id,
            plan_id=plan.plan_id,
            details={"phase_count": len(plan.phases)},
        )
        metadata = attach_runtime_matrix(dict(plan.metadata), trace)
        return plan.model_copy(update={"metadata": metadata})

    def _fallback_plan(self, envelope: Envelope) -> PhasePlan:
        return PhasePlan.model_validate(
            {
                "plan_id": f"v2_plan_{envelope.request_id}_clarify",
                "request_id": envelope.request_id,
                "objective": f"Clarify before acting: {envelope.normalized_input}",
                "strategy": "finalize_with_clarification",
                "phases": [
                    {
                        "phase_id": "finalize_clarification",
                        "phase": "FINALIZE",
                        "goal": "Provide safe clarification guidance without tools or mutation.",
                        "instructions": ["Ask concise clarification or provide direct safe guidance."],
                        "input_artifacts": [],
                        "output_artifacts": ["final_report"],
                        "allowed_tool_groups": [],
                        "acceptance_checks": ["final_report answers or asks concise clarification"],
                        "max_tool_calls": 0,
                        "max_model_calls": 1,
                    }
                ],
                "budgets": {"max_tool_calls": 0, "max_model_calls": 1, "max_retries": 0},
                "global_invariants": ["no_tools", "no_mutation"],
                "success_criteria": ["User receives direct clarification or safe guidance."],
                "artifact_contracts": [{"id": "final_report", "description": "Direct response or clarification."}],
                "metadata": {"appv2_phase_planner": {"mode": "fallback"}},
            }
        )
