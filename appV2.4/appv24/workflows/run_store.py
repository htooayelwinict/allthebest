"""Append-only workflow run storage."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import re
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from appv24.workflows.approval_gate import ApprovalDecision, ApprovalRequest, resume_approval
from appv24.workflows.execution import WorkflowRunEvent

SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9_])(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")


class WorkflowRunStore:
    def __init__(self, root: Path):
        self.root = root

    def run_dir(self, run_id: str) -> Path:
        run_dir = self.root / run_id
        if not run_dir.is_dir():
            raise ValueError(f"unknown workflow run: {run_id}")
        return run_dir

    def create_run(
        self,
        workflow_name: str,
        package_fingerprint: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        run_id = f"run-{uuid4().hex}"
        run_dir = self.root / run_id
        run_dir.mkdir()
        payload = {
            "run_id": run_id,
            "workflow_name": workflow_name,
            "package_fingerprint": package_fingerprint,
        }
        if metadata:
            payload.update(_json_ready(metadata))
        encoded = json.dumps(payload, sort_keys=True, indent=2)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow run metadata contains a raw secret-looking value")
        (run_dir / "run.json").write_text(encoded + "\n", encoding="utf-8")
        (run_dir / "events.jsonl").write_text("", encoding="utf-8")
        return run_id

    def append_event(self, event: WorkflowRunEvent) -> None:
        run_dir = self.root / event.run_id
        if not run_dir.is_dir():
            raise ValueError(f"unknown workflow run: {event.run_id}")
        encoded = json.dumps(event.to_dict(), sort_keys=True)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow run event contains a raw secret-looking value")
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")

    def read_events(self, run_id: str) -> tuple[WorkflowRunEvent, ...]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.is_file():
            raise ValueError(f"unknown workflow run: {run_id}")
        events: list[WorkflowRunEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(WorkflowRunEvent.from_dict(json.loads(line)))
        return tuple(events)

    def read_run_manifest(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "run.json"
        if not path.is_file():
            raise ValueError(f"unknown workflow run: {run_id}")
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("workflow run manifest is malformed")
        return {str(key): value for key, value in parsed.items()}

    def append_approval_decision(self, request: ApprovalRequest, decision: ApprovalDecision) -> dict[str, Any]:
        payload = _approval_decision_payload(request, decision)
        self.run_dir(request.run_id)
        encoded = json.dumps(_json_ready(payload), sort_keys=True)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow approval decision contains a raw secret-looking value")
        with (self.run_dir(request.run_id) / "approval_decisions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        return payload

    def read_approval_decisions(self, run_id: str) -> tuple[dict[str, Any], ...]:
        path = self.run_dir(run_id) / "approval_decisions.jsonl"
        if not path.is_file():
            return ()
        decisions: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    decisions.append({str(key): value for key, value in parsed.items()})
        return tuple(decisions)

    def read_approval_requests(self, run_id: str) -> tuple[ApprovalRequest, ...]:
        path = self.run_dir(run_id) / "approvals.json"
        if not path.is_file():
            return ()
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("workflow approvals file is malformed")
        raw_requests = parsed.get("approval_requests", ())
        if not isinstance(raw_requests, list):
            raise ValueError("workflow approval requests are malformed")
        return tuple(_approval_request_from_mapping(raw) for raw in raw_requests)

    def record_approval_decision(
        self,
        run_id: str,
        approval_request_id: str,
        decision: ApprovalDecision,
    ) -> dict[str, Any]:
        for request in self.read_approval_requests(run_id):
            if request.approval_request_id == approval_request_id:
                return self.append_approval_decision(request, decision)
        raise ValueError(f"unknown workflow approval request: {approval_request_id}")

    def artifact_root(self, run_id: str) -> Path:
        artifact_root = self.run_dir(run_id) / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        return artifact_root

    def write_trace(self, run_id: str, trace: object) -> None:
        self._write_json(run_id, "trace.json", {"trace": _json_ready(trace)})

    def write_approvals(
        self,
        run_id: str,
        approvals: object,
        *,
        approval_requests: object = (),
    ) -> None:
        self._write_json(
            run_id,
            "approvals.json",
            {
                "approvals": _json_ready(approvals),
                "approval_requests": _json_ready(approval_requests),
            },
        )

    def write_report(self, run_id: str, report: Mapping[str, object]) -> None:
        self._write_json(run_id, "report.json", report)

    def write_handoff(self, run_id: str, handoff: Mapping[str, object]) -> None:
        self._write_json(run_id, "handoff.json", handoff)

    def _write_json(self, run_id: str, filename: str, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(_json_ready(payload), sort_keys=True, indent=2)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow run event contains a raw secret-looking value")
        (self.run_dir(run_id) / filename).write_text(encoded + "\n", encoding="utf-8")


def _json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    return value


def _approval_request_from_mapping(raw: Any) -> ApprovalRequest:
    if not isinstance(raw, Mapping):
        raise ValueError("workflow approval request entry is malformed")
    try:
        return ApprovalRequest(
            approval_request_id=str(raw["approval_request_id"]),
            run_id=str(raw["run_id"]),
            package_fingerprint=str(raw["package_fingerprint"]),
            step_id=str(raw["step_id"]),
            connector_action=str(raw["connector_action"]),
            approval_id=str(raw["approval_id"]),
            allowed_outputs=_string_tuple(raw["allowed_outputs"]),
            idempotency_key_fields=_string_tuple(raw["idempotency_key_fields"]),
            idempotency_key=None if raw.get("idempotency_key") is None else str(raw["idempotency_key"]),
            payload_hash=None if raw.get("payload_hash") is None else str(raw["payload_hash"]),
            payload_preview_path=None if raw.get("payload_preview_path") is None else str(raw["payload_preview_path"]),
            status=str(raw.get("status", "pending")),
        )
    except KeyError as error:
        raise ValueError(f"workflow approval request is missing field: {error.args[0]}") from error


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("workflow approval request tuple field is malformed")
    return tuple(str(item) for item in value)


def _approval_decision_payload(request: ApprovalRequest, decision: ApprovalDecision) -> dict[str, Any]:
    updated_request = resume_approval(request, decision)
    return {
        "approval_request_id": request.approval_request_id,
        "run_id": request.run_id,
        "approval_id": request.approval_id,
        "step_id": request.step_id,
        "connector_action": request.connector_action,
        "approved": decision.approved,
        "status": updated_request.status,
        "package_fingerprint": decision.package_fingerprint,
        "decided_by": decision.decided_by,
        "idempotency_key": request.idempotency_key,
        "payload_hash": request.payload_hash,
        "payload_preview_path": request.payload_preview_path,
    }
