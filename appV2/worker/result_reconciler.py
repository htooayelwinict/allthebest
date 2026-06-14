"""Final result reconciliation for AppV2 worker runtime."""

from __future__ import annotations

from appV2.schemas import ArtifactRecord, PhasePlan, RuntimeResult, ValidationIssue


class ResultReconciler:
    def reconcile(
        self,
        *,
        run_id: str,
        plan: PhasePlan,
        status: str,
        summary: str,
        artifacts: list[ArtifactRecord],
        issues: list[ValidationIssue],
        usage: dict,
        metadata: dict,
    ) -> RuntimeResult:
        return RuntimeResult(
            run_id=run_id,
            request_id=plan.request_id,
            plan_id=plan.plan_id,
            status=status,  # type: ignore[arg-type]
            summary=summary,
            artifacts=artifacts,
            issues=issues,
            usage=usage,
            metadata=metadata,
        )
