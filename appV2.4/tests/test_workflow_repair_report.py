from __future__ import annotations

from types import SimpleNamespace

from appv24.workflows.repair_report import (
    WorkflowRepairAttemptSummary,
    WorkflowRepairReport,
    format_workflow_repair_report,
)
from appv24.workflows.repair_scope import repeated_validation_count, workflow_issue_fingerprint


def test_repair_report_formats_attempts_and_convergence() -> None:
    report = WorkflowRepairReport(
        attempts=(
            WorkflowRepairAttemptSummary(
                attempt=1,
                status="failed",
                issue_fingerprints=("unknown_catalog_action:workflow.yaml:steps[1].uses",),
            ),
            WorkflowRepairAttemptSummary(attempt=2, status="passed", issue_fingerprints=()),
        )
    )

    text = format_workflow_repair_report(report)

    assert "Attempt 1: failed" in text
    assert "unknown_catalog_action" in text
    assert "Attempt 2: passed" in text


def test_issue_fingerprint_ignores_message_wording() -> None:
    issue_a = SimpleNamespace(
        code="unknown_catalog_action",
        file="workflow.yaml",
        location="steps[1].uses",
        message="A",
    )
    issue_b = SimpleNamespace(
        code="unknown_catalog_action",
        file="workflow.yaml",
        location="steps[1].uses",
        message="B",
    )

    assert workflow_issue_fingerprint(issue_a) == workflow_issue_fingerprint(issue_b)


def test_repeated_validation_count_counts_prior_matching_fingerprints() -> None:
    current = ("unknown_catalog_action:workflow.yaml:steps[1].uses",)
    history = (
        ("missing_design_contract:workflow.yaml:design.inputs",),
        current,
        current,
    )

    assert repeated_validation_count(history, current) == 2

