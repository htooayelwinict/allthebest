"""Workflow repair reporting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowRepairAttemptSummary:
    attempt: int
    status: str
    issue_fingerprints: tuple[str, ...]
    repeated_count: int = 0


@dataclass(frozen=True)
class WorkflowRepairReport:
    attempts: tuple[WorkflowRepairAttemptSummary, ...] = ()

    @property
    def status(self) -> str:
        if not self.attempts:
            return "unavailable"
        return self.attempts[-1].status


def format_workflow_repair_report(report: WorkflowRepairReport) -> str:
    if not report.attempts:
        return "No workflow repair attempts recorded."

    lines = ["Workflow repair report:"]
    for attempt in report.attempts:
        suffix = " (repeated same failure pattern)" if attempt.repeated_count else ""
        lines.append(f"Attempt {attempt.attempt}: {attempt.status}{suffix}")
        if attempt.issue_fingerprints:
            lines.extend(f"- {fingerprint}" for fingerprint in attempt.issue_fingerprints)
    return "\n".join(lines)
