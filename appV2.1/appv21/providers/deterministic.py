"""Deterministic provider that drives the current workspace probe through decisions."""

from __future__ import annotations

from appv21.runtime.decisions import (
    RuntimeDecision,
    finalize_decision,
    mutation_decision,
    observe_decision,
    plan_decision,
    verify_decision,
)


class DeterministicWorkspaceProvider:
    provider_id = "deterministic-workspace"

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        state = prompt_payload.get("state", {})
        world = prompt_payload.get("world", {})
        plan = state.get("plan")
        mutation_receipts = state.get("mutation_receipts") or []
        verification_receipts = state.get("verification_receipts") or []
        artifacts = state.get("artifacts") or []
        repo_refs = [ref for ref in world.get("world_refs", []) if ref.get("kind") == "repo_snapshot"]

        if not repo_refs:
            return observe_decision()
        if plan is None:
            return plan_decision(evidence_refs=[repo_refs[-1]["ref_id"]])
        if not mutation_receipts:
            return mutation_decision(plan=plan.get("runtime_plan") or plan)
        if not verification_receipts:
            return verify_decision(plan=plan.get("runtime_plan") or plan)
        if not artifacts:
            return finalize_decision()
        return finalize_decision(reason="Run already has verified artifact; finalize terminal state.")
