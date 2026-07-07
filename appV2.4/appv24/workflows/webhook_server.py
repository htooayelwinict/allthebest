"""Foreground local HTTP webhook server for workflow activations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from appv24.workflows.activation import WorkflowActivationStore
from appv24.workflows.webhook_ingress import WebhookReplayStore, handle_local_webhook_run


@dataclass(frozen=True)
class WorkflowWebhookServerConfig:
    activation_root: Path
    replay_root: Path
    shared_secret: str
    host: str = "127.0.0.1"
    port: int = 8765
    path_prefix: str = "/workflow-webhook/"
    max_body_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class WorkflowWebhookServerReport:
    status: str
    host: str
    port: int
    handled: int
    ran: int
    blocked: int


def format_workflow_webhook_server_report(report: WorkflowWebhookServerReport) -> str:
    return "\n".join(
        [
            f"Workflow webhook server: {report.status}",
            f"host: {report.host}",
            f"port: {report.port}",
            f"handled: {report.handled}",
            f"ran: {report.ran}",
            f"blocked: {report.blocked}",
        ]
    )


def serve_workflow_webhooks(
    config: WorkflowWebhookServerConfig,
    *,
    max_requests: int | None = None,
    on_ready: Callable[[str, int], None] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> WorkflowWebhookServerReport:
    stats = {"handled": 0, "ran": 0, "blocked": 0}
    store = WorkflowActivationStore(Path(config.activation_root))
    replay_store = WebhookReplayStore(Path(config.replay_root))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler hook.
            status_code, payload = _handle_post(
                self,
                config=config,
                store=store,
                replay_store=replay_store,
                now_fn=now_fn,
            )
            stats["handled"] += 1
            if status_code == 200 and payload.get("status") == "ran":
                stats["ran"] += 1
            else:
                stats["blocked"] += 1
            _write_json(self, status_code, payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature.
            return

    server = HTTPServer((config.host, int(config.port)), Handler)
    host, port = server.server_address
    if on_ready is not None:
        on_ready(str(host), int(port))
    try:
        if max_requests is None:
            server.serve_forever()
        else:
            for _ in range(max(0, int(max_requests))):
                server.handle_request()
    finally:
        server.server_close()

    status = "blocked" if stats["blocked"] else "ran" if stats["ran"] else "idle"
    return WorkflowWebhookServerReport(
        status=status,
        host=str(host),
        port=int(port),
        handled=stats["handled"],
        ran=stats["ran"],
        blocked=stats["blocked"],
    )


def _handle_post(
    request: BaseHTTPRequestHandler,
    *,
    config: WorkflowWebhookServerConfig,
    store: WorkflowActivationStore,
    replay_store: WebhookReplayStore,
    now_fn: Callable[[], datetime] | None,
) -> tuple[int, dict[str, object]]:
    activation_id, path_issue = _activation_id_from_path(request.path, prefix=config.path_prefix)
    if path_issue is not None:
        return 404, {"status": "blocked", "reason": path_issue}

    content_length_text = request.headers.get("Content-Length")
    try:
        content_length = int(content_length_text or "0")
    except ValueError:
        return 400, {"status": "blocked", "reason": "invalid content length"}
    if content_length > config.max_body_bytes:
        return 413, {"status": "blocked", "reason": "webhook payload too large"}

    payload = request.rfile.read(content_length)
    try:
        decision = handle_local_webhook_run(
            store,
            activation_id,
            payload=payload,
            headers=_headers_dict(request),
            shared_secret=config.shared_secret,
            replay_store=replay_store,
            now=now_fn() if now_fn is not None else None,
        )
    except ValueError as error:
        return 404, {"status": "blocked", "activation_id": activation_id, "reason": str(error)}

    status_code = 200 if decision.status == "ran" else 403
    return status_code, {
        "activation_id": decision.activation_id,
        "status": decision.status,
        "reason": decision.reason,
        "replay_key": decision.replay_key,
        "run_id": decision.run_id,
        "run_status": decision.run_status,
    }


def _activation_id_from_path(path: str, *, prefix: str) -> tuple[str, str | None]:
    parsed_path = unquote(urlparse(path).path)
    if not parsed_path.startswith(prefix):
        return "", "unknown webhook path"
    activation_id = parsed_path[len(prefix) :].strip()
    if not activation_id or "/" in activation_id or "\\" in activation_id or ".." in activation_id:
        return "", "invalid activation id"
    if not activation_id.startswith("activation-"):
        return "", "invalid activation id"
    return activation_id, None


def _headers_dict(request: BaseHTTPRequestHandler) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in request.headers.items()}


def _write_json(request: BaseHTTPRequestHandler, status_code: int, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    request.send_response(status_code)
    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(encoded)))
    request.end_headers()
    request.wfile.write(encoded)
