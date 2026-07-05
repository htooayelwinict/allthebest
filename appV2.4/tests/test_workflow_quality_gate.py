from __future__ import annotations

from dataclasses import replace

from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.quality_gate import check_workflow_quality_documents


def test_quality_gate_rejects_action_without_next() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    steps = [dict(step) for step in workflow["steps"]]
    steps[1].pop("next")
    workflow["steps"] = steps
    documents = replace(example.documents, workflow=workflow)

    result = check_workflow_quality_documents(documents, broker=example.broker)

    assert not result.ok
    assert [issue.code for issue in result.issues] == ["missing_transition"]
    assert result.issues[0].path == ("steps", "create_ticket_draft", "next")
