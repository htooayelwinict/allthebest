from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from appv24.workflows.bundle_export import (
    build_workflow_bundle,
    check_workflow_bundle,
    format_workflow_bundle_check,
    format_workflow_bundle_export,
    write_workflow_bundle,
)


def test_build_workflow_bundle_exports_runtime_artifacts_without_secret_values(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)

    bundle = build_workflow_bundle(
        package,
        env={
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true",
            "APPV24_TELEGRAM_BOT_TOKEN": "secret-token",
            "APPV24_TELEGRAM_CREDENTIAL_REF": "telegram-prod",
            "APPV24_TELEGRAM_SCOPES": "telegram:send",
        },
    )

    assert bundle.workflow_name == "telegram-production-alert"
    assert bundle.package_fingerprint.startswith("sha256:")
    assert bundle.bundle_id == f"bundle-{bundle.package_fingerprint.removeprefix('sha256:')[:12]}"
    assert sorted(bundle.files) == [
        "README.md",
        "bundle.manifest.json",
        "connectors.manifest.json",
        "live.env.example",
        "policy.json",
        "runtime.json",
        "secrets.required.json",
        "triggers.json",
        "workflow.ir.json",
    ]

    workflow_ir = json.loads(bundle.files["workflow.ir.json"])
    assert workflow_ir["workflow_name"] == "telegram-production-alert"
    assert workflow_ir["entrypoint"] == "notify_admin"
    assert workflow_ir["steps"] == [
        {
            "approval": "admin_approval",
            "branches": {},
            "id": "notify_admin",
            "kind": "action",
            "next": "done",
            "side_effect_class": "send",
            "uses": "telegram.send_message",
        },
        {
            "approval": None,
            "branches": {},
            "id": "done",
            "kind": "terminal",
            "next": None,
            "side_effect_class": None,
            "uses": None,
        },
    ]

    connector_manifest = json.loads(bundle.files["connectors.manifest.json"])
    assert connector_manifest["actions"][0]["action_ref"] == "telegram.send_message"
    assert connector_manifest["actions"][0]["live_modes"] == ["live-production"]
    assert connector_manifest["actions"][0]["configured_live_modes"] == ["live-production"]

    secrets = json.loads(bundle.files["secrets.required.json"])
    assert secrets["required_credential_env"] == [
        "APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES=telegram:send"
    ]
    assert secrets["required_transport_env"] == [
        "live-production: APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, APPV24_TELEGRAM_BOT_TOKEN"
    ]

    runtime = json.loads(bundle.files["runtime.json"])
    assert runtime["runtime_mode"] == "export-only"
    assert runtime["execution_status"] == "not-started"
    assert runtime["side_effects"] == "none"

    assert "secret-token" not in "\n".join(bundle.files.values())
    assert not (package / ".workflow-runs").exists()


def test_workflow_bundle_exports_file_checksum_manifest(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)

    bundle = build_workflow_bundle(package, env={})

    manifest = json.loads(bundle.files["bundle.manifest.json"])
    assert manifest["version"] == "appv24.workflow-bundle-manifest/v1"
    assert manifest["bundle_id"] == bundle.bundle_id
    assert manifest["package_fingerprint"] == bundle.package_fingerprint
    assert sorted(manifest["files"]) == sorted(filename for filename in bundle.files if filename != "bundle.manifest.json")
    runtime_digest = hashlib.sha256(bundle.files["runtime.json"].encode("utf-8")).hexdigest()
    assert manifest["files"]["runtime.json"] == f"sha256:{runtime_digest}"
    assert "bundle.manifest.json" not in manifest["files"]


def test_workflow_bundle_exports_safe_live_env_example(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)

    bundle = build_workflow_bundle(
        package,
        env={
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true",
            "APPV24_TELEGRAM_BOT_TOKEN": "secret-token",
            "APPV24_TELEGRAM_CREDENTIAL_REF": "telegram-prod",
            "APPV24_TELEGRAM_SCOPES": "telegram:send",
        },
    )

    env_example = bundle.files["live.env.example"]
    assert "Workflow live env template: live-production" in env_example
    assert "APPV24_TELEGRAM_CREDENTIAL_REF=<credential-ref>" in env_example
    assert "APPV24_TELEGRAM_SCOPES=telegram:send" in env_example
    assert "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true" in env_example
    assert "APPV24_TELEGRAM_BOT_TOKEN=<telegram-bot-token>" in env_example
    assert "secret-token" not in env_example


