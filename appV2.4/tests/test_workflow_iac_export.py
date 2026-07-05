from __future__ import annotations

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.iac_export import export_deployment_intent


def test_deployment_intent_reports_scopes_approvals_and_rollback() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None

    intent = export_deployment_intent(compiled.ir, example.documents)

    assert intent.workflow_name == "telegram_support_ticket"
    assert intent.connector_scopes == {
        "ticket": ("ticket:draft",),
        "telegram": ("telegram:send",),
    }
    assert intent.approval_ids == ("admin_approval",)
    assert intent.rollback_required is True
    assert intent.webhook_triggers == ("telegram_message",)
