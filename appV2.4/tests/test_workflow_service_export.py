from __future__ import annotations

import json
from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.iac_export import export_deployment_intent
from appv24.workflows.service_export import build_local_service_export, format_local_service_export


def test_local_service_export_builds_webhook_templates_without_secret_values(tmp_path: Path) -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None
    intent = export_deployment_intent(compiled.ir, example.documents)

    export = build_local_service_export(intent, package_path=tmp_path / "telegram_support_ticket")

    assert export.workflow_name == "telegram_support_ticket"
    assert export.status == "exportable"
    assert tuple(service["kind"] for service in export.services) == ("webhook",)
    assert sorted(export.files) == [
        "services.launchd/com.appv24.workflow.telegram-support-ticket.webhook.plist",
        "services.manifest.json",
        "services.systemd/appv24-workflow-telegram-support-ticket-webhook.service",
        "services/run-webhook.sh",
    ]
    manifest = json.loads(export.files["services.manifest.json"])
    assert manifest["version"] == "appv24.local-services/v1"
    assert manifest["workflow_name"] == "telegram_support_ticket"
    assert manifest["services"][0]["command"] == "/workflow-webhook-serve 1000000 127.0.0.1 8765"
    script = export.files["services/run-webhook.sh"]
    assert "APPV24_WORKFLOW_WEBHOOK_SECRET" in script
    assert "appv24 --cwd" in script
    assert "/workflow-webhook-serve 1000000 127.0.0.1 8765" in script
    assert "secret-token" not in "\n".join(export.files.values())


def test_local_service_export_builds_scheduler_template_for_schedule_runtime(tmp_path: Path) -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    workflow["triggers"] = [{"id": "daily_check", "type": "schedule"}]
    documents = example.documents.__class__(
        root=example.documents.root,
        workflow=workflow,
        connectors=example.documents.connectors,
        permissions=example.documents.permissions,
        dry_run_fixtures=example.documents.dry_run_fixtures,
        rollback_text=example.documents.rollback_text,
    )
    compiled = compile_workflow_documents(documents)
    assert compiled.ir is not None
    intent = export_deployment_intent(compiled.ir, documents)

    export = build_local_service_export(intent, package_path=tmp_path / "scheduled_support")

    assert tuple(service["kind"] for service in export.services) == ("scheduler",)
    assert "services/run-scheduler.sh" in export.files
    assert "/workflow-activation-loop 1000000 60" in export.files["services/run-scheduler.sh"]
    assert "appv24-workflow-telegram-support-ticket-scheduler.service" in "\n".join(export.files)


def test_local_service_export_is_empty_for_manual_only_workflow(tmp_path: Path) -> None:
    example = golden_workflow_examples()["gmail_draft_reply"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None
    intent = export_deployment_intent(compiled.ir, example.documents)

    export = build_local_service_export(intent, package_path=tmp_path / "basic_file_report")

    assert export.status == "empty"
    assert export.services == ()
    assert sorted(export.files) == ["services.manifest.json"]
    assert "No local runtime services" in format_local_service_export(export)
