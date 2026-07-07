"""Passive local service exports for appv24 workflow runtime commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re
import shlex

from appv24.workflows.deployment_intent import WorkflowDeploymentIntent


LOCAL_SERVICES_VERSION = "appv24.local-services/v1"


@dataclass(frozen=True)
class LocalServiceExport:
    workflow_name: str
    package_path: Path
    package_fingerprint: str
    status: str
    services: tuple[Mapping[str, str], ...]
    files: Mapping[str, str]


def build_local_service_export(
    intent: WorkflowDeploymentIntent,
    *,
    package_path: str | Path,
    app_command: str = "appv24",
    env_file: str | Path = ".env",
    scheduler_ticks: int = 1_000_000,
    scheduler_interval_seconds: int = 60,
    webhook_max_requests: int = 1_000_000,
    webhook_host: str = "127.0.0.1",
    webhook_port: int = 8765,
) -> LocalServiceExport:
    """Build service templates without installing, starting, or calling connectors."""

    package = Path(package_path).expanduser().resolve()
    slug = _slug(intent.workflow_name)
    services: list[dict[str, str]] = []
    files: dict[str, str] = {}
    command_specs = _command_specs(
        intent,
        slug=slug,
        scheduler_ticks=scheduler_ticks,
        scheduler_interval_seconds=scheduler_interval_seconds,
        webhook_max_requests=webhook_max_requests,
        webhook_host=webhook_host,
        webhook_port=webhook_port,
    )

    for spec in command_specs:
        service = {
            "kind": spec["kind"],
            "service_id": spec["service_id"],
            "command": spec["command"],
            "script": spec["script"],
            "systemd_unit": spec["systemd_unit"],
            "launchd_plist": spec["launchd_plist"],
        }
        services.append(service)
        files[spec["script"]] = _run_script(
            package=package,
            app_command=app_command,
            env_file=env_file,
            command=spec["command"],
            require_webhook_secret=spec["kind"] == "webhook",
        )
        files[spec["systemd_unit"]] = _systemd_unit(package=package, spec=spec)
        files[spec["launchd_plist"]] = _launchd_plist(package=package, spec=spec)

    status = "exportable" if services else "empty"
    files["services.manifest.json"] = _manifest(
        intent=intent,
        package=package,
        status=status,
        services=services,
    )
    return LocalServiceExport(
        workflow_name=intent.workflow_name,
        package_path=package,
        package_fingerprint=intent.package_fingerprint,
        status=status,
        services=tuple(services),
        files=files,
    )


def format_local_service_export(export: LocalServiceExport) -> str:
    lines = [
        f"Workflow local service export: {export.status}",
        f"workflow: {export.workflow_name}",
        f"package: {export.package_path}",
        f"package fingerprint: {export.package_fingerprint}",
    ]
    if not export.services:
        lines.append("No local runtime services.")
        return "\n".join(lines)
    lines.append("services:")
    for service in export.services:
        lines.append(f"- {service['kind']}: {service['command']}")
        lines.append(f"  script: {service['script']}")
    lines.append("side effects: none until an operator installs and starts a service")
    return "\n".join(lines)


def _command_specs(
    intent: WorkflowDeploymentIntent,
    *,
    slug: str,
    scheduler_ticks: int,
    scheduler_interval_seconds: int,
    webhook_max_requests: int,
    webhook_host: str,
    webhook_port: int,
) -> tuple[dict[str, str], ...]:
    specs: list[dict[str, str]] = []
    commands = set(intent.local_runtime_commands)
    if "/workflow-activation-loop <ticks> [interval-seconds]" in commands:
        specs.append(
            _service_spec(
                slug=slug,
                kind="scheduler",
                command=f"/workflow-activation-loop {scheduler_ticks} {scheduler_interval_seconds}",
                script="services/run-scheduler.sh",
            )
        )
    if "/workflow-webhook-serve <max-requests> [host] [port]" in commands:
        specs.append(
            _service_spec(
                slug=slug,
                kind="webhook",
                command=f"/workflow-webhook-serve {webhook_max_requests} {webhook_host} {webhook_port}",
                script="services/run-webhook.sh",
            )
        )
    return tuple(specs)


def _service_spec(*, slug: str, kind: str, command: str, script: str) -> dict[str, str]:
    service_id = f"appv24-workflow-{slug}-{kind}"
    return {
        "kind": kind,
        "service_id": service_id,
        "command": command,
        "script": script,
        "systemd_unit": f"services.systemd/{service_id}.service",
        "launchd_plist": f"services.launchd/com.appv24.workflow.{slug}.{kind}.plist",
    }


def _run_script(
    *,
    package: Path,
    app_command: str,
    env_file: str | Path,
    command: str,
    require_webhook_secret: bool,
) -> str:
    env_path = _env_path(package, env_file)
    lines = [
        "#!/bin/sh",
        "set -eu",
        "",
        f"PACKAGE_DIR={shlex.quote(str(package))}",
        f"ENV_FILE={shlex.quote(str(env_path))}",
        'if [ -n "${APPV24_WORKFLOW_ENV_FILE:-}" ]; then',
        '  ENV_FILE="${APPV24_WORKFLOW_ENV_FILE}"',
        "fi",
        "",
        'cd "${PACKAGE_DIR}"',
        'if [ -f "${ENV_FILE}" ]; then',
        "  set -a",
        '  . "${ENV_FILE}"',
        "  set +a",
        "fi",
        "",
    ]
    if require_webhook_secret:
        lines.append(': "${APPV24_WORKFLOW_WEBHOOK_SECRET:?APPV24_WORKFLOW_WEBHOOK_SECRET is required}"')
        lines.append("")
    lines.append(f"exec {shlex.quote(app_command)} --cwd \"${{PACKAGE_DIR}}\" {shlex.quote(command)}")
    return "\n".join(lines) + "\n"


def _systemd_unit(*, package: Path, spec: Mapping[str, str]) -> str:
    script = package / ".workflow-bundle" / spec["script"]
    return (
        "[Unit]\n"
        f"Description=appv24 workflow {spec['kind']} service for {spec['service_id']}\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={package}\n"
        f"ExecStart=/bin/sh {script}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        f"EnvironmentFile=-{package / '.env'}\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _launchd_plist(*, package: Path, spec: Mapping[str, str]) -> str:
    script = package / ".workflow-bundle" / spec["script"]
    label = f"com.appv24.workflow.{spec['service_id'].removeprefix('appv24-workflow-')}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        f"  <string>{escape(label)}</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        "    <string>/bin/sh</string>\n"
        f"    <string>{escape(str(script))}</string>\n"
        "  </array>\n"
        "  <key>WorkingDirectory</key>\n"
        f"  <string>{escape(str(package))}</string>\n"
        "  <key>KeepAlive</key>\n"
        "  <true/>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _manifest(
    *,
    intent: WorkflowDeploymentIntent,
    package: Path,
    status: str,
    services: list[dict[str, str]],
) -> str:
    return json.dumps(
        {
            "version": LOCAL_SERVICES_VERSION,
            "workflow_name": intent.workflow_name,
            "package_path": str(package),
            "package_fingerprint": intent.package_fingerprint,
            "status": status,
            "side_effects": "none until an operator installs and starts a service",
            "services": services,
        },
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"


def _env_path(package: Path, env_file: str | Path) -> Path:
    path = Path(env_file)
    if path.is_absolute():
        return path
    return package / path


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "workflow"
