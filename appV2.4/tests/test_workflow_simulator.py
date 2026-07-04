from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.simulator import simulate_workflow_fixture


def write_branching_package(root: Path) -> None:
    from test_workflow_packages import write_minimal_package

    write_minimal_package(root)
    workflow = (root / "workflow.yaml").read_text(encoding="utf-8")
    workflow = workflow.replace(
        "entrypoint: inspect_alerts\nsteps:\n  - id: inspect_alerts\n    uses: github.list_dependabot_alerts\n    next: draft_issue",
        "entrypoint: inspect_alerts\nsteps:\n  - id: inspect_alerts\n    uses: github.list_dependabot_alerts\n    next: decide_path\n  - id: decide_path\n    type: decision\n    branches:\n      approved: draft_issue\n      rejected: done",
    )
    (root / "workflow.yaml").write_text(workflow, encoding="utf-8")
    fixtures = root / "dry_run_fixtures"
    (fixtures / "happy_path.yaml").write_text(
        "decision:\n"
        "  decide_path: approved\n"
        "mock_responses:\n"
        "  github.list_dependabot_alerts:\n"
        "    alerts: []\n"
        "  github.create_issue:\n"
        "    issue_number: 12\n"
        "    issue_url: https://example.invalid/12\n",
        encoding="utf-8",
    )
    (fixtures / "rejection_path.yaml").write_text(
        "decision:\n"
        "  decide_path: rejected\n"
        "mock_responses:\n"
        "  github.list_dependabot_alerts:\n"
        "    alerts: []\n",
        encoding="utf-8",
    )


def replace_documents(
    documents: PackageDocuments,
    *,
    workflow: object | None = None,
) -> PackageDocuments:
    return PackageDocuments(
        root=documents.root,
        workflow=documents.workflow if workflow is None else workflow,
        connectors=documents.connectors,
        permissions=documents.permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )


def compile_branching_ir(tmp_path: Path):
    package = tmp_path / "package"
    write_branching_package(package)
    documents = load_package_documents(package)
    result = compile_workflow_documents(documents)
    assert result.ok
    assert result.ir is not None
    return result.ir, documents


def test_simulator_runs_happy_path_fixture(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)

    ledger = simulate_workflow_fixture(ir, "happy_path.yaml", documents.dry_run_fixtures["happy_path.yaml"])

    assert ledger.status == "passed"
    assert [trace.step_id for trace in ledger.trace] == ["inspect_alerts", "decide_path", "draft_issue", "done"]
    assert ledger.terminal_step == "done"
    assert ledger.simulated_side_effects == ("draft_issue",)


def test_simulator_runs_rejection_path_fixture_without_side_effect(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)

    ledger = simulate_workflow_fixture(ir, "rejection_path.yaml", documents.dry_run_fixtures["rejection_path.yaml"])

    assert ledger.status == "passed"
    assert [trace.step_id for trace in ledger.trace] == ["inspect_alerts", "decide_path", "done"]
    assert ledger.simulated_side_effects == ()


def test_simulator_blocks_unknown_decision_value(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)
    fixture = deepcopy(documents.dry_run_fixtures["happy_path.yaml"])
    fixture["decision"]["decide_path"] = "unknown"

    ledger = simulate_workflow_fixture(ir, "broken.yaml", fixture)

    assert ledger.status == "blocked"
    assert ledger.terminal_step is None
    assert any("no branch matched" in issue for issue in ledger.issues)


def test_simulator_detects_cycle(tmp_path: Path) -> None:
    ir, documents = compile_branching_ir(tmp_path)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][-1]["next"] = "inspect_alerts"
    workflow["steps"][-1]["type"] = "action"
    result = compile_workflow_documents(replace_documents(documents, workflow=workflow))
    assert result.ok
    assert result.ir is not None

    ledger = simulate_workflow_fixture(
        result.ir,
        "happy_path.yaml",
        documents.dry_run_fixtures["happy_path.yaml"],
        max_transitions=5,
    )

    assert ledger.status == "blocked"
    assert any("transition budget exceeded" in issue for issue in ledger.issues)
