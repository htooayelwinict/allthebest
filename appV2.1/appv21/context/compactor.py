"""Runtime-owned context compaction for AppV2.1."""

from __future__ import annotations

from appv21.state.models import AgentState


class RuntimeContextCompactor:
    def should_compact(self, state: AgentState) -> bool:
        return len(state.conversation.messages) >= 8 or len(state.world.refs) >= 8 or bool(state.world.verification_receipts)

    def compact(self, state: AgentState) -> dict:
        latest_world_refs = list(state.world.refs)[-3:]
        repo_snapshot_refs = [ref_id for ref_id, ref in state.world.refs.items() if ref.kind == "repo_snapshot"]
        artifact_refs = sorted(
            {
                ref
                for artifact in state.world.artifacts.values()
                for ref in artifact.evidence_refs
                if ref in state.world.refs
            }
        )
        return {
            "active_request": state.request.user_goal,
            "current_mode": state.mode,
            "open_pause": state.pauses[-1].__dict__ if state.pauses else None,
            "active_leases": list(state.world.mutation_leases),
            "latest_world_refs": latest_world_refs,
            "preserved_world_refs": sorted(set([*latest_world_refs, *repo_snapshot_refs, *artifact_refs])),
            "compacted_world_ref_count": len(state.world.refs),
            "verification_receipts": list(state.world.verification_receipts),
            "mutation_receipts": list(state.world.mutation_receipts),
            "artifact_evidence_refs": {
                artifact_id: artifact.evidence_refs for artifact_id, artifact in state.world.artifacts.items()
            },
            "unresolved_errors": [],
        }
