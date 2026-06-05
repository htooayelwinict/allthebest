"""Compact context-frame builder for the AppV2 worker loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from appV2.schemas import Envelope, PhasePlan, PhaseStep
from appV2.worker.ledgers import ArtifactLedger, MutationLedger
from appV2.worker.tools import ToolRegistry


@dataclass(frozen=True)
class PhaseFrame:
    request_id: str
    plan_id: str
    objective: str
    phase: dict[str, Any]
    pending_outputs: list[str]
    resolved_inputs: list[dict[str, Any]]
    input_artifact_contracts: list[dict[str, Any]]
    output_artifact_contracts: list[dict[str, Any]]
    artifact_ledger: dict[str, Any]
    mutation_ledger: dict[str, Any]
    available_tools: list[dict[str, Any]]
    envelope_summary: dict[str, Any]
    retry_memory: dict[str, Any]


class ContextController:
    def build_phase_frame(
        self,
        *,
        envelope: Envelope | None,
        plan: PhasePlan,
        phase: PhaseStep,
        artifacts: ArtifactLedger,
        mutations: MutationLedger,
        tools: ToolRegistry,
        retry_memory: dict[str, Any] | None = None,
    ) -> PhaseFrame:
        contracts_by_id = {contract.id: contract.model_dump(mode="json") for contract in plan.artifact_contracts}
        runtime_scope = _runtime_scope_records(envelope=envelope, plan=plan, input_artifact_ids=phase.input_artifacts)
        return PhaseFrame(
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            objective=plan.objective,
            phase=phase.model_dump(mode="json"),
            pending_outputs=[
                artifact_id for artifact_id in phase.output_artifacts if artifacts.by_id(artifact_id) is None
            ],
            resolved_inputs=[
                *runtime_scope,
                *artifacts.context_records(phase.input_artifacts),
            ],
            input_artifact_contracts=[
                contracts_by_id[artifact_id] for artifact_id in phase.input_artifacts if artifact_id in contracts_by_id
            ],
            output_artifact_contracts=[
                contracts_by_id[artifact_id] for artifact_id in phase.output_artifacts if artifact_id in contracts_by_id
            ],
            artifact_ledger=artifacts.compact_view(phase_id=phase.phase_id),
            mutation_ledger=mutations.compact_view(),
            available_tools=tools.available_tools(phase),
            envelope_summary={
                "normalized_input": envelope.normalized_input if envelope else None,
                "user_goal": envelope.user_goal if envelope else None,
                "input_type": envelope.input_type if envelope else None,
                "ambiguity": envelope.ambiguity if envelope else [],
                "literal_contract": [literal.model_dump(mode="json") for literal in envelope.literal_contract] if envelope else [],
            },
            retry_memory=retry_memory or {},
        )


def _runtime_scope_records(
    *,
    envelope: Envelope | None,
    plan: PhasePlan,
    input_artifact_ids: list[str],
) -> list[dict[str, Any]]:
    if envelope is None:
        return []
    contracts_by_id = {contract.id: contract for contract in plan.artifact_contracts}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact_id in input_artifact_ids:
        if artifact_id in seen:
            continue
        record = _runtime_scope_record(
            artifact_id=artifact_id,
            contract=contracts_by_id.get(artifact_id),
            envelope=envelope,
        )
        if record is None:
            continue
        seen.add(artifact_id)
        records.append(record)
    return records


def _runtime_scope_record(
    *,
    artifact_id: str,
    contract: Any | None,
    envelope: Envelope,
) -> dict[str, Any] | None:
    if artifact_id == "request_envelope":
        return {
            "id": "request_envelope",
            "kind": "input",
            "content": {
                "request_id": envelope.request_id,
                "normalized_input": envelope.normalized_input,
                "user_goal": envelope.user_goal,
                "constraints": list(envelope.constraints),
                "ambiguity": list(envelope.ambiguity),
                "artifacts": list(envelope.artifacts),
                "literal_contract": [literal.model_dump(mode="json") for literal in envelope.literal_contract],
            },
            "producer": "appv2_runtime_scope",
            "phase_id": None,
            "trust_level": "runtime_verified",
            "lifecycle": "completed",
            "metadata": {"source": "envelope"},
        }

    if contract is None:
        return None
    kind = str(getattr(contract, "kind", "") or "").strip().lower()
    if kind != "input" and not artifact_id.endswith("_input"):
        return None

    content = _const_content_from_schema(contract.content_schema or {})
    if not content:
        return None

    return {
        "id": artifact_id,
        "kind": "input",
        "content": content,
        "producer": "appv2_runtime_scope",
        "phase_id": None,
        "trust_level": "runtime_verified",
        "lifecycle": "completed",
        "metadata": {"source": contract.metadata.get("source", "artifact_contract")},
    }


def _const_content_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    content: dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, dict) and "const" in value:
            content[str(key)] = value["const"]
    return content
