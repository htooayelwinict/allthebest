"""AppV2 contracts for phase planning and single-loop execution."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


PhaseName = Literal["DISCOVER", "ANALYZE", "RESEARCH", "DESIGN", "MUTATE", "VERIFY", "FINALIZE"]
ToolGroup = Literal["repo_read", "file_write", "verify", "research_read"]
ArtifactKind = Literal[
    "input",
    "contract",
    "tool_observation",
    "phase_output",
    "mutation_record",
    "verification_evidence",
    "runtime_memory",
    "final_report",
]
ArtifactLifecycle = Literal["completed", "partial", "failed", "observation", "derived", "memory"]
TrustLevel = Literal["unknown", "model_reported", "tool_observed", "runtime_verified"]
RuntimeStatus = Literal["completed", "failed", "blocked", "budget_exceeded", "needs_replan", "kernel_error"]
ValidationOwner = Literal["decomposer", "planner", "worker", "policy_gate", "verification_gate", "kernel"]
ValidationSeverity = Literal["warning", "repairable", "blocking"]


class ExactLiteral(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    kind: Literal["json_key", "path", "filename", "artifact_id", "symbol", "other"] = "other"
    source: Literal["user_input", "runtime_observation", "model"] = "user_input"

    @model_validator(mode="after")
    def normalize(self) -> "ExactLiteral":
        self.value = " ".join(str(self.value or "").strip().split())
        if not self.value:
            raise ValueError("literal value must be non-empty")
        return self


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    raw_input: str
    normalized_input: str
    user_goal: str | None = None
    input_type: str
    intents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    complexity_hint: Literal["low", "medium", "high"] | str = "medium"
    confidence: float = 0.0
    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    literal_contract: list[ExactLiteral] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = ""
    kind: ArtifactKind | str = "contract"
    required: bool = True
    content_schema: dict[str, Any] = Field(default_factory=dict)
    produced_by_phase: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MutationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["readonly", "advisory", "strict"] = "advisory"
    allowed_paths: list[str] = Field(default_factory=list)
    advisory_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    forbidden_globs: list[str] = Field(default_factory=list)
    max_files: int = 8
    allow_create: bool = True
    allow_update: bool = True
    allow_delete: bool = False
    allow_move: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = True
    commands: list[str] = Field(default_factory=list)
    file_state_checks: list[dict[str, Any]] = Field(default_factory=list)
    require_evidence: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class PhaseStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase_id: str
    phase: PhaseName
    goal: str
    instructions: list[str] = Field(default_factory=list)
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    allowed_tool_groups: list[ToolGroup] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    mutation_policy: MutationPolicy | None = None
    verification_policy: VerificationPolicy | None = None
    acceptance_checks: list[str] = Field(default_factory=list)
    max_tool_calls: int = 5
    max_model_calls: int = 3
    metadata: dict[str, Any] = Field(default_factory=dict)


class PhasePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    request_id: str
    objective: str
    strategy: str
    phases: list[PhaseStep]
    budgets: dict[str, Any] = Field(default_factory=dict)
    global_invariants: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    artifact_contracts: list[ArtifactContract] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: ArtifactKind | str
    content: Any = None
    producer: str
    phase_id: str | None = None
    trust_level: TrustLevel = "unknown"
    lifecycle: ArtifactLifecycle = "completed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""


class FileOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["write", "replace", "move", "delete", "mkdir"]
    path: str | None = None
    source: str | None = None
    destination: str | None = None
    content: str | None = None
    old: str | None = None
    new: str | None = None
    overwrite: bool = False


class MutationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_batch_id: str
    operations: list[FileOperation]
    reason: str
    expected_artifacts: list[str] = Field(default_factory=list)


class PhaseOutputProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed", "blocked", "needs_replan"] = "completed"
    summary: str
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannerReplanSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    phase_id: str
    issue_codes: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: list[ToolCallProposal] | None = None
    mutation: MutationProposal | None = None
    final_phase_output: PhaseOutputProposal | None = None
    planner_replan_signal: PlannerReplanSignal | None = None

    @model_validator(mode="after")
    def exactly_one_branch(self) -> "WorkerDecision":
        branches = [
            bool(self.tool_calls),
            self.mutation is not None,
            self.final_phase_output is not None,
            self.planner_replan_signal is not None,
        ]
        if sum(1 for branch in branches if branch) != 1:
            raise ValueError("WorkerDecision must contain exactly one action branch")
        return self


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: ValidationOwner
    severity: ValidationSeverity
    code: str
    message: str
    path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    request_id: str
    plan_id: str | None = None
    status: RuntimeStatus
    summary: str
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PhaseReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    plan_id: str
    run_id: str
    failed_phase_id: str
    reason: str
    completed_artifacts: list[ArtifactRecord] = Field(default_factory=list)
    carryover_artifacts: list[ArtifactRecord] = Field(default_factory=list)
    completed_phase_ids: list[str] = Field(default_factory=list)
    remaining_budgets: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def default_carryover(self) -> "PhaseReplanRequest":
        if not self.carryover_artifacts:
            self.carryover_artifacts = list(self.completed_artifacts)
        return self


class RuntimeState(TypedDict, total=False):
    user_input: str
    envelope: dict[str, Any]
    phase_plan: dict[str, Any]
    result: dict[str, Any]
    runtime_matrix: dict[str, Any]
    errors: list[str]
