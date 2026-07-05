from __future__ import annotations

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.fixtures import fixture_names, require_fixture
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.reports import build_enablement_report
from appv24.workflows.simulator import simulate_workflow_fixture


def test_golden_examples_compile_and_pass_policy() -> None:
    for example in golden_workflow_examples().values():
        result = compile_workflow_documents(example.documents)
        assert result.ok, example.name
        assert result.ir is not None
        assert evaluate_policy(result.ir).ok, example.name


def test_gmail_draft_qa_example_runs_happy_path() -> None:
    example = golden_workflow_examples()["gmail_draft_reply"]
    result = compile_workflow_documents(example.documents)
    assert result.ok
    assert result.ir is not None

    ledger = simulate_workflow_fixture(
        result.ir,
        "happy_path.yaml",
        example.fixtures["happy_path.yaml"],
        broker=example.broker,
    )

    assert ledger.status == "passed"
    assert ledger.terminal_step == "done"
    assert ledger.simulated_side_effects == ("create_gmail_draft",)
    assert ledger.trace[1].connector_action == "gmail.create_draft"
    assert ledger.trace[1].simulated_outputs == {"draft_id": "draft-1"}


def test_telegram_support_ticket_happy_path_is_deterministic() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = compile_workflow_documents(example.documents)
    assert result.ir is not None

    first = simulate_workflow_fixture(result.ir, "happy_path.yaml", example.fixtures["happy_path.yaml"], broker=example.broker)
    second = simulate_workflow_fixture(result.ir, "happy_path.yaml", example.fixtures["happy_path.yaml"], broker=example.broker)

    assert first.to_dict() == second.to_dict()
    assert first.status == "passed"
    assert first.terminal_step == "done"
    assert first.simulated_side_effects == ("create_ticket_draft", "notify_admin")


def test_telegram_support_ticket_rejection_path_has_no_side_effects() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = compile_workflow_documents(example.documents)
    assert result.ir is not None

    ledger = simulate_workflow_fixture(
        result.ir,
        "rejection_path.yaml",
        example.fixtures["rejection_path.yaml"],
        broker=example.broker,
    )

    assert ledger.status == "passed"
    assert ledger.terminal_step == "rejected"
    assert ledger.simulated_side_effects == ()


def test_enablement_report_summarizes_golden_example() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    report = build_enablement_report(
        example.documents,
        "happy_path.yaml",
        example.fixtures["happy_path.yaml"],
        broker=example.broker,
    )

    assert report.workflow_name == "telegram_support_ticket"
    assert report.validation_ok is True
    assert report.compile_ok is True
    assert report.policy_ok is True
    assert report.simulation_status == "passed"
    assert report.terminal_step == "done"
    assert report.simulated_side_effects == ("create_ticket_draft", "notify_admin")
    assert report.issues == ()


def test_fixture_helpers_return_sorted_names_and_required_fixture() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    assert fixture_names(example.fixtures) == ("happy_path.yaml", "rejection_path.yaml")
    assert require_fixture(example.fixtures, "happy_path.yaml") is example.fixtures["happy_path.yaml"]


def test_require_fixture_reports_available_names() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    try:
        require_fixture(example.fixtures, "missing.yaml")
    except WorkflowPackageError as error:
        assert str(error) == "missing dry-run fixture: missing.yaml; available: happy_path.yaml, rejection_path.yaml"
    else:
        raise AssertionError("expected missing fixture to raise")
