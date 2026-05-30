"""LLM-backed planner prompt chain with deterministic validation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.planner.contracts import PlannerModelClient, PlannerValidationError, WORKER_CATALOG
from app.planner.validator import PlannerPlanValidator
from app.schemas import Envelope, Plan


class PlannerPromptChainError(RuntimeError):
    """Raised when planner prompt-chain generation fails."""


class LLMPlanCompiler:
    """Compile a validated plan from an envelope using draft+repair stages."""

    def __init__(
        self,
        *,
        model_client: PlannerModelClient,
        validator: PlannerPlanValidator | None = None,
    ) -> None:
        self._model_client = model_client
        self._validator = validator or PlannerPlanValidator()

    def run(self, envelope: Envelope) -> Plan:
        schema = Plan.model_json_schema()
        draft_prompt = self._draft_prompt(envelope=envelope, schema=schema)
        draft_response = self._model_client.complete_json(
            stage="draft_plan",
            prompt=draft_prompt,
            schema=schema,
        )

        try:
            plan = self._parse_and_validate(envelope=envelope, response=draft_response)
            diagnostics = self._build_diagnostics(
                mode="completed",
                stages=["draft_plan", "validate_plan"],
                model_calls=1,
                repair_attempted=False,
                validation_errors=[],
                envelope=envelope,
            )
            return self._with_metadata(plan, diagnostics)
        except (ValidationError, PlannerValidationError) as draft_exc:
            validation_errors = self._serialize_validation_errors(draft_exc)

        repair_response = self._model_client.complete_json(
            stage="repair_plan",
            prompt=self._repair_prompt(
                envelope=envelope,
                schema=schema,
                draft_response=draft_response,
                validation_errors=validation_errors,
            ),
            schema=schema,
        )

        try:
            repaired_plan = self._parse_and_validate(envelope=envelope, response=repair_response)
            diagnostics = self._build_diagnostics(
                mode="repaired",
                stages=["draft_plan", "validate_plan", "repair_plan", "validate_plan"],
                model_calls=2,
                repair_attempted=True,
                validation_errors=validation_errors,
                envelope=envelope,
            )
            return self._with_metadata(repaired_plan, diagnostics)
        except (ValidationError, PlannerValidationError) as repair_exc:
            repair_errors = self._serialize_validation_errors(repair_exc)
            diagnostics = self._build_diagnostics(
                mode="failed",
                stages=["draft_plan", "validate_plan", "repair_plan", "validate_plan"],
                model_calls=2,
                repair_attempted=True,
                validation_errors=repair_errors,
                envelope=envelope,
            )
            raise PlannerPromptChainError(
                f"planner prompt chain failed after repair: {json.dumps(diagnostics, sort_keys=True)}"
            ) from repair_exc

    def _parse_and_validate(self, *, envelope: Envelope, response: str) -> Plan:
        plan = Plan.model_validate_json(response)
        return self._validator.validate(envelope, plan)

    def _draft_prompt(self, *, envelope: Envelope, schema: dict[str, Any]) -> str:
        payload = {
            "task": "Create a safe execution plan JSON.",
            "instructions": [
                "Return only JSON matching the plan schema exactly.",
                "Do not add markdown or prose outside JSON.",
                "Use only worker types in worker_catalog.",
                "Every input_artifact must be produced by an earlier step output_artifacts.",
                "Plan budget must cover all step max_tool_calls/max_model_calls and step count.",
                "If write_files=true appears in any step, include prior read-only discovery when constraints/context require discovery.",
                "If write_files=true appears in any step, include a later verify_worker step.",
                "Treat envelope artifacts as search hints unless they are explicit paths.",
            ],
            "permission_semantics": {
                "read_files": "May inspect repository and files.",
                "write_files": "May mutate files. Only safe on code_worker.",
                "run_commands": "May execute shell or test commands.",
            },
            "safety_policies": {
                "discovery_before_mutation": "Do not mutate before target/dependency/performance context is established when required.",
                "verify_after_write": "Any file write requires a later verify_worker step.",
                "evidence_required": "Do not claim performance fixes without evidence collection when requested.",
                "low_confidence": "Low confidence or high ambiguity should favor observe-first/discovery-first sequencing.",
            },
            "worker_catalog": WORKER_CATALOG,
            "envelope": envelope.model_dump(mode="json"),
            "plan_schema": schema,
        }
        return json.dumps(payload, sort_keys=True)

    def _repair_prompt(
        self,
        *,
        envelope: Envelope,
        schema: dict[str, Any],
        draft_response: str,
        validation_errors: list[dict[str, Any]],
    ) -> str:
        payload = {
            "task": "Repair the invalid plan JSON so it passes schema and safety validation.",
            "instructions": [
                "Return only repaired JSON.",
                "Use only worker types in worker_catalog.",
                "Ensure artifact dependencies reference prior outputs.",
                "Ensure budget covers step totals.",
                "Ensure discovery-before-mutation and verify-after-write policies.",
            ],
            "validation_errors": validation_errors,
            "previous_response": draft_response[:8000],
            "envelope": envelope.model_dump(mode="json"),
            "worker_catalog": WORKER_CATALOG,
            "plan_schema": schema,
        }
        return json.dumps(payload, sort_keys=True)

    def _serialize_validation_errors(self, error: ValidationError | PlannerValidationError) -> list[dict[str, Any]]:
        if isinstance(error, ValidationError):
            return [
                {
                    "type": err.get("type"),
                    "loc": err.get("loc"),
                    "msg": err.get("msg"),
                }
                for err in error.errors(include_input=False)
            ]
        return [{"type": "planner_validation", "msg": msg} for msg in error.errors]

    def _build_diagnostics(
        self,
        *,
        mode: str,
        stages: list[str],
        model_calls: int,
        repair_attempted: bool,
        validation_errors: list[dict[str, Any]],
        envelope: Envelope,
    ) -> dict[str, Any]:
        return {
            "mode": mode,
            "stages": stages,
            "model_calls": model_calls,
            "repair_attempted": repair_attempted,
            "validation_errors": validation_errors,
            "envelope_input_type": envelope.input_type,
            "envelope_complexity_hint": envelope.complexity_hint,
        }

    def _with_metadata(self, plan: Plan, diagnostics: dict[str, Any]) -> Plan:
        metadata = dict(plan.metadata)
        metadata["llm_planner"] = diagnostics
        return plan.model_copy(update={"metadata": metadata})
