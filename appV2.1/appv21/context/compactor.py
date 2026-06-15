"""Runtime-owned context compaction for AppV2.1."""

from __future__ import annotations

from copy import deepcopy

from appv21.state.models import AgentState


IMMUTABLE_CONTEXT_CLASSES = [
    "user_request",
    "constraints",
    "pause_state",
    "mutation_receipts",
    "verification_receipts",
    "active_leases",
]

CONTEXT_PRESERVATION_POLICY = {
    "keep_repo_snapshot_refs": True,
    "keep_artifact_evidence_refs": True,
    "keep_latest_world_ref_count": 3,
}


class RuntimeContextCompactor:
    def should_compact(self, state: AgentState) -> bool:
        return len(state.conversation.messages) >= 8 or len(state.world.refs) >= 8 or bool(state.world.verification_receipts)

    def compact(self, state: AgentState) -> dict:
        latest_world_refs = list(state.world.refs)[-3:]
        repo_snapshot_refs = sorted(ref_id for ref_id, ref in state.world.refs.items() if ref.kind == "repo_snapshot")
        artifact_refs = sorted(
            {
                ref
                for artifact in state.world.artifacts.values()
                for ref in artifact.evidence_refs
                if ref in state.world.refs
            }
        )
        active_lease_ids = sorted(state.world.mutation_leases)
        mutation_receipt_ids = sorted(state.world.mutation_receipts)
        verification_receipt_ids = sorted(state.world.verification_receipts)
        return {
            "immutable_classes": list(IMMUTABLE_CONTEXT_CLASSES),
            "preservation_policy": dict(CONTEXT_PRESERVATION_POLICY),
            "active_request": state.request.user_goal,
            "current_mode": state.mode,
            "open_pause": deepcopy(state.pauses[-1].__dict__) if state.pauses else None,
            "active_leases": active_lease_ids,
            "active_lease_snapshots": {
                lease_id: deepcopy(state.world.mutation_leases[lease_id].__dict__) for lease_id in active_lease_ids
            },
            "latest_world_refs": latest_world_refs,
            "preserved_world_refs": sorted(set([*latest_world_refs, *repo_snapshot_refs, *artifact_refs])),
            "compacted_world_ref_count": len(state.world.refs),
            "verification_receipts": verification_receipt_ids,
            "verification_receipt_snapshots": {
                receipt_id: deepcopy(state.world.verification_receipts[receipt_id])
                for receipt_id in verification_receipt_ids
            },
            "mutation_receipts": mutation_receipt_ids,
            "mutation_receipt_snapshots": {
                receipt_id: deepcopy(state.world.mutation_receipts[receipt_id].__dict__)
                for receipt_id in mutation_receipt_ids
            },
            "artifact_evidence_refs": {
                artifact_id: list(state.world.artifacts[artifact_id].evidence_refs)
                for artifact_id in sorted(state.world.artifacts)
            },
            "unresolved_errors": [],
        }