def test_workflow_bundle_exports_live_path_next_commands(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)

    bundle = build_workflow_bundle(package, env={})

    runtime = json.loads(bundle.files["runtime.json"])
    expected_commands = [
        f"/workflow-run-approval {package.resolve()} happy_path.yaml",
        f"/workflow-approvals {package.resolve()} <approval-run-id>",
        f"/workflow-live-ready live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow telegram.send_message",
        f"/workflow-run-live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow telegram.send_message",
    ]
    assert runtime["live_plan_status"] == "live-production"
    assert runtime["next_commands"] == expected_commands
    assert bundle.next_commands == tuple(expected_commands)
    readme = bundle.files["README.md"]
    rendered = format_workflow_bundle_export(bundle)
    assert "## Next commands" in readme
    assert f"- {expected_commands[0]}" in readme
    assert "next commands:" in rendered
    assert f"- {expected_commands[-1]}" in rendered


def test_write_workflow_bundle_persists_expected_files_without_running_workflow(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)
    output_dir = tmp_path / "bundle"

    bundle = write_workflow_bundle(package, output_dir=output_dir, env={})

    assert bundle.output_dir == output_dir.resolve()
    for filename in bundle.files:
        assert (output_dir / filename).is_file()
    assert json.loads((output_dir / "runtime.json").read_text(encoding="utf-8"))["bundle_id"] == bundle.bundle_id
    assert (output_dir / "README.md").read_text(encoding="utf-8").startswith("# appv24 workflow bundle")
    assert not (package / ".workflow-runs").exists()


def test_check_workflow_bundle_passes_for_clean_export(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)
    output_dir = tmp_path / "bundle"
    write_workflow_bundle(package, output_dir=output_dir, env={})

    report = check_workflow_bundle(output_dir)

    assert report.ok is True
    assert report.issues == ()
    assert "runtime.json" in report.files_checked
    rendered = format_workflow_bundle_check(report)
    assert "Workflow bundle check: passed" in rendered
    assert f"bundle: {output_dir.resolve()}" in rendered


def test_check_workflow_bundle_reports_tampered_file(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)
    output_dir = tmp_path / "bundle"
    write_workflow_bundle(package, output_dir=output_dir, env={})
    (output_dir / "runtime.json").write_text('{"tampered": true}\n', encoding="utf-8")

    report = check_workflow_bundle(output_dir)

    assert report.ok is False
    assert report.files_checked == ()
    assert report.issues == ("checksum mismatch: runtime.json",)
    assert "Workflow bundle check: failed" in format_workflow_bundle_check(report)


def test_check_workflow_bundle_reports_changed_source_package(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)
    output_dir = tmp_path / "bundle"
    write_workflow_bundle(package, output_dir=output_dir, env={})
    (package / "rollback.md").write_text("# Rollback\n\nChanged after export.\n", encoding="utf-8")

    report = check_workflow_bundle(output_dir)

    assert report.ok is False
    assert report.files_checked == ()
    assert len(report.issues) == 1
    assert report.issues[0].startswith("source package fingerprint changed:")
    assert "expected sha256:" in report.issues[0]
    assert "got sha256:" in report.issues[0]


def _write_telegram_package(root: Path) -> Path:
    package = root / "telegram-production-alert"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "telegram-production-alert",
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "notify_admin",
                "steps": [
                    {
                        "id": "notify_admin",
                        "uses": "telegram.send_message",
                        "side_effect_class": "send",
                        "approval": "admin_approval",
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
                "design": {
                    "intent": "Send one approved Telegram alert.",
                    "trigger": "Manual operator run.",
                    "inputs": ["chat_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["Telegram credentials are resolved outside the package."],
                    "side_effects": ["send Telegram message"],
                    "approval_gates": ["admin_approval"],
                    "failure_modes": ["Telegram API unavailable."],
                    "rollback": ["Send a corrective message manually."],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "send_message",
                                "operationId": "send_message",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "execution_mode": "brokered",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "idempotency": {"key_fields": ["chat_id", "text"]},
                                "required_scopes": ["telegram:send"],
                                "outputs": {"allowed": ["message_id", "chat_id"]},
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [{"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]}],
                "approvals": [{"id": "admin_approval", "mode": "explicit", "required_for": ["notify_admin"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  chat_id: admin-chat\n  text: alert\n", encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text(
        "input:\n  chat_id: admin-chat\n  text: rejected alert\nexpected:\n  status: rejected\n",
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nSend a corrective message manually.\n", encoding="utf-8")
    return package
