"""Workflow readiness gate for appv24 passive workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.ir import StepKind, WorkflowIR
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.validator import validate_workflow_documents

WorkflowQualitySeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class WorkflowQualityIssue:
    code: str
    file: str
    path: tuple[str | int, ...]
    message: str
    severity: WorkflowQualitySeverity = "error"
    repair_hint: str | None = None

    @property
    def location(self) -> str:
        if not self.path:
            return self.file
        suffix = ".".join(str(part) for part in self.path)
        return f"{self.file}:{suffix}"


@dataclass(frozen=True)
class WorkflowFixtureQualityReport:
    fixture_name: str
    status: str
    terminal_step: str | None
    trace_steps: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowQualityResult:
    root: Path
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    issues: tuple[WorkflowQualityIssue, ...] = ()
    fixture_reports: tuple[WorkflowFixtureQualityReport, ...] = ()

    @property
    def ok(self) -> bool:
        return self.validation_ok and self.compile_ok and self.policy_ok and not self.issues


def check_workflow_quality_package(
    path: str | Path,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult:
    documents = load_package_documents(path)
    return check_workflow_quality_documents(documents, broker=broker)


def check_workflow_quality_documents(
    documents: PackageDocuments,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult:
    del broker
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=False,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(
                    code=issue.code,
                    file=issue.file,
                    path=issue.path,
                    message=issue.message,
                    repair_hint=issue.repair_hint,
                )
                for issue in validation.issues
            ),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=True,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(
                    code=issue.code,
                    file=issue.file,
                    path=issue.path,
                    message=issue.message,
                    repair_hint=issue.repair_hint,
                )
                for issue in compiled.issues
            ),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=True,
            compile_ok=True,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(
                    code=issue.code,
                    file="workflow.yaml",
                    path=("steps", issue.step_id) if issue.step_id else (),
                    message=issue.message,
                    severity=issue.severity.value,
                )
                for issue in policy.issues
            ),
        )

    return WorkflowQualityResult(
        root=documents.root,
        validation_ok=True,
        compile_ok=True,
        policy_ok=True,
        issues=tuple(_check_graph(compiled.ir)),
    )


def _check_graph(ir: WorkflowIR) -> list[WorkflowQualityIssue]:
    issues: list[WorkflowQualityIssue] = []
    for step in ir.steps.values():
        if step.kind is StepKind.TERMINAL or step.kind is StepKind.DECISION:
            continue
        if step.next_step is not None:
            continue
        issues.append(
            WorkflowQualityIssue(
                code="missing_transition",
                file="workflow.yaml",
                path=("steps", step.step_id, "next"),
                message=f"non-terminal step {step.step_id} has no next transition",
                repair_hint=f"Add next to workflow.yaml step {step.step_id}.",
            )
        )
    return issues
