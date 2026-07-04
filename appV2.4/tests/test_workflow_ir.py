from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents, compile_workflow_package
from appv24.workflows.ir import StepKind
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.policy import evaluate_policy


def write_ir_package(root: Path) -> None:
    from test_workflow_packages import write_minimal_package

    write_minimal_package(root)


def replace_documents(
    documents: PackageDocuments,
    *,
    workflow: object | None = None,
    connectors: object | None = None,
    permissions: object | None = None,
) -> PackageDocuments:
    return PackageDocuments(
        root=documents.root,
        workflow=documents.workflow if workflow is None else workflow,
        connectors=documents.connectors if connectors is None else connectors,
        permissions=documents.permissions if permissions is None else permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_compile_workflow_package_builds_ir_for_valid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)

    result = compile_workflow_package(package)

    assert result.ok
    assert result.ir is not None
    assert result.ir.name == "dependency-security-review"
    assert result.ir.entrypoint == "inspect_alerts"
    assert sorted(result.ir.steps) == ["done", "draft_issue", "inspect_alerts"]
    assert result.ir.steps["inspect_alerts"].kind is StepKind.ACTION
    assert result.ir.steps["draft_issue"].uses is not None
    assert result.ir.steps["draft_issue"].uses.connector_id == "github"
    assert result.ir.steps["draft_issue"].uses.action_id == "create_issue"
    assert result.ir.steps["draft_issue"].side_effect_class == "create"
    assert result.ir.steps["draft_issue"].approval == "human-review"
    assert result.ir.connectors["github"].actions["create_issue"].execution_mode == "brokered"


def test_compile_workflow_documents_normalizes_edges(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)

    result = compile_workflow_documents(documents)

    assert result.ok
    assert result.ir is not None
    assert result.ir.steps["inspect_alerts"].outbound == ("draft_issue",)
    assert result.ir.steps["draft_issue"].outbound == ("done",)
    assert result.ir.steps["done"].outbound == ()


def test_compile_reports_unknown_transition(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["next"] = "missing_step"

    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))

    assert not result.ok
    assert "unknown_transition" in issue_codes(result)
    assert result.ir is None


def test_compile_reports_unknown_connector_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["uses"] = "github.missing_action"

    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))

    assert not result.ok
    assert "unknown_action" in issue_codes(result)
    assert result.ir is None


def test_compile_reports_duplicate_step_ids(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][1]["id"] = "inspect_alerts"

    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))

    assert not result.ok
    assert "duplicate_step" in issue_codes(result)
    assert result.ir is None


def test_compile_reports_invalid_package_before_ir(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_ir_package(package)
    (package / "workflow.yaml").write_text("version: appv24.workflow/v1\n", encoding="utf-8")

    result = compile_workflow_package(package)

    assert not result.ok
    assert "schema_required" in issue_codes(result)
    assert result.ir is None


def policy_codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def compile_valid_ir(tmp_path: Path):
    package = tmp_path / "package"
    write_ir_package(package)
    result = compile_workflow_package(package)
    assert result.ok
    assert result.ir is not None
    return result.ir


def test_policy_accepts_valid_brokered_mutation(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)

    report = evaluate_policy(ir)

    assert report.ok
    assert report.issues == ()


def test_policy_blocks_mutating_step_without_approval(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)
    steps = dict(ir.steps)
    steps["draft_issue"] = replace(steps["draft_issue"], approval=None)
    ir = replace(ir, steps=steps)

    report = evaluate_policy(ir)

    assert not report.ok
    assert "policy_missing_approval" in policy_codes(report)


def test_policy_blocks_missing_required_scope(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)
    connector = ir.connectors["github"]
    actions = dict(connector.actions)
    actions["create_issue"] = replace(actions["create_issue"], granted_scopes=("dependabot:read",))
    connectors = dict(ir.connectors)
    connectors["github"] = replace(connector, actions=actions)
    ir = replace(ir, connectors=connectors)

    report = evaluate_policy(ir)

    assert not report.ok
    assert "policy_missing_scope" in policy_codes(report)


def test_policy_blocks_output_wildcard(tmp_path: Path) -> None:
    ir = compile_valid_ir(tmp_path)
    connector = ir.connectors["github"]
    actions = dict(connector.actions)
    actions["create_issue"] = replace(actions["create_issue"], allowed_outputs=("*",))
    connectors = dict(ir.connectors)
    connectors["github"] = replace(connector, actions=actions)
    ir = replace(ir, connectors=connectors)

    report = evaluate_policy(ir)

    assert not report.ok
    assert "policy_wildcard_outputs" in policy_codes(report)
