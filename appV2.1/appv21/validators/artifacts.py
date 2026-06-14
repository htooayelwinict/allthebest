"""Artifact validators for AppV2.1."""

from __future__ import annotations

from appv21.runtime.decisions import RuntimeDecision
from appv21.state.models import Artifact, AgentState


class ArtifactValidator:
    known_decision_kinds = {"observe", "read_file", "plan", "tool_call", "mutation_intent", "verify", "pause", "compact", "finalize"}

    def validate_decision(self, decision: RuntimeDecision, state: AgentState) -> list[str]:
        issues: list[str] = []
        if decision.kind not in self.known_decision_kinds:
            issues.append(f"unknown_decision_kind:{decision.kind}")
        for ref in decision.evidence_refs:
            if ref == "plan://accepted/latest" and state.plan is None:
                issues.append(f"missing_evidence_ref:{ref}")
            elif ref == "verification://latest" and not state.world.verification_receipts:
                issues.append(f"missing_evidence_ref:{ref}")
            elif (
                not ref.startswith("plan://")
                and not ref.startswith("verification://")
                and ref not in state.world.refs
                and ref not in state.world.mutation_receipts
                and ref not in state.world.verification_receipts
            ):
                issues.append(f"missing_evidence_ref:{ref}")
        if decision.kind == "finalize" and not state.world.verification_receipts and not decision.payload.get("explicit_noop"):
            issues.append("finalize_requires_verification_or_explicit_noop")
        return issues

    def validate_tool_call(self, tool_call: dict, _state: AgentState) -> list[str]:
        if not tool_call.get("tool_name"):
            return ["tool_name_required"]
        return []

    def validate_tool_result(self, tool_result: dict, _state: AgentState) -> list[str]:
        issues: list[str] = []
        if not tool_result.get("tool_result_id"):
            issues.append("tool_result_id_required")
        if tool_result.get("status") not in {"completed", "failed", "denied"}:
            issues.append("invalid_tool_result_status")
        if "prompt_summary" not in tool_result:
            issues.append("prompt_summary_required")
        return issues

    def validate_artifact(self, artifact: Artifact, state: AgentState) -> list[str]:
        return self.validate(artifact, state)

    def validate(self, artifact: Artifact, state: AgentState) -> list[str]:
        issues: list[str] = []
        if not artifact.artifact_id:
            issues.append("artifact_id_required")
        if artifact.trust == "runtime_verified" and not artifact.evidence_refs:
            issues.append("runtime_verified_requires_evidence")
        for ref in artifact.evidence_refs:
            if ref not in state.world.refs and ref not in state.world.mutation_receipts and ref not in state.world.verification_receipts:
                issues.append(f"missing_evidence_ref:{ref}")
        return issues
