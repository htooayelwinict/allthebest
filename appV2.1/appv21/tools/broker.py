"""Tool broker and mutation lease executor for AppV2.1."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from appv21.state.models import MutationLease, MutationReceipt

SENSITIVE_PATH_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".pypirc",
    ".npmrc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
}
SENSITIVE_PATH_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".crt", ".cer")


class ToolBroker:
    def __init__(self, *, root_path: str | Path) -> None:
        self.root = Path(root_path).resolve()
        self._issued_leases: dict[str, MutationLease] = {}

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "repo_snapshot",
                "kind": "observe",
                "trust": "runtime_observed",
                "guidance": "Use before planning; returns file and directory map only.",
            },
            {
                "name": "read_file",
                "kind": "observe",
                "trust": "runtime_observed",
                "guidance": "Use for targeted file evidence; never infer file contents without this.",
            },
        ]

    def validate_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        if tool_name == "repo_snapshot":
            return []
        if tool_name == "read_file":
            path = str(arguments.get("path") or "")
            safe_path = self._safe_path(path)
            if safe_path is None:
                return [f"path_outside_root:{path}"]
            if _sensitive_path(safe_path):
                return [f"sensitive_path_denied:{path}"]
            if not safe_path.is_file():
                return [f"path_not_file:{path}"]
            return []
        return [f"unknown_tool:{tool_name}"]

    def execute_tool_call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        errors = self.validate_tool_call(tool_name, arguments)
        if errors:
            return self.tool_result_envelope(tool_name=tool_name, status="denied", payload={"errors": errors})
        if tool_name == "repo_snapshot":
            result = self.repo_snapshot()
        elif tool_name == "read_file":
            result = self.read_file(str(arguments.get("path") or ""))
        else:
            result = {"status": "denied", "errors": [f"unknown_tool:{tool_name}"]}
        status = str(result.get("status") or "completed")
        payload = {key: value for key, value in result.items() if key not in {"tool_result_id", "tool_name", "status", "trust", "prompt_summary"}}
        return self.tool_result_envelope(
            tool_name=tool_name,
            status=status,
            payload=payload,
            prompt_summary=result.get("prompt_summary") or self.compact_tool_result(result),
            evidence_refs=list(result.get("evidence_refs") or []),
        )

    def tool_result_envelope(
        self,
        *,
        tool_name: str,
        status: str,
        payload: dict[str, Any],
        prompt_summary: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "tool_result_id": f"toolres_{uuid4().hex}",
            "tool_name": tool_name,
            "status": status,
            "trust": "runtime_observed" if tool_name in {"repo_snapshot", "read_file"} else "runtime_owned",
            "payload": payload,
            "prompt_summary": prompt_summary or self.compact_tool_result(payload),
            "evidence_refs": list(evidence_refs or []),
        }

    def compact_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if "content" in result:
            content = str(result.get("content") or "")
            return {"bytes": len(content.encode("utf-8")), "preview": content[:500]}
        if "files" in result:
            return {"file_count": len(result.get("files") or []), "directory_count": len(result.get("directories") or [])}
        return {"keys": sorted(result)[:20]}

    def tool_policy_for(self, _state: Any) -> dict[str, Any]:
        return {
            "mutating_tools_require_lease": True,
            "high_risk_mutations_require_human": True,
            "read_tools": ["repo_snapshot", "read_file"],
        }

    def repo_snapshot(self) -> dict[str, Any]:
        files: list[str] = []
        directories: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if _ignored(path):
                continue
            rel = path.relative_to(self.root).as_posix()
            if path.is_dir():
                directories.append(rel)
            elif path.is_file():
                files.append(rel)
        return {
            "status": "completed",
            "tool_result_id": f"toolres_{uuid4().hex}",
            "tool_name": "repo_snapshot",
            "trust": "runtime_observed",
            "files": files,
            "directories": directories,
            "prompt_summary": {"file_count": len(files), "directory_count": len(directories)},
        }

    def read_file(self, path: str) -> dict[str, Any]:
        target = self._safe_path(path)
        if target is None or not target.is_file():
            return {"status": "failed", "tool_name": "read_file", "path": path, "error": "not_file"}
        if _sensitive_path(target):
            return {
                "status": "denied",
                "tool_name": "read_file",
                "path": path,
                "error": "sensitive_path_denied",
                "prompt_summary": {"path": path, "preview": "[redacted:sensitive_path_denied]"},
            }
        text = target.read_text(encoding="utf-8")
        return {
            "status": "completed",
            "tool_result_id": f"toolres_{uuid4().hex}",
            "tool_name": "read_file",
            "path": path,
            "bytes": len(text.encode("utf-8")),
            "content": text,
            "prompt_summary": {"path": path, "preview": text[:500]},
        }

    def derive_mutation_lease(self, *, operation_batch_id: str, operations: list[dict[str, Any]]) -> MutationLease:
        errors = self.validate_mutation_intent(operations)
        if errors:
            raise ValueError(";".join(errors))
        risk = self.classify_mutation_risk(operations)
        sources: list[str] = []
        destinations: list[str] = []
        for operation in operations:
            action = operation.get("action")
            if action == "move":
                if operation.get("source"):
                    sources.append(str(operation["source"]))
                if operation.get("destination"):
                    destinations.append(str(operation["destination"]))
            elif operation.get("path"):
                destinations.append(str(operation["path"]))
        lease = MutationLease(
            lease_id=f"lease_{uuid4().hex}",
            operation_batch_id=operation_batch_id,
            allowed_operations=operations,
            allowed_sources=sorted(set(sources)),
            allowed_destinations=sorted(set(destinations)),
            risk_level=str(risk["risk_level"]),
            requires_human=bool(risk["requires_human"]),
        )
        self._issued_leases[lease.lease_id] = lease
        return lease

    def classify_mutation_risk(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        reasons: list[str] = []
        destructive_actions = {"delete", "remove", "rm", "rmtree", "unlink"}
        sensitive_names = {".env", ".env.local", ".git", "id_rsa", "id_ed25519"}
        sensitive_suffixes = (".key", ".pem", ".crt", ".p12")
        for operation in operations:
            action = str(operation.get("action") or "")
            declared_risk = str(operation.get("risk_level") or operation.get("risk") or "").lower()
            if action in destructive_actions or bool(operation.get("destructive")):
                reasons.append(f"destructive_action:{action}")
            if declared_risk in {"high", "critical", "destructive"}:
                reasons.append(f"declared_risk:{declared_risk}")
            for key in ("path", "source", "destination"):
                raw_path = operation.get(key)
                if not raw_path:
                    continue
                path = Path(str(raw_path))
                parts = set(path.parts)
                if parts & sensitive_names or path.name in sensitive_names or path.name.endswith(sensitive_suffixes):
                    reasons.append(f"sensitive_path:{raw_path}")
        return {
            "risk_level": "high" if reasons else "low",
            "requires_human": bool(reasons),
            "reasons": sorted(set(reasons)),
        }

    def validate_mutation_intent(self, operations: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for operation in operations:
            action = operation.get("action")
            if action == "move":
                for key in ("source", "destination"):
                    path = str(operation.get(key) or "")
                    if self._safe_path(path) is None:
                        errors.append(f"{key}_outside_root:{path}")
            elif action == "write":
                path = str(operation.get("path") or "")
                if self._safe_path(path) is None:
                    errors.append(f"path_outside_root:{path}")
            else:
                errors.append(f"unsupported_operation:{action}")
        return errors

    def apply_mutation_lease(self, lease: MutationLease) -> MutationReceipt:
        lease_errors = self._validate_lease(lease)
        if lease_errors:
            return MutationReceipt(
                receipt_id=f"mut_{lease.operation_batch_id}",
                lease_id=lease.lease_id,
                status="denied",
                operations=lease.allowed_operations,
                touched_paths=[],
                errors=lease_errors,
            )
        touched: list[str] = []
        errors: list[str] = []
        for operation in lease.allowed_operations:
            action = operation.get("action")
            if action == "move":
                source = self._safe_path(str(operation.get("source") or ""))
                destination = self._safe_path(str(operation.get("destination") or ""))
                if source is None or destination is None:
                    errors.append(f"invalid move path: {operation}")
                    continue
                if not source.exists():
                    errors.append(f"missing source: {operation.get('source')}")
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                touched.extend([operation["source"], operation["destination"]])
            elif action == "write":
                path = self._safe_path(str(operation.get("path") or ""))
                if path is None:
                    errors.append(f"invalid write path: {operation}")
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                content = operation.get("content")
                if not isinstance(content, str):
                    content = json.dumps(content, indent=2, sort_keys=True)
                path.write_text(content, encoding="utf-8")
                touched.append(operation["path"])
            else:
                errors.append(f"unsupported operation: {operation}")
        status = "applied" if not errors else "failed"
        return MutationReceipt(
            receipt_id=f"mut_{lease.operation_batch_id}",
            lease_id=lease.lease_id,
            status=status,
            operations=lease.allowed_operations,
            touched_paths=sorted(set(touched)),
            errors=errors,
        )

    def _validate_lease(self, lease: MutationLease) -> list[str]:
        errors: list[str] = []
        issued = self._issued_leases.get(lease.lease_id)
        if issued is None:
            errors.append("lease_not_issued")
            return errors
        if lease.requires_human:
            errors.append("lease_requires_human_approval")
        issued_operations = [_operation_fingerprint(operation) for operation in issued.allowed_operations]
        allowed_sources = set(issued.allowed_sources)
        allowed_destinations = set(issued.allowed_destinations)
        for operation in lease.allowed_operations:
            if _operation_fingerprint(operation) not in issued_operations:
                errors.append(f"operation_not_in_lease:{operation}")
                continue
            action = operation.get("action")
            if action == "move":
                source = str(operation.get("source") or "")
                destination = str(operation.get("destination") or "")
                if source not in allowed_sources:
                    errors.append(f"source_not_in_lease:{source}")
                if destination not in allowed_destinations:
                    errors.append(f"destination_not_in_lease:{destination}")
            elif operation.get("path"):
                path = str(operation.get("path") or "")
                if path not in allowed_destinations:
                    errors.append(f"path_not_in_lease:{path}")
        return errors

    def _safe_path(self, path: str) -> Path | None:
        if not path:
            return None
        candidate = (self.root / path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate


def _ignored(path: Path) -> bool:
    return any(part in {".git", ".appv21", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"} for part in path.parts)


def _sensitive_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if parts & SENSITIVE_PATH_NAMES or name in SENSITIVE_PATH_NAMES:
        return True
    if name.startswith(".env."):
        return True
    return name.endswith(SENSITIVE_PATH_SUFFIXES)


def _operation_fingerprint(operation: dict[str, Any]) -> str:
    return json.dumps(operation, sort_keys=True, default=str)
