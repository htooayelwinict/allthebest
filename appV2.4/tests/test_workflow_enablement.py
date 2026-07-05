from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from appv24.workflows.enablement import build_workflow_enablement_report
from appv24.workflows.examples import golden_workflow_examples


def test_enablement_v1_report_covers_all_fixtures_and_capabilities() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    report = build_workflow_enablement_report(example.documents, broker=example.broker)

    assert report.workflow_name == "telegram_support_ticket"
    assert report.validation_ok is True
    assert report.compile_ok is True
    assert report.policy_ok is True
    assert report.quality_ok is True
    assert report.ok is True
    assert tuple(item.action for item in report.capability_plan) == (
        "ticket.create_draft",
        "telegram.send_message",
    )
    assert {fixture.fixture_name for fixture in report.fixtures} == {"happy_path.yaml", "rejection_path.yaml"}
    assert report.issues == ()


def test_enablement_v1_report_blocks_quality_gate_failures() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = deepcopy(example.documents.workflow)
    workflow["steps"][1].pop("next", None)
    documents = replace(example.documents, workflow=workflow)

    report = build_workflow_enablement_report(documents, broker=example.broker)

    assert report.ok is False
    assert report.validation_ok is True
    assert report.compile_ok is True
    assert report.policy_ok is True
    assert report.quality_ok is False
    assert any("no next transition" in issue for issue in report.issues)


def test_enablement_v1_public_api_exports() -> None:
    import appv24.workflows as workflows

    assert hasattr(workflows, "CapabilityPlanItem")
    assert hasattr(workflows, "WorkflowEnablementV1Report")
    assert hasattr(workflows, "build_workflow_enablement_report")
