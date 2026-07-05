from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.quality_gate import check_workflow_quality_documents


def _documents_with_workflow(workflow: dict, *, permissions: dict | None = None):
    example = golden_workflow_examples()["telegram_support_ticket"]
    return replace(
        example.documents,
        workflow=workflow,
        permissions=example.documents.permissions if permissions is None else permissions,
    )


def _golden_workflow_copy() -> dict:
    example = golden_workflow_examples()["telegram_support_ticket"]
    return deepcopy(example.documents.workflow)


def test_quality_gate_rejects_action_without_next() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    steps = [dict(step) for step in workflow["steps"]]
    steps[1].pop("next")
    workflow["steps"] = steps
    documents = replace(example.documents, workflow=workflow)

    result = check_workflow_quality_documents(documents, broker=example.broker)

    assert not result.ok
    missing_transition = [issue for issue in result.issues if issue.code == "missing_transition"]
    assert len(missing_transition) == 1
    assert missing_transition[0].path == ("steps", "create_ticket_draft", "next")


def test_quality_gate_rejects_decision_without_branches() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"][0].pop("branches")
    documents = _documents_with_workflow(workflow)

    result = check_workflow_quality_documents(documents)

    assert "missing_branch_transition" in {issue.code for issue in result.issues}


def test_quality_gate_rejects_unreachable_non_terminal_step() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"].append(
        {
            "id": "orphan_review",
            "type": "action",
            "uses": "ticket.create_draft",
            "side_effect_class": "create",
            "approval": "admin_approval",
            "next": "done",
        }
    )
    example = golden_workflow_examples()["telegram_support_ticket"]
    permissions = deepcopy(example.documents.permissions)
    permissions["approvals"][0]["required_for"].append("orphan_review")
    documents = _documents_with_workflow(workflow, permissions=permissions)

    result = check_workflow_quality_documents(documents)

    assert "unreachable_step" in {issue.code for issue in result.issues}


def test_quality_gate_rejects_entrypoint_path_without_reachable_terminal() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"] = [
        {
            "id": "check_authorized_customer",
            "type": "decision",
            "branches": {"authorized": "create_ticket_draft"},
        },
        {
            "id": "create_ticket_draft",
            "type": "action",
            "uses": "ticket.create_draft",
            "side_effect_class": "create",
            "approval": "admin_approval",
            "next": "notify_admin",
        },
        {
            "id": "notify_admin",
            "type": "action",
            "uses": "telegram.send_message",
            "side_effect_class": "send",
            "approval": "admin_approval",
            "next": "create_ticket_draft",
        },
        {"id": "done", "type": "terminal"},
    ]
    documents = _documents_with_workflow(workflow)

    result = check_workflow_quality_documents(documents)

    codes = {issue.code for issue in result.issues}
    assert "terminal_unreachable" in codes
    assert "cycle_without_terminal_escape" in codes


def test_quality_gate_allows_cycle_with_terminal_escape() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"][1]["next"] = "review_loop"
    workflow["steps"].insert(
        2,
        {
            "id": "review_loop",
            "type": "decision",
            "branches": {
                "retry": "create_ticket_draft",
                "finish": "notify_admin",
            },
        },
    )
    documents = _documents_with_workflow(workflow)

    result = check_workflow_quality_documents(documents)

    assert "cycle_without_terminal_escape" not in {issue.code for issue in result.issues}
