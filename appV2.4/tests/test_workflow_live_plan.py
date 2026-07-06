from __future__ import annotations

from pathlib import Path
import shutil

from appv24.workflows.live_plan import build_workflow_live_plan_from_path, format_workflow_live_plan


def test_live_plan_for_mixed_financial_workflow_separates_live_and_local_phases(tmp_path: Path) -> None:
    package = _copy_example(tmp_path, "financial-catalyst-alerts")

    plan = build_workflow_live_plan_from_path(package, "happy_path.yaml")

    assert plan.workflow_name == "financial-catalyst-alerts"
    assert plan.fixture_name == "happy_path.yaml"
    assert plan.status == "partial-live"
    assert plan.live_read_actions == ("fetch_news: source.fetch_items",)
    assert plan.local_only_actions == (
        "extract_catalysts: llm.extract_structured",
        "check_policy: policy.check",
    )
    assert plan.live_draft_actions == ("create_alert_draft: notification.create_draft",)
    assert plan.live_production_actions == ()
    assert plan.transport_gaps == ("notification.create_draft: no built-in live-draft transport",)
    assert plan.approval_actions == ("create_alert_draft: notification.create_draft approval=alert-draft-review",)
    assert plan.phase_sequence == (
        "1 live-read: fetch_news: source.fetch_items",
        "2 local: extract_catalysts: llm.extract_structured, check_policy: policy.check",
        "3 live-draft: create_alert_draft: notification.create_draft (transport gap)",
    )
    assert plan.next_commands == (
        f"/workflow-live-ready live-read {package.resolve()} happy_path.yaml --allow source.fetch_items",
        f"/workflow-run-live-read-to-approval {package.resolve()} happy_path.yaml --allow source.fetch_items,llm.extract_structured",
        f"/workflow-approvals {package.resolve()} <approval-run-id>",
        f"/workflow-live-ready live-draft {package.resolve()} happy_path.yaml <approval-run-id> --allow notification.create_draft",
    )
    assert not (package / ".workflow-runs").exists()

    rendered = format_workflow_live_plan(plan)
    assert "Workflow live plan: partial-live" in rendered
    assert "- fetch_news: source.fetch_items" in rendered
    assert "- extract_catalysts: llm.extract_structured" in rendered
    assert "phase sequence:" in rendered
    assert "- 2 local: extract_catalysts: llm.extract_structured, check_policy: policy.check" in rendered
    assert "no built-in live-draft transport" in rendered


def test_live_plan_for_telegram_production_workflow_reports_production_path(tmp_path: Path) -> None:
    package = _copy_example(tmp_path, "telegram-production-alert")

    plan = build_workflow_live_plan_from_path(package, "happy_path.yaml")

    assert plan.status == "live-production"
    assert plan.live_read_actions == ()
    assert plan.live_draft_actions == ()
    assert plan.live_production_actions == ("notify_admin: telegram.send_message",)
    assert plan.local_only_actions == ()
    assert plan.transport_gaps == ()
    assert plan.approval_actions == ("notify_admin: telegram.send_message approval=operator-approval",)
    assert plan.phase_sequence == ("1 live-production: notify_admin: telegram.send_message",)
    assert plan.next_commands == (
        f"/workflow-run-approval {package.resolve()} happy_path.yaml",
        f"/workflow-approvals {package.resolve()} <approval-run-id>",
        f"/workflow-live-ready live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow telegram.send_message",
        f"/workflow-run-live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow telegram.send_message",
    )


def _copy_example(root: Path, name: str) -> Path:
    source = Path("examples/appv24/workflows") / name
    package = root / name
    shutil.copytree(source, package)
    return package
