"""Deterministic planner output validation."""

from __future__ import annotations

from app.planner.contracts import ALLOWED_WORKER_TYPES, PlannerValidationError
from app.schemas import Envelope, Plan


DISCOVERY_CONTEXT_SIGNALS = {
    "target_file",
    "repo_tree",
    "dependency_manifest",
    "performance_evidence",
    "scope_clarification",
}
DISCOVERY_CONSTRAINT_SIGNALS = {
    "target_locations_must_be_identified_before_mutation",
    "target_scope_must_be_identified_before_mutation",
    "performance_claims_require_evidence",
}


class PlannerPlanValidator:
    """Validates generated plans before worker-kernel execution."""

    _WRITE_CAPABLE_WORKERS = {"code_worker"}

    def validate(self, envelope: Envelope, plan: Plan) -> Plan:
        errors: list[str] = []

        if plan.request_id != envelope.request_id:
            errors.append("plan.request_id must match envelope.request_id")

        if not (plan.plan_id or "").strip():
            errors.append("plan.plan_id must be non-empty")

        if not plan.steps:
            errors.append("plan.steps must contain at least one step")

        step_ids = [step.step_id for step in plan.steps]
        if len(set(step_ids)) != len(step_ids):
            errors.append("plan.steps step_id values must be unique")

        for step in plan.steps:
            if step.worker_type not in ALLOWED_WORKER_TYPES:
                errors.append(f"unknown worker_type: {step.worker_type}")
            if step.max_tool_calls < 0:
                errors.append(f"step {step.step_id} max_tool_calls must be non-negative")
            if step.max_model_calls < 0:
                errors.append(f"step {step.step_id} max_model_calls must be non-negative")

        required_tools = sum(step.max_tool_calls for step in plan.steps)
        required_models = sum(step.max_model_calls for step in plan.steps)
        max_tool_calls = int(plan.budget.get("max_tool_calls", 0) or 0)
        max_model_calls = int(plan.budget.get("max_model_calls", 0) or 0)
        max_workers = int(plan.budget.get("max_workers", 0) or 0)

        if max_tool_calls < required_tools:
            errors.append("plan budget max_tool_calls must cover sum of step max_tool_calls")
        if max_model_calls < required_models:
            errors.append("plan budget max_model_calls must cover sum of step max_model_calls")
        if max_workers < len(plan.steps):
            errors.append("plan budget max_workers must cover step count")

        produced: set[str] = set()
        write_step_indexes: list[int] = []
        for index, step in enumerate(plan.steps):
            for artifact_id in step.input_artifacts:
                if artifact_id not in produced:
                    errors.append(
                        f"step {step.step_id} input_artifact '{artifact_id}' is not produced by an earlier step"
                    )

            write_files = bool(step.permissions.get("write_files", False))
            if write_files:
                write_step_indexes.append(index)
                if step.worker_type not in self._WRITE_CAPABLE_WORKERS:
                    errors.append(
                        f"step {step.step_id} requests write_files but worker_type {step.worker_type} is not write-capable"
                    )

            produced.update(step.output_artifacts)

        if write_step_indexes:
            if self._requires_discovery_before_mutation(envelope):
                first_write = min(write_step_indexes)
                if not any(self._is_discovery_step(plan.steps[i].worker_type, plan.steps[i].permissions) for i in range(first_write)):
                    errors.append("mutation requires a prior read-only discovery step")

            last_write = max(write_step_indexes)
            if not any(step.worker_type == "verify_worker" for step in plan.steps[last_write + 1 :]):
                errors.append("mutation requires a later verify_worker step")

            if envelope.confidence < 0.7 and not any(
                self._is_discovery_step(plan.steps[i].worker_type, plan.steps[i].permissions)
                for i in range(min(write_step_indexes))
            ):
                errors.append("low-confidence envelopes cannot mutate before discovery")

        if errors:
            raise PlannerValidationError(errors)
        return plan

    def _requires_discovery_before_mutation(self, envelope: Envelope) -> bool:
        context = {value.strip().lower() for value in envelope.context_needed}
        constraints = {value.strip().lower() for value in envelope.constraints}
        return bool(context & DISCOVERY_CONTEXT_SIGNALS) or bool(constraints & DISCOVERY_CONSTRAINT_SIGNALS)

    def _is_discovery_step(self, worker_type: str, permissions: dict) -> bool:
        return worker_type in {"repo_worker", "research_worker", "infra_worker"} and not bool(
            permissions.get("write_files", False)
        )
