"""Single AppV2 worker agent loop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from appV2.prompts import WORKER_PROMPT_CONTRACT, WORKER_SYSTEM_PROMPT, prompt_contract, schema_prompt_summary
from appV2.runtime_matrix import RuntimeMatrixLogger
from appV2.schemas import (
    ArtifactRecord,
    MutationProposal,
    PhaseOutputProposal,
    PhasePlan,
    PhaseStep,
    RuntimeResult,
    ToolCallProposal,
    ValidationIssue,
    WorkerDecision,
)
from appV2.validator import AppV2Validator, blocking
from appV2.worker.context import ContextController
from appV2.worker.ledgers import ArtifactLedger, MutationLedger, MutationRecord, snapshot_postimages, snapshot_preimages
from appV2.worker.policy_gate import PolicyGate
from appV2.worker.tools import ToolExecutionError, ToolRegistry
from appV2.worker.verification_gate import VerificationGate


@dataclass(frozen=True)
class PhaseRunOutcome:
    status: str
    summary: str
    issues: list[ValidationIssue]
    model_calls: int
    tool_calls: int
    replan_signal: dict[str, Any] | None = None


class WorkerModelClient:
    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:  # pragma: no cover - protocol shape
        raise NotImplementedError


class AgentLoop:
    def __init__(
        self,
        *,
        model_client: WorkerModelClient,
        tools: ToolRegistry,
        validator: AppV2Validator | None = None,
        verification_gate: VerificationGate | None = None,
        context_controller: ContextController | None = None,
    ) -> None:
        self._model_client = model_client
        self._tools = tools
        self._validator = validator or AppV2Validator()
        self._verification_gate = verification_gate or VerificationGate(validator=self._validator)
        self._context_controller = context_controller or ContextController()
        self._decision_schema = WorkerDecision.model_json_schema()

    def run_phase(
        self,
        *,
        envelope: Any,
        plan: PhasePlan,
        phase: PhaseStep,
        artifacts: ArtifactLedger,
        mutations: MutationLedger,
        retry_memory: dict[str, Any] | None = None,
        trace: RuntimeMatrixLogger | None = None,
    ) -> PhaseRunOutcome:
        observations: list[dict[str, Any]] = []
        model_calls = 0
        tool_calls = 0
        budget = _resolve_phase_budget(phase)
        max_model_calls = budget["effective_model_calls"]
        max_tool_calls = budget["effective_tool_calls"]
        self._trace(
            trace,
            plan=plan,
            phase=phase,
            event="loop_started",
            status="started",
            details=budget,
        )

        while model_calls < max_model_calls:
            frame = self._context_controller.build_phase_frame(
                envelope=envelope,
                plan=plan,
                phase=phase,
                artifacts=artifacts,
                mutations=mutations,
                tools=self._tools,
                retry_memory=retry_memory,
            )
            model_calls += 1
            self._trace(
                trace,
                plan=plan,
                phase=phase,
                event="model_call_started",
                status="started",
                attempt_id=f"turn_{model_calls}",
                details={"tool_calls_used": tool_calls, "pending_outputs": frame.pending_outputs},
            )
            try:
                raw_response = self._model_client.complete_json(
                    stage=f"appv2_worker_{phase.phase_id}",
                    prompt=self._prompt(frame=frame, observations=observations, model_calls=model_calls, tool_calls=tool_calls),
                    schema=self._decision_schema,
                )
                decision = WorkerDecision.model_validate(_normalize_worker_decision_payload(raw_response))
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="model_call_completed",
                    status="completed",
                    attempt_id=f"turn_{model_calls}",
                )
            except (ValidationError, ValueError) as exc:
                feedback = _decision_parse_feedback(exc)
                observation = self._feedback(
                    phase_id=phase.phase_id,
                    code="model_decision_invalid",
                    message=feedback["message"],
                    repairable=True,
                    metadata={
                        "remaining_model_calls": max_model_calls - model_calls,
                        **feedback["metadata"],
                    },
                )
                observations.append(observation.content)
                artifacts.append(observation)
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="model_decision_invalid",
                    status="failed",
                    attempt_id=f"turn_{model_calls}",
                    details={"error": str(exc)},
                )
                continue

            issues = self._validator.validate_worker_decision(decision, phase=phase)
            self._trace(
                trace,
                plan=plan,
                phase=phase,
                event="loop_decision",
                status="completed",
                attempt_id=f"turn_{model_calls}",
                details={"branch": _decision_branch(decision), "issue_count": len(issues)},
            )
            if blocking(issues):
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="decision_blocked",
                    status="blocked",
                    attempt_id=f"turn_{model_calls}",
                    details={"issues": [issue.code for issue in blocking(issues)]},
                )
                return PhaseRunOutcome(
                    status="blocked",
                    summary="Worker decision failed blocking validation.",
                    issues=blocking(issues),
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                )
            if issues:
                observation = self._feedback(
                    phase_id=phase.phase_id,
                    code="worker_decision_validation_failed",
                    message="Worker decision failed repairable validation.",
                    repairable=True,
                    metadata={"issues": [issue.model_dump(mode="json") for issue in issues]},
                )
                observations.append(observation.content)
                artifacts.append(observation)
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="decision_repair_requested",
                    status="repairable",
                    attempt_id=f"turn_{model_calls}",
                    details={"issues": [issue.code for issue in issues]},
                )
                continue

            if decision.tool_calls:
                if tool_calls >= max_tool_calls:
                    observation = self._feedback(
                        phase_id=phase.phase_id,
                        code="tool_budget_exceeded",
                        message="Tool budget is exhausted for this phase.",
                        repairable=False,
                    )
                    observations.append(observation.content)
                    artifacts.append(observation)
                    self._trace(
                        trace,
                        plan=plan,
                        phase=phase,
                        event="tool_budget_exceeded",
                        status="blocked",
                        attempt_id=f"turn_{model_calls}",
                    )
                    continue
                for proposal in decision.tool_calls:
                    if tool_calls >= max_tool_calls:
                        observations.append({"code": "tool_budget_exceeded", "message": "No tool calls remain."})
                        break
                    tool_calls += 1
                    self._trace(
                        trace,
                        plan=plan,
                        phase=phase,
                        event="tool_call_started",
                        status="started",
                        attempt_id=proposal.call_id,
                        details={"tool_name": proposal.tool_name, "purpose": proposal.purpose},
                    )
                    observation = self._execute_tool(phase=phase, proposal=proposal)
                    observations.append(observation.content)
                    artifacts.append(observation)
                    self._trace(
                        trace,
                        plan=plan,
                        phase=phase,
                        event="tool_call_completed",
                        status=str(observation.content.get("status") or "completed"),
                        attempt_id=proposal.call_id,
                        details={"tool_name": proposal.tool_name, "feedback_code": observation.content.get("code")},
                    )
                continue

            if decision.mutation is not None:
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="mutation_started",
                    status="started",
                    attempt_id=decision.mutation.operation_batch_id,
                    details={"operation_count": len(decision.mutation.operations)},
                )
                observation = self._execute_mutation(
                    phase=phase,
                    proposal=decision.mutation,
                    artifacts=artifacts,
                    mutations=mutations,
                )
                observations.append(observation.content)
                artifacts.append(observation)
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="mutation_completed",
                    status=str(observation.content.get("status") or "completed"),
                    attempt_id=decision.mutation.operation_batch_id,
                    details={"feedback_code": observation.content.get("code"), "touched_paths": observation.content.get("touched_paths")},
                )
                continue

            if decision.final_phase_output is not None:
                issues = self._verification_gate.validate_phase_output(
                    phase=phase,
                    output=decision.final_phase_output,
                    evidence=artifacts.evidence(),
                )
                if issues:
                    observation = self._feedback(
                        phase_id=phase.phase_id,
                        code="phase_output_validation_failed",
                        message="Final phase output failed validation; repair and resend final_phase_output.",
                        repairable=True,
                        metadata={"issues": [issue.model_dump(mode="json") for issue in issues]},
                    )
                    observations.append(observation.content)
                    artifacts.append(observation)
                    self._trace(
                        trace,
                        plan=plan,
                        phase=phase,
                        event="final_output_validation_failed",
                        status="repairable",
                        attempt_id=f"turn_{model_calls}",
                        details={"issues": [issue.code for issue in issues]},
                    )
                    continue
                for artifact in decision.final_phase_output.artifacts:
                    artifacts.append(
                        artifact.model_copy(
                            update={
                                "phase_id": artifact.phase_id or phase.phase_id,
                                "producer": artifact.producer or "appv2_worker",
                                "lifecycle": "completed",
                            }
                        )
                    )
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="phase_output_completed",
                    status=decision.final_phase_output.status,
                    attempt_id=f"turn_{model_calls}",
                    details={"artifact_ids": [artifact.id for artifact in decision.final_phase_output.artifacts]},
                )
                return PhaseRunOutcome(
                    status=decision.final_phase_output.status,
                    summary=decision.final_phase_output.summary,
                    issues=[],
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                )

            if decision.planner_replan_signal is not None:
                self._trace(
                    trace,
                    plan=plan,
                    phase=phase,
                    event="planner_replan_signal_emitted",
                    status="needs_replan",
                    attempt_id=f"turn_{model_calls}",
                    details={"issue_codes": decision.planner_replan_signal.issue_codes},
                )
                return PhaseRunOutcome(
                    status="needs_replan",
                    summary=decision.planner_replan_signal.reason,
                    issues=[],
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    replan_signal=decision.planner_replan_signal.model_dump(mode="json"),
                )

        self._trace(
            trace,
            plan=plan,
            phase=phase,
            event="model_budget_exceeded",
            status="budget_exceeded",
            details={"max_model_calls": max_model_calls, "tool_calls_used": tool_calls},
        )
        return PhaseRunOutcome(
            status="budget_exceeded",
            summary=f"Phase {phase.phase_id} exhausted model budget before valid completion.",
            issues=[
                ValidationIssue(
                    owner="worker",
                    severity="blocking",
                    code="model_budget_exceeded",
                    message="Worker phase exceeded max_model_calls",
                    metadata={"phase_id": phase.phase_id, "max_model_calls": max_model_calls},
                )
            ],
            model_calls=model_calls,
            tool_calls=tool_calls,
        )

    def _execute_tool(self, *, phase: PhaseStep, proposal: ToolCallProposal) -> ArtifactRecord:
        policy = PolicyGate(root_path=self._tools.root).validate_tool_call(phase=phase, proposal=proposal)
        if not policy.allowed:
            return self._feedback(
                phase_id=phase.phase_id,
                code=policy.code,
                message=policy.message,
                repairable=policy.repairable,
                metadata={"tool_name": proposal.tool_name, **policy.metadata},
            )
        try:
            result = self._tools.execute(phase=phase, tool_name=proposal.tool_name, arguments=proposal.arguments)
        except (ToolExecutionError, OSError, ValueError, ValidationError) as exc:
            return self._feedback(
                phase_id=phase.phase_id,
                code="tool_execution_failed",
                message=str(exc),
                repairable=True,
                metadata={"tool_name": proposal.tool_name},
            )
        trust = "runtime_verified" if result.get("status") == "passed" else "tool_observed"
        if result.get("status") == "denied":
            return self._feedback(
                phase_id=phase.phase_id,
                code=str(result.get("code") or "tool_denied"),
                message=str(result.get("message") or "Tool call denied"),
                repairable=bool(result.get("repairable", True)),
                metadata={"tool_name": proposal.tool_name, "tool_result": result},
            )
        return ArtifactRecord(
            id=f"tool_{phase.phase_id}_{proposal.call_id}",
            kind="tool_observation",
            content={"tool_name": proposal.tool_name, "arguments": proposal.arguments, "result": result},
            producer=f"tool:{proposal.tool_name}",
            phase_id=phase.phase_id,
            trust_level=trust,
            lifecycle="observation",
        )

    def _execute_mutation(
        self,
        *,
        phase: PhaseStep,
        proposal: MutationProposal,
        artifacts: ArtifactLedger,
        mutations: MutationLedger,
    ) -> ArtifactRecord:
        policy = PolicyGate(root_path=self._tools.root).validate_mutation(phase=phase, operations=proposal.operations)
        if not policy.allowed:
            return self._feedback(
                phase_id=phase.phase_id,
                code=policy.code,
                message=policy.message,
                repairable=policy.repairable,
                metadata=policy.metadata,
            )
        try:
            preimages = snapshot_preimages(self._tools.root, proposal.operations)
            result = self._tools.apply_operations(phase=phase, operations=proposal.operations)
        except (OSError, ToolExecutionError, UnicodeDecodeError) as exc:
            return self._feedback(
                phase_id=phase.phase_id,
                code="mutation_execution_failed",
                message=str(exc),
                repairable=True,
                metadata={
                    "operation_batch_id": proposal.operation_batch_id,
                    "operation_count": len(proposal.operations),
                },
            )
        if result.get("status") == "denied":
            return self._feedback(
                phase_id=phase.phase_id,
                code=str(result.get("code") or "mutation_denied"),
                message=str(result.get("message") or "Mutation denied"),
                repairable=bool(result.get("repairable", True)),
                metadata={"tool_result": result},
            )
        touched_paths = list(result.get("touched_paths") or [])
        try:
            postimages = snapshot_postimages(self._tools.root, touched_paths)
        except (OSError, UnicodeDecodeError) as exc:
            return self._feedback(
                phase_id=phase.phase_id,
                code="mutation_snapshot_failed",
                message=str(exc),
                repairable=True,
                metadata={
                    "operation_batch_id": proposal.operation_batch_id,
                    "touched_paths": touched_paths,
                },
            )
        record = MutationRecord(
            operation_batch_id=proposal.operation_batch_id,
            phase_id=phase.phase_id,
            proposed_operations=proposal.operations,
            applied_operations=[operation.model_dump(mode="json") for operation in proposal.operations],
            preimages=preimages,
            postimages=postimages,
            touched_paths=touched_paths,
        )
        mutations.append(record)
        artifact = record.to_artifact()
        artifacts.append(artifact)
        return ArtifactRecord(
            id=f"mutation_observation_{proposal.operation_batch_id}",
            kind="tool_observation",
            content={"status": "completed", "mutation_artifact_id": artifact.id, "touched_paths": touched_paths},
            producer="appv2_worker_mutation",
            phase_id=phase.phase_id,
            trust_level="runtime_verified",
            lifecycle="observation",
        )

    def _feedback(
        self,
        *,
        phase_id: str,
        code: str,
        message: str,
        repairable: bool,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        return ArtifactRecord(
            id=f"feedback_{phase_id}_{code}",
            kind="tool_observation",
            content={
                "status": "failed",
                "code": code,
                "message": message,
                "repairable": repairable,
                "next_action": "Repair the next WorkerDecision using this feedback." if repairable else "Stop or return blocked.",
                **(metadata or {}),
            },
            producer="appv2_worker_feedback_loop",
            phase_id=phase_id,
            trust_level="runtime_verified",
            lifecycle="observation",
        )

    def _prompt(self, *, frame: Any, observations: list[dict[str, Any]], model_calls: int, tool_calls: int) -> str:
        budget = _render_prompt_budget(frame.phase, model_calls=model_calls, tool_calls=tool_calls)
        payload = {
            "system_prompt": WORKER_SYSTEM_PROMPT,
            "prompt_contract": prompt_contract(WORKER_PROMPT_CONTRACT),
            "schema_contract": schema_prompt_summary(schema_name="WorkerDecision", schema=self._decision_schema),
            "phase_frame": frame.__dict__,
            "budget": budget,
            "budget_pressure": _budget_pressure(budget),
            "runtime_authority": {
                "llm_role": "propose one WorkerDecision branch",
                "runtime_role": "validate schema, execute tools, apply policy gates, mutate files, verify evidence, own final status",
                "planner_replan_rule": "only semantic planner-quality failures; never ordinary tool/model/budget/policy repair failures",
            },
            "feedback_summary": _feedback_summary(observations[-6:]),
            "feedback_observations": observations[-6:],
            "tool_call_contract": {
                "top_level_rule": "Never place call_id, tool_name, arguments, or purpose at the top level. Put tool calls inside tool_calls: [ ... ].",
                "purpose_rule": "purpose should be short, concrete, and under 20 words.",
                "argument_rule": "arguments must be real JSON values that match available_tools[*].parameters, not JSON-encoded strings or schema descriptions.",
            },
            "decision_examples": {
                "tool_calls_shape": {"tool_calls": [{"call_id": "read_target", "tool_name": "read_file", "arguments": {"path": "src/app.py"}, "purpose": "inspect current implementation"}]},
                "mutation_shape": {"mutation": {"operation_batch_id": "apply_fix", "operations": [{"action": "replace", "path": "src/app.py", "old": "before", "new": "after"}], "reason": "apply bounded fix", "expected_artifacts": ["change_summary"]}},
                "final_shape": {
                    "final_phase_output": {
                        "status": "completed",
                        "summary": "phase complete with evidence",
                        "artifacts": [
                            {
                                "id": "example_output_artifact",
                                "kind": "phase_output",
                                "content": {"evidence": ["tool-backed fact"], "result": "succinct structured payload"},
                                "producer": "appv2_worker_model",
                                "trust_level": "model_reported",
                                "lifecycle": "completed",
                            }
                        ],
                    }
                },
                "replan_shape": {"planner_replan_signal": {"reason": "required input artifact was never produced by any previous phase", "phase_id": frame.phase.get("phase_id"), "issue_codes": ["missing_required_input_artifact"], "recommended_action": "repair phase artifact ordering"}},
            },
            "artifact_record_rules": {
                "allowed_top_level_fields": ["id", "kind", "content", "producer", "phase_id", "trust_level", "lifecycle", "metadata"],
                "forbidden_top_level_fields": ["summary", "status", "required", "schema"],
                "invalid_lifecycle_values": ["output"],
                "artifact_summary_rule": "Put narrative summary in final_phase_output.summary or inside artifact.content, never at artifact top level.",
            },
        }
        return json.dumps(payload, indent=2, sort_keys=True, default=str)

    def _trace(
        self,
        trace: RuntimeMatrixLogger | None,
        *,
        plan: PhasePlan,
        phase: PhaseStep,
        event: str,
        status: str,
        attempt_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if trace is None:
            return
        trace.record(
            component="appv2_worker_loop",
            stage=phase.phase,
            event=event,
            status=status,
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            step_id=phase.phase_id,
            attempt_id=attempt_id,
            details=details,
        )


def _feedback_summary(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, dict):
            summary.append({"status": "unknown", "message": str(observation)[:500]})
            continue
        item: dict[str, Any] = {
            "status": observation.get("status"),
            "code": observation.get("code"),
            "message": observation.get("message"),
            "repairable": observation.get("repairable"),
            "next_action": observation.get("next_action"),
        }
        for key in ("tool_name", "issues", "tool_result", "remaining_model_calls"):
            if key in observation:
                item[key] = observation[key]
        summary.append({key: value for key, value in item.items() if value is not None})
    return summary


def _normalize_worker_decision_payload(raw_response: str) -> dict[str, Any]:
    payload = json.loads(raw_response)
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("WorkerDecision response must decode to a JSON object")

    normalized = _normalize_marshaled_node(dict(payload))
    if not isinstance(normalized, dict):
        raise ValueError("WorkerDecision response must normalize to a JSON object")
    if not any(key in normalized for key in ("tool_calls", "mutation", "final_phase_output", "planner_replan_signal")):
        if _looks_like_tool_call(normalized):
            normalized = {"tool_calls": [normalized]}
        elif _looks_like_mutation(normalized):
            normalized = {"mutation": normalized}
        elif _looks_like_final_output(normalized):
            normalized = {"final_phase_output": normalized}
        elif _looks_like_replan_signal(normalized):
            normalized = {"planner_replan_signal": normalized}
    for key in ("final_phase_output", "planner_replan_signal", "mutation"):
        normalized[key] = _maybe_parse_nested_json(normalized.get(key), expected_type=dict)
        normalized[key] = _normalize_marshaled_node(normalized.get(key))
    normalized["tool_calls"] = _maybe_parse_nested_json(normalized.get("tool_calls"), expected_type=list)
    normalized["tool_calls"] = _normalize_marshaled_node(normalized.get("tool_calls"))
    if isinstance(normalized.get("tool_calls"), dict) and _looks_like_tool_call(normalized["tool_calls"]):
        normalized["tool_calls"] = [normalized["tool_calls"]]
    return normalized


def _render_prompt_budget(phase: dict[str, Any], *, model_calls: int, tool_calls: int) -> dict[str, int]:
    budget = _resolve_phase_budget_dict(phase)
    return {
        **budget,
        "model_calls_used_including_this_turn": model_calls,
        "tool_calls_used": tool_calls,
        "remaining_model_calls_after_this_turn": max(0, budget["effective_model_calls"] - model_calls),
        "remaining_tool_calls": max(0, budget["effective_tool_calls"] - tool_calls),
    }


def _budget_pressure(budget: dict[str, int]) -> dict[str, Any] | None:
    remaining_model_calls = budget["remaining_model_calls_after_this_turn"]
    remaining_tool_calls = budget["remaining_tool_calls"]
    if remaining_model_calls <= 0:
        return {
            "level": "critical",
            "message": "No model turns remain after this turn. Return the smallest honest completion or blocked outcome.",
        }
    if remaining_model_calls == 1 or remaining_tool_calls == 0:
        return {
            "level": "warning",
            "message": "Budget is nearly exhausted. Consolidate, avoid new exploration, and prefer a valid completion if evidence is sufficient.",
        }
    if remaining_model_calls == 2:
        return {
            "level": "caution",
            "message": "Budget is tightening. Use only targeted repair or the smallest necessary tool call.",
        }
    return None


def _maybe_parse_nested_json(value: Any, *, expected_type: type[dict[str, Any]] | type[list[Any]]) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate:
        return value
    opener = "{" if expected_type is dict else "["
    closer = "}" if expected_type is dict else "]"
    if not (candidate.startswith(opener) and candidate.endswith(closer)):
        return value
    parsed = json.loads(candidate)
    if isinstance(parsed, expected_type):
        return parsed
    return value


def _normalize_marshaled_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_marshaled_node(item) for item in value]
    if not isinstance(value, dict):
        return value

    node_type = value.get("type")
    entries = value.get("entries")
    if node_type == "Object" and isinstance(entries, list):
        normalized: dict[str, Any] = {}
        for entry in entries:
            if isinstance(entry, list) and len(entry) == 2:
                key, nested = entry
                normalized[str(key)] = _normalize_marshaled_node(nested)
        if "completionState" in value and "status" not in normalized:
            normalized["status"] = _normalize_completion_state(value.get("completionState"))
        for key, nested in value.items():
            if key in {"type", "entries", "completionState"}:
                continue
            normalized.setdefault(str(key), _normalize_marshaled_node(nested))
        return normalized
    if node_type == "Array" and isinstance(entries, list):
        items: list[Any] = []
        for entry in entries:
            if isinstance(entry, list) and len(entry) == 2:
                maybe_index, nested = entry
                if isinstance(maybe_index, int) or (isinstance(maybe_index, str) and maybe_index.isdigit()):
                    items.append(_normalize_marshaled_node(nested))
                    continue
            items.append(_normalize_marshaled_node(entry))
        return items
    return {str(key): _normalize_marshaled_node(nested) for key, nested in value.items()}


def _normalize_completion_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"complete", "completed", "success", "done"}:
        return "completed"
    if text in {"blocked"}:
        return "blocked"
    if text in {"failed", "failure", "error"}:
        return "failed"
    if text in {"needs_replan", "replan"}:
        return "needs_replan"
    return text or "completed"


def _decision_parse_feedback(exc: ValidationError | ValueError) -> dict[str, Any]:
    if not isinstance(exc, ValidationError):
        message = str(exc).strip() or "WorkerDecision was invalid."
        return {
            "message": f"{message} Return exactly one valid WorkerDecision branch.",
            "metadata": {"repair_hints": ["Return one JSON object with exactly one branch: tool_calls, mutation, final_phase_output, or planner_replan_signal."]},
        }

    hints: list[str] = []
    invalid_fields: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ()))
        msg = str(error.get("msg") or "")
        if "exactly one action branch" in msg:
            hints.append("Return exactly one branch: tool_calls, mutation, final_phase_output, or planner_replan_signal.")
            continue
        if loc in {"call_id", "tool_name", "arguments", "purpose"}:
            hints.append("Tool call fields appeared at the top level. Wrap them inside tool_calls: [{call_id, tool_name, arguments, purpose}].")
            invalid_fields.append(loc)
            continue
        if loc.startswith("tool_calls") and "list" in msg.lower():
            hints.append("tool_calls must be a JSON array, even for a single tool call.")
            invalid_fields.append(loc)
            continue
        if loc.startswith("final_phase_output"):
            hints.append("final_phase_output must use {status, summary, artifacts, optional issues, optional metadata}.")
            invalid_fields.append(loc)
            continue
        if loc.startswith("mutation"):
            hints.append("mutation must use {operation_batch_id, operations, reason, optional expected_artifacts}.")
            invalid_fields.append(loc)
            continue
        if loc.startswith("planner_replan_signal"):
            hints.append("planner_replan_signal must use {reason, phase_id, issue_codes, optional recommended_action, optional metadata}.")
            invalid_fields.append(loc)
            continue
        if loc:
            invalid_fields.append(loc)

    if not hints:
        hints.append("Return a smaller schema-compliant WorkerDecision JSON object.")
    message = " ".join(dict.fromkeys(hints))
    return {
        "message": message,
        "metadata": {
            "invalid_fields": sorted(set(invalid_fields)),
            "repair_hints": list(dict.fromkeys(hints)),
        },
    }


def _decision_branch(decision: WorkerDecision) -> str:
    if decision.tool_calls:
        return "tool_calls"
    if decision.mutation is not None:
        return "mutation"
    if decision.final_phase_output is not None:
        return "final_phase_output"
    if decision.planner_replan_signal is not None:
        return "planner_replan_signal"
    return "unknown"


def _looks_like_tool_call(payload: dict[str, Any]) -> bool:
    return {"call_id", "tool_name", "arguments"}.issubset(payload.keys())


def _looks_like_mutation(payload: dict[str, Any]) -> bool:
    return {"operation_batch_id", "operations", "reason"}.issubset(payload.keys())


def _looks_like_final_output(payload: dict[str, Any]) -> bool:
    return "artifacts" in payload and ("status" in payload or "summary" in payload)


def _looks_like_replan_signal(payload: dict[str, Any]) -> bool:
    return {"reason", "phase_id"}.issubset(payload.keys()) and "issue_codes" in payload


def _resolve_phase_budget(phase: PhaseStep) -> dict[str, int]:
    return _resolve_phase_budget_dict(phase.model_dump(mode="json"))


def _resolve_phase_budget_dict(phase: dict[str, Any]) -> dict[str, int]:
    phase_model_call_cap = 3
    configured_model_calls = max(0, int(phase.get("max_model_calls", 1) or 0))
    configured_tool_calls = max(0, int(phase.get("max_tool_calls", 0) or 0))
    uses_tools = bool(phase.get("allowed_tool_groups"))
    repair_turn_reserve = 1
    retry_turn_reserve = 1 if uses_tools else 0
    effective_model_calls = min(phase_model_call_cap, max(1, configured_model_calls, 1 + repair_turn_reserve + retry_turn_reserve))
    effective_tool_calls = max(configured_tool_calls, 1 if uses_tools else 0)
    return {
        "configured_model_calls": configured_model_calls,
        "configured_tool_calls": configured_tool_calls,
        "phase_model_call_cap": phase_model_call_cap,
        "repair_turn_reserve": repair_turn_reserve,
        "retry_turn_reserve": retry_turn_reserve,
        "effective_model_calls": effective_model_calls,
        "effective_tool_calls": effective_tool_calls,
    }
