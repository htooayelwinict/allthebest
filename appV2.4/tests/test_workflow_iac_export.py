from __future__ import annotations

import yaml

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.iac_export import export_deployment_intent, export_deployment_intent_from_path, format_deployment_intent


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
    assert intent.approval_policy == (
        "admin_approval: create_ticket_draft(ticket.create_draft), notify_admin(telegram.send_message)",
    )
    assert intent.rollback_required is True
    assert intent.webhook_triggers == ("telegram_message",)


def test_deployment_intent_formats_operator_checklist(tmp_path) -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    package = tmp_path / "telegram_support_ticket"
    package.mkdir()
    (package / "workflow.yaml").write_text(yaml.safe_dump(dict(example.documents.workflow), sort_keys=False), encoding="utf-8")
    (package / "connectors.yaml").write_text(yaml.safe_dump(dict(example.documents.connectors), sort_keys=False), encoding="utf-8")
    (package / "permissions.yaml").write_text(yaml.safe_dump(dict(example.documents.permissions), sort_keys=False), encoding="utf-8")
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    for name, fixture in example.documents.dry_run_fixtures.items():
        (fixtures / name).write_text(yaml.safe_dump(dict(fixture), sort_keys=False), encoding="utf-8")
    (package / "rollback.md").write_text(example.documents.rollback_text or "", encoding="utf-8")

    intent = export_deployment_intent_from_path(package)
    text = format_deployment_intent(intent)

    assert "Deployment intent: telegram_support_ticket" in text
    assert "- APPV24_TICKET_CREDENTIAL_REF" in text
    assert "- APPV24_TELEGRAM_SCOPES" in text
    assert "- ticket: ticket:draft" in text
    assert "- webhook: telegram_message" in text
    assert "- admin_approval: create_ticket_draft(ticket.create_draft), notify_admin(telegram.send_message)" in text
    assert "rollback required: yes" in text
