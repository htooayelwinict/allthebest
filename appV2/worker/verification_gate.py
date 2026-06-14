"""Verification gate for AppV2 worker output."""

from __future__ import annotations

from appV2.schemas import ArtifactRecord, PhaseOutputProposal, PhaseStep, ValidationIssue
from appV2.validator import AppV2Validator


class VerificationGate:
    def __init__(self, *, validator: AppV2Validator | None = None) -> None:
        self._validator = validator or AppV2Validator()

    def validate_phase_output(
        self,
        *,
        phase: PhaseStep,
        output: PhaseOutputProposal,
        evidence: list[ArtifactRecord],
    ) -> list[ValidationIssue]:
        issues = self._validator.validate_phase_output(output, phase=phase)
        issues.extend(self._validator.validate_verification_evidence(phase=phase, output=output, evidence=evidence))
        return issues
