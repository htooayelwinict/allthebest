"""Unified deterministic validation for AppV2 runtimes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from appV2.schemas import (
    ArtifactContract,
    ArtifactRecord,
    Envelope,
    MutationProposal,
    PhaseName,
    PhaseOutputProposal,
    PhasePlan,
    PhaseStep,
    RuntimeResult,
    ToolCallProposal,
    ValidationIssue,
    WorkerDecision,
)


PHASE_ORDER: tuple[PhaseName, ...] = ("DISCOVER", "ANALYZE", "RESEARCH", "DESIGN", "MUTATE", "VERIFY", "FINALIZE")
PHASE_INDEX = {phase: index for index, phase in enumerate(PHASE_ORDER)}
RUNTIME_SCOPE_INPUT_IDS: frozenset[str] = frozenset({"request_envelope"})
WRITE_TOOL_NAMES = {
    "write_file",
    "write_many_files",
    "replace_in_file",
    "apply_file_operations",
    "move_file",
    "delete_file",
    "write_json_manifest",
}


class AppV2ValidationError(ValueError):
    """Raised when blocking validation issues are requested as an exception."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        joined = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        super().__init__(joined or "AppV2 validation failed")


class AppV2Validator:
    """One deterministic validator used by decomposer, planner, and worker."""

    def validate_envelope(self, envelope: Envelope) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not envelope.request_id.strip():
            issues.append(_issue("decomposer", "blocking", "empty_request_id", "Envelope request_id is required"))
        if not envelope.normalized_input.strip():
            issues.append(_issue("decomposer", "blocking", "empty_normalized_input", "Envelope normalized_input is required"))
        if not envelope.input_type.strip():
            issues.append(_issue("decomposer", "blocking", "empty_input_type", "Envelope input_type is required"))
        if not 0 <= float(envelope.confidence) <= 1:
            issues.append(_issue("decomposer", "blocking", "invalid_confidence", "Envelope confidence must be between 0 and 1"))
        forbidden_metadata = {"phases", "phase_plan", "worker_type", "worker_types", "budget"}
        leaked = sorted(key for key in forbidden_metadata if key in envelope.metadata)
        if leaked:
            issues.append(
                _issue(
                    "decomposer",
                    "blocking",
                    "decomposer_boundary_leak",
                    "Envelope metadata must not contain planner or worker fields",
                    metadata={"keys": leaked},
                )
            )
        return issues

    def validate_phase_plan(
        self,
        plan: PhasePlan,
        *,
        envelope: Envelope | None = None,
        initial_artifact_ids: Iterable[str] | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if envelope is not None and plan.request_id != envelope.request_id:
            issues.append(_issue("planner", "blocking", "request_id_mismatch", "PhasePlan request_id must match Envelope"))
        if not plan.phases:
            issues.append(_issue("planner", "blocking", "empty_phase_plan", "PhasePlan must contain at least one phase"))
            return issues

        phase_ids = [phase.phase_id for phase in plan.phases]
        if len(phase_ids) != len(set(phase_ids)):
            issues.append(_issue("planner", "blocking", "duplicate_phase_id", "Phase phase_id values must be unique"))

        last_index = -1
        produced = set(initial_artifact_ids or [])
        produced.update(runtime_scope_input_ids(envelope, plan=plan))
        produced_by: dict[str, str] = {}
        saw_mutate = False
        mutation_phase_index: int | None = None
        verify_after_mutate = False

        for index, phase in enumerate(plan.phases):
            issues.extend(self.validate_phase_step(phase, path=f"phases[{index}]"))
            phase_index = PHASE_INDEX[phase.phase]
            if phase_index < last_index:
                issues.append(
                    _issue(
                        "planner",
                        "blocking",
                        "phase_order_regression",
                        "PhasePlan phases must follow DISCOVER -> ANALYZE -> RESEARCH -> DESIGN -> MUTATE -> VERIFY -> FINALIZE order",
                        path=f"phases[{index}].phase",
                    )
                )
            last_index = max(last_index, phase_index)

            for artifact_id in phase.input_artifacts:
                if artifact_id not in produced:
                    issues.append(
                        _issue(
                            "planner",
                            "blocking",
                            "missing_artifact_producer",
                            f"Phase {phase.phase_id} requires artifact '{artifact_id}' before it is produced",
                            path=f"phases[{index}].input_artifacts",
                            metadata={"artifact_id": artifact_id},
                        )
                    )

            if phase.phase == "MUTATE":
                saw_mutate = True
                mutation_phase_index = index
                if phase.mutation_policy is None:
                    issues.append(
                        _issue(
                            "planner",
                            "blocking",
                            "mutation_policy_required",
                            "MUTATE phase requires mutation_policy",
                            path=f"phases[{index}].mutation_policy",
                        )
                    )
                if "file_write" not in phase.allowed_tool_groups:
                    issues.append(
                        _issue(
                            "planner",
                            "blocking",
                            "mutation_requires_file_write_group",
                            "MUTATE phase must allow file_write tools",
                            path=f"phases[{index}].allowed_tool_groups",
                        )
                    )
            if phase.phase == "VERIFY":
                if phase.verification_policy is None:
                    issues.append(
                        _issue(
                            "planner",
                            "blocking",
                            "verification_policy_required",
                            "VERIFY phase requires verification_policy",
                            path=f"phases[{index}].verification_policy",
                        )
                    )
                if mutation_phase_index is not None and index > mutation_phase_index:
                    verify_after_mutate = True

            for artifact_id in phase.output_artifacts:
                produced.add(artifact_id)
                produced_by.setdefault(artifact_id, phase.phase_id)

        if saw_mutate and not verify_after_mutate:
            issues.append(
                _issue(
                    "planner",
                    "blocking",
                    "mutation_requires_verify_after",
                    "Any MUTATE phase must be followed by a VERIFY phase",
                )
            )

        contract_ids = {contract.id for contract in plan.artifact_contracts}
        for contract in plan.artifact_contracts:
            if _is_runtime_scope_contract(contract) and contract.produced_by_phase is not None:
                issues.append(
                    _issue(
                        "planner",
                        "blocking",
                        "runtime_scope_input_has_producer",
                        f"Runtime scope input '{contract.id}' must not declare produced_by_phase.",
                        metadata={"artifact_id": contract.id, "produced_by_phase": contract.produced_by_phase},
                    )
                )
        planned_outputs = {artifact_id for phase in plan.phases for artifact_id in phase.output_artifacts}
        for artifact_id in planned_outputs:
            if artifact_id not in contract_ids:
                issues.append(
                    _issue(
                        "planner",
                        "warning",
                        "missing_artifact_contract",
                        f"Output artifact '{artifact_id}' has no artifact contract",
                        metadata={"artifact_id": artifact_id},
                    )
                )
        return issues

    def validate_phase_step(self, phase: PhaseStep, *, path: str | None = None) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not phase.phase_id.strip():
            issues.append(_issue("planner", "blocking", "empty_phase_id", "phase_id is required", path=path))
        if not phase.goal.strip():
            issues.append(_issue("planner", "blocking", "empty_phase_goal", "phase goal is required", path=path))
        if phase.max_tool_calls < 0:
            issues.append(_issue("planner", "blocking", "negative_tool_budget", "max_tool_calls must be non-negative", path=path))
        if phase.max_model_calls < 0:
            issues.append(_issue("planner", "blocking", "negative_model_budget", "max_model_calls must be non-negative", path=path))
        if phase.max_model_calls > 3:
            issues.append(
                _issue(
                    "planner",
                    "blocking",
                    "phase_model_budget_exceeds_cap",
                    "max_model_calls must be 3 or less per phase",
                    path=f"{path}.max_model_calls" if path else None,
                    metadata={"phase_id": phase.phase_id, "max_model_calls": phase.max_model_calls, "phase_cap": 3},
                )
            )
        if phase.allowed_tool_groups and phase.max_model_calls < 2:
            issues.append(
                _issue(
                    "planner",
                    "blocking",
                    "insufficient_model_repair_budget",
                    "Tool-using phases must reserve at least 2 model calls for one repair turn.",
                    path=f"{path}.max_model_calls" if path else None,
                    metadata={"phase_id": phase.phase_id, "allowed_tool_groups": list(phase.allowed_tool_groups)},
                )
            )
        if phase.phase == "VERIFY" and "verify" not in phase.allowed_tool_groups:
            issues.append(
                _issue(
                    "planner",
                    "blocking",
                    "verify_requires_verify_tool_group",
                    "VERIFY phase must allow verify tools",
                    path=f"{path}.allowed_tool_groups" if path else None,
                )
            )
        return issues

    def validate_artifact_record(self, artifact: ArtifactRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not artifact.id.strip():
            issues.append(_issue("worker", "blocking", "empty_artifact_id", "Artifact id is required"))
        if artifact.content is None:
            issues.append(_issue("worker", "repairable", "empty_artifact_content", "Artifact content must not be null"))
        if isinstance(artifact.content, str) and not artifact.content.strip():
            issues.append(_issue("worker", "repairable", "empty_artifact_content", "Artifact content must not be empty"))
        return issues

    def validate_worker_decision(self, decision: WorkerDecision, phase: PhaseStep | None = None) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if decision.tool_calls:
            for index, call in enumerate(decision.tool_calls):
                issues.extend(self.validate_tool_call_proposal(call, phase=phase, path=f"tool_calls[{index}]"))
        if decision.mutation is not None:
            issues.extend(self.validate_mutation_proposal(decision.mutation, phase=phase))
        return issues

    def validate_tool_call_proposal(
        self,
        proposal: ToolCallProposal,
        *,
        phase: PhaseStep | None = None,
        path: str | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not proposal.tool_name.strip():
            issues.append(_issue("worker", "blocking", "empty_tool_name", "Tool call requires tool_name", path=path))
        return issues

    def validate_mutation_proposal(
        self,
        proposal: MutationProposal,
        *,
        phase: PhaseStep | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not proposal.operations:
            issues.append(_issue("worker", "repairable", "empty_mutation_proposal", "MutationProposal requires operations"))
        if phase is not None and phase.phase != "MUTATE":
            issues.append(_issue("policy_gate", "repairable", "mutation_outside_mutate_phase", "Mutation proposals are only allowed in MUTATE phases"))
        if phase is not None and phase.mutation_policy is None:
            issues.append(_issue("policy_gate", "blocking", "mutation_policy_missing", "Mutation proposal requires phase mutation_policy"))
        return issues

    def validate_phase_output(self, output: PhaseOutputProposal, *, phase: PhaseStep) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        by_id = {artifact.id: artifact for artifact in output.artifacts}
        if output.status == "completed":
            for artifact_id in phase.output_artifacts:
                if artifact_id not in by_id:
                    issues.append(
                        _issue(
                            "worker",
                            "repairable",
                            "missing_phase_output_artifact",
                            f"Completed phase output is missing artifact '{artifact_id}'",
                            metadata={"artifact_id": artifact_id, "phase_id": phase.phase_id},
                        )
                    )
            for artifact in output.artifacts:
                issues.extend(self.validate_artifact_record(artifact))
        return issues

    def validate_verification_evidence(
        self,
        *,
        phase: PhaseStep,
        output: PhaseOutputProposal,
        evidence: list[ArtifactRecord],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if phase.phase != "VERIFY" or output.status != "completed":
            return issues
        policy = phase.verification_policy
        if policy and policy.require_evidence:
            has_runtime_evidence = any(
                artifact.kind in {"tool_observation", "verification_evidence"} and artifact.trust_level in {"tool_observed", "runtime_verified"}
                for artifact in evidence
            )
            if not has_runtime_evidence:
                issues.append(
                    _issue(
                        "verification_gate",
                        "blocking",
                        "verification_missing_runtime_evidence",
                        "VERIFY phase cannot pass without tool or runtime verification evidence",
                        metadata={"phase_id": phase.phase_id},
                    )
                )
        return issues

    def validate_final_result(self, result: RuntimeResult) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if result.status == "completed" and not result.artifacts:
            issues.append(_issue("kernel", "warning", "completed_without_artifacts", "Completed result has no artifacts"))
        return issues

    def raise_if_blocking(self, issues: list[ValidationIssue]) -> None:
        blocking = [issue for issue in issues if issue.severity == "blocking"]
        if blocking:
            raise AppV2ValidationError(blocking)


def blocking(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [issue for issue in issues if issue.severity == "blocking"]


def repairable(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [issue for issue in issues if issue.severity == "repairable"]


def runtime_scope_input_ids(envelope: Envelope | None, *, plan: PhasePlan | None = None) -> set[str]:
    """Return runtime-supplied scope inputs that are available without phase production."""

    if envelope is None:
        return set()
    runtime_ids = set(RUNTIME_SCOPE_INPUT_IDS)
    if plan is not None:
        runtime_ids.update(
            contract.id
            for contract in plan.artifact_contracts
            if contract.produced_by_phase is None and _is_runtime_scope_contract(contract)
        )
    return runtime_ids


def normalize_artifact_contract_bundle(contracts: list[ArtifactContract]) -> list[ArtifactContract]:
    """Remove speculative producer semantics before phase assembly."""

    normalized: list[ArtifactContract] = []
    for contract in contracts:
        updates: dict[str, Any] = {"produced_by_phase": None}
        normalized.append(contract.model_copy(update=updates))
    return normalized


def normalize_phase_plan(plan: PhasePlan) -> PhasePlan:
    """Align artifact contracts with actual phase outputs and built-in runtime scope."""

    output_phase_by_artifact: dict[str, str] = {}
    for phase in plan.phases:
        for artifact_id in phase.output_artifacts:
            output_phase_by_artifact.setdefault(artifact_id, phase.phase_id)

    normalized_contracts: list[ArtifactContract] = []
    for contract in plan.artifact_contracts:
        updates: dict[str, Any] = {}
        output_phase_id = output_phase_by_artifact.get(contract.id)
        if _is_runtime_scope_contract(contract) and output_phase_id is None:
            updates["kind"] = "input"
            updates["produced_by_phase"] = None
        elif output_phase_id is not None:
            if str(contract.kind).strip().lower() == "input":
                updates["kind"] = "phase_output"
            updates["produced_by_phase"] = output_phase_id
        normalized_contracts.append(contract.model_copy(update=updates) if updates else contract)

    return plan.model_copy(update={"artifact_contracts": normalized_contracts})


def _is_builtin_runtime_scope_contract(contract: ArtifactContract) -> bool:
    return contract.id in RUNTIME_SCOPE_INPUT_IDS


def _is_runtime_scope_contract(contract: ArtifactContract) -> bool:
    kind = str(contract.kind or "").strip().lower()
    return contract.id in RUNTIME_SCOPE_INPUT_IDS or kind == "input" or contract.id.endswith("_input")


def _issue(
    owner: str,
    severity: str,
    code: str,
    message: str,
    *,
    path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        owner=owner,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        path=path,
        metadata=metadata or {},
    )
