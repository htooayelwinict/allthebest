"""AppV2 phase planner prompt chain."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from appV2.planner.contracts import ArtifactContractBundle, PhaseSkeleton, PlannerModelClient
from appV2.prompts import PLANNER_STAGE_CONTRACTS, PLANNER_SYSTEM_PROMPT, prompt_contract, schema_prompt_summary
from appV2.runtime_matrix import RuntimeMatrixLogger
from appV2.schemas import Envelope, PhasePlan, PhaseReplanRequest
from appV2.validator import AppV2ValidationError, AppV2Validator, blocking


class PhasePlannerPromptChainError(RuntimeError):
    """Raised when the phase planner cannot emit a valid PhasePlan."""


class PhasePlannerPromptChain:
    def __init__(self, *, model_client: PlannerModelClient, validator: AppV2Validator | None = None) -> None:
        self._model_client = model_client
        self._validator = validator or AppV2Validator()
        self._plan_schema = PhasePlan.model_json_schema()

    def run(self, envelope: Envelope, *, trace: RuntimeMatrixLogger | None = None) -> PhasePlan:
        stages: list[str] = []
        try:
            stages.append("draft_phase_skeleton")
            self._trace(trace, envelope=envelope, stage="draft_phase_skeleton", event="model_call_started", status="started", details={"schema": "PhaseSkeleton"})
            skeleton = PhaseSkeleton.model_validate_json(
                self._model_client.complete_json(
                    stage="draft_phase_skeleton",
                    prompt=self._skeleton_prompt(envelope),
                    schema=PhaseSkeleton.model_json_schema(),
                )
            )
            self._trace(trace, envelope=envelope, stage="draft_phase_skeleton", event="model_call_completed", status="completed", details={"phase_count": len(skeleton.phases)})

            stages.append("draft_artifact_contracts")
            self._trace(trace, envelope=envelope, stage="draft_artifact_contracts", event="model_call_started", status="started", details={"schema": "ArtifactContractBundle"})
            contracts = ArtifactContractBundle.model_validate_json(
                self._model_client.complete_json(
                    stage="draft_artifact_contracts",
                    prompt=self._artifact_contract_prompt(envelope=envelope, skeleton=skeleton),
                    schema=ArtifactContractBundle.model_json_schema(),
                )
            )
            self._trace(trace, envelope=envelope, stage="draft_artifact_contracts", event="model_call_completed", status="completed", details={"artifact_contract_count": len(contracts.artifact_contracts)})

            stages.append("draft_phase_plan")
            self._trace(trace, envelope=envelope, stage="draft_phase_plan", event="model_call_started", status="started", details={"schema": "PhasePlan"})
            draft_response = self._model_client.complete_json(
                stage="draft_phase_plan",
                prompt=self._phase_plan_prompt(envelope=envelope, skeleton=skeleton, contracts=contracts),
                schema=self._plan_schema,
            )
            self._trace(trace, envelope=envelope, stage="draft_phase_plan", event="model_call_completed", status="completed")
            plan = self._parse_validate_or_repair(
                envelope=envelope,
                response=draft_response,
                stages=stages,
                trace=trace,
            )
            return self._with_metadata(plan, stages=stages)
        except Exception as exc:
            self._trace(trace, envelope=envelope, stage=stages[-1] if stages else "draft_phase_skeleton", event="chain_failed", status="failed", details={"error": str(exc)})
            if isinstance(exc, PhasePlannerPromptChainError):
                raise
            raise PhasePlannerPromptChainError("AppV2 phase planner prompt chain failed") from exc

    def replan(self, *, envelope: Envelope, current_plan: PhasePlan, replan_request: PhaseReplanRequest, trace: RuntimeMatrixLogger | None = None) -> PhasePlan:
        stages = ["planner_replan"]
        self._trace(trace, envelope=envelope, stage="planner_replan", event="model_call_started", status="started", plan_id=current_plan.plan_id, details={"schema": "PhasePlan"})
        response = self._model_client.complete_json(
            stage="planner_replan",
            prompt=self._replan_prompt(envelope=envelope, current_plan=current_plan, replan_request=replan_request),
            schema=self._plan_schema,
        )
        self._trace(trace, envelope=envelope, stage="planner_replan", event="model_call_completed", status="completed", plan_id=current_plan.plan_id)
        try:
            plan = PhasePlan.model_validate_json(response)
            issues = self._validator.validate_phase_plan(
                plan,
                envelope=envelope,
                initial_artifact_ids=[artifact.id for artifact in replan_request.carryover_artifacts],
            )
            self._trace(trace, envelope=envelope, stage="planner_replan", event="validation_completed", status="completed", plan_id=plan.plan_id, details={"issue_count": len(issues), "blocking_issue_count": len(blocking(issues))})
            self._validator.raise_if_blocking(issues)
            return self._with_metadata(
                plan,
                stages=stages,
                extra={
                    "replan": True,
                    "parent_plan_id": current_plan.plan_id,
                    "failed_phase_id": replan_request.failed_phase_id,
                },
            )
        except (ValidationError, AppV2ValidationError) as exc:
            stages.append("repair_phase_replan")
            self._trace(trace, envelope=envelope, stage="repair_phase_replan", event="model_call_started", status="started", plan_id=current_plan.plan_id, details={"schema": "PhasePlan", "error": str(exc)[:500]})
            repair_response = self._model_client.complete_json(
                stage="repair_phase_replan",
                prompt=self._repair_prompt(envelope=envelope, previous_response=response, error=str(exc)),
                schema=self._plan_schema,
            )
            self._trace(trace, envelope=envelope, stage="repair_phase_replan", event="model_call_completed", status="completed", plan_id=current_plan.plan_id)
            plan = PhasePlan.model_validate_json(repair_response)
            issues = self._validator.validate_phase_plan(
                plan,
                envelope=envelope,
                initial_artifact_ids=[artifact.id for artifact in replan_request.carryover_artifacts],
            )
            self._trace(trace, envelope=envelope, stage="repair_phase_replan", event="validation_completed", status="completed", plan_id=plan.plan_id, details={"issue_count": len(issues), "blocking_issue_count": len(blocking(issues))})
            self._validator.raise_if_blocking(issues)
            return self._with_metadata(
                plan,
                stages=stages,
                extra={
                    "replan": True,
                    "parent_plan_id": current_plan.plan_id,
                    "failed_phase_id": replan_request.failed_phase_id,
                },
            )

    def _parse_validate_or_repair(self, *, envelope: Envelope, response: str, stages: list[str], trace: RuntimeMatrixLogger | None = None) -> PhasePlan:
        try:
            plan = PhasePlan.model_validate_json(response)
            issues = self._validator.validate_phase_plan(plan, envelope=envelope)
            self._trace(trace, envelope=envelope, stage="validate_phase_plan", event="validation_completed", status="completed", plan_id=plan.plan_id, details={"issue_count": len(issues), "blocking_issue_count": len(blocking(issues))})
            self._validator.raise_if_blocking(issues)
            return plan
        except (ValidationError, AppV2ValidationError) as exc:
            stages.append("repair_phase_plan")
            self._trace(trace, envelope=envelope, stage="repair_phase_plan", event="model_call_started", status="started", details={"schema": "PhasePlan", "error": str(exc)[:500]})
            repair_response = self._model_client.complete_json(
                stage="repair_phase_plan",
                prompt=self._repair_prompt(envelope=envelope, previous_response=response, error=str(exc)),
                schema=self._plan_schema,
            )
            self._trace(trace, envelope=envelope, stage="repair_phase_plan", event="model_call_completed", status="completed")
            plan = PhasePlan.model_validate_json(repair_response)
            issues = self._validator.validate_phase_plan(plan, envelope=envelope)
            self._trace(trace, envelope=envelope, stage="repair_phase_plan", event="validation_completed", status="completed", plan_id=plan.plan_id, details={"issue_count": len(issues), "blocking_issue_count": len(blocking(issues))})
            self._validator.raise_if_blocking(issues)
            return plan

    def _with_metadata(self, plan: PhasePlan, *, stages: list[str], extra: dict[str, Any] | None = None) -> PhasePlan:
        metadata = dict(plan.metadata)
        metadata["appv2_phase_planner"] = {
            "mode": "phase_prompt_chain",
            "stages": list(stages),
            "model_calls": len(stages),
            **(extra or {}),
        }
        return plan.model_copy(update={"metadata": metadata})

    def _skeleton_prompt(self, envelope: Envelope) -> str:
        payload = {
            "role": "appv2_phase_skeleton_planner",
            "system_prompt": PLANNER_SYSTEM_PROMPT,
            "prompt_contract": prompt_contract(PLANNER_STAGE_CONTRACTS["draft_phase_skeleton"]),
            "schema_contract": schema_prompt_summary(schema_name="PhaseSkeleton", schema=PhaseSkeleton.model_json_schema()),
            "allowed_phases": ["DISCOVER", "ANALYZE", "RESEARCH", "DESIGN", "MUTATE", "VERIFY", "FINALIZE"],
            "envelope": envelope.model_dump(mode="json"),
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _artifact_contract_prompt(self, *, envelope: Envelope, skeleton: PhaseSkeleton) -> str:
        payload = {
            "role": "appv2_artifact_contract_planner",
            "system_prompt": PLANNER_SYSTEM_PROMPT,
            "prompt_contract": prompt_contract(PLANNER_STAGE_CONTRACTS["draft_artifact_contracts"]),
            "schema_contract": schema_prompt_summary(schema_name="ArtifactContractBundle", schema=ArtifactContractBundle.model_json_schema()),
            "envelope": envelope.model_dump(mode="json"),
            "phase_skeleton": skeleton.model_dump(mode="json"),
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _phase_plan_prompt(
        self,
        *,
        envelope: Envelope,
        skeleton: PhaseSkeleton,
        contracts: ArtifactContractBundle,
    ) -> str:
        payload = {
            "role": "appv2_phase_plan_assembler",
            "system_prompt": PLANNER_SYSTEM_PROMPT,
            "prompt_contract": prompt_contract(PLANNER_STAGE_CONTRACTS["draft_phase_plan"]),
            "schema_contract": schema_prompt_summary(schema_name="PhasePlan", schema=self._plan_schema),
            "envelope": envelope.model_dump(mode="json"),
            "phase_skeleton": skeleton.model_dump(mode="json"),
            "artifact_contracts": contracts.model_dump(mode="json"),
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _repair_prompt(self, *, envelope: Envelope, previous_response: str, error: str) -> str:
        payload = {
            "role": "appv2_phase_plan_repair",
            "system_prompt": PLANNER_SYSTEM_PROMPT,
            "prompt_contract": prompt_contract(PLANNER_STAGE_CONTRACTS["repair_phase_plan"]),
            "schema_contract": schema_prompt_summary(schema_name="PhasePlan", schema=self._plan_schema),
            "envelope": envelope.model_dump(mode="json"),
            "validation_error": error[:4000],
            "previous_response": previous_response[:8000],
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _replan_prompt(
        self,
        *,
        envelope: Envelope,
        current_plan: PhasePlan,
        replan_request: PhaseReplanRequest,
    ) -> str:
        payload = {
            "role": "appv2_phase_replanner",
            "system_prompt": PLANNER_SYSTEM_PROMPT,
            "prompt_contract": prompt_contract(PLANNER_STAGE_CONTRACTS["planner_replan"]),
            "schema_contract": schema_prompt_summary(schema_name="PhasePlan", schema=self._plan_schema),
            "envelope": envelope.model_dump(mode="json"),
            "current_plan": current_plan.model_dump(mode="json"),
            "replan_request": replan_request.model_dump(mode="json"),
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _trace(
        self,
        trace: RuntimeMatrixLogger | None,
        *,
        envelope: Envelope,
        stage: str,
        event: str,
        status: str,
        plan_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if trace is None:
            return
        trace.record(
            component="appv2_phase_planner_chain",
            stage=stage,
            event=event,
            status=status,
            request_id=envelope.request_id,
            plan_id=plan_id,
            details=details,
        )
