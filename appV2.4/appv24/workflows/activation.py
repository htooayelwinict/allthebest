"""Managed local workflow activations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from appv24.workflows.execution import WorkflowRunMode, WorkflowRunReport, WorkflowRunRequest
from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.loader import load_package_documents
from appv24.workflows.run_store import SECRET_VALUE_RE
from appv24.workflows.runner import run_workflow
from appv24.workflows.validator import validate_workflow_documents


ALLOWED_TRIGGER_TYPES = frozenset({"manual", "schedule", "webhook"})


@dataclass(frozen=True)
class WorkflowActivationRecord:
    activation_id: str
    workflow_name: str
    package_path: Path
    package_fingerprint: str
    trigger_type: str
    fixture_name: str
    mode: WorkflowRunMode
    approve_all: bool
    enabled: bool = True
    last_run_id: str | None = None
    last_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["package_path"] = str(self.package_path)
        data["mode"] = self.mode.value
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowActivationRecord":
        return cls(
            activation_id=str(value["activation_id"]),
            workflow_name=str(value["workflow_name"]),
            package_path=Path(str(value["package_path"])),
            package_fingerprint=str(value["package_fingerprint"]),
            trigger_type=str(value["trigger_type"]),
            fixture_name=str(value["fixture_name"]),
            mode=WorkflowRunMode(str(value["mode"])),
            approve_all=bool(value.get("approve_all", False)),
            enabled=bool(value.get("enabled", True)),
            last_run_id=str(value["last_run_id"]) if value.get("last_run_id") else None,
            last_status=str(value["last_status"]) if value.get("last_status") else None,
        )


class WorkflowActivationStore:
    def __init__(self, root: Path):
        self.root = root

    def create_activation(
        self,
        package_path: Path,
        *,
        trigger_type: str = "manual",
        fixture_name: str = "happy_path.yaml",
        mode: WorkflowRunMode = WorkflowRunMode.LOCAL,
        approve_all: bool = False,
        enabled: bool = True,
    ) -> WorkflowActivationRecord:
        trigger_type = str(trigger_type).strip()
        if trigger_type not in ALLOWED_TRIGGER_TYPES:
            raise ValueError(f"unsupported workflow activation trigger: {trigger_type}")
        if mode is not WorkflowRunMode.LOCAL:
            raise ValueError(f"workflow activation mode is not enabled: {mode.value}")

        documents = load_package_documents(package_path)
        validation = validate_workflow_documents(documents)
        if not validation.ok:
            first_message = validation.messages()[0] if validation.messages() else "workflow package validation failed"
            raise ValueError(first_message)
        try:
            require_fixture(documents.dry_run_fixtures, fixture_name)
        except WorkflowPackageError as error:
            raise ValueError(f"unknown dry-run fixture for activation: {fixture_name}") from error

        activation = WorkflowActivationRecord(
            activation_id=f"activation-{uuid4().hex}",
            workflow_name=str(documents.workflow.get("name", "")),
            package_path=documents.root,
            package_fingerprint=fingerprint_package_documents(documents),
            trigger_type=trigger_type,
            fixture_name=fixture_name,
            mode=mode,
            approve_all=approve_all,
            enabled=enabled,
        )
        self._write_activation(activation)
        return activation

    def get_activation(self, activation_id: str) -> WorkflowActivationRecord:
        path = self._activation_dir(activation_id) / "activation.json"
        if not path.is_file():
            raise ValueError(f"unknown workflow activation: {activation_id}")
        return WorkflowActivationRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_activations(self, *, root: Path | None = None) -> tuple[WorkflowActivationRecord, ...]:
        activation_root = root or self.root
        if not activation_root.is_dir():
            return ()
        records: list[WorkflowActivationRecord] = []
        for activation_path in sorted(activation_root.glob("activation-*/activation.json")):
            records.append(WorkflowActivationRecord.from_dict(json.loads(activation_path.read_text(encoding="utf-8"))))
        return tuple(records)

    def list_statuses(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._status_for_activation(activation) for activation in self.list_activations())

    def set_enabled(self, activation_id: str, enabled: bool) -> WorkflowActivationRecord:
        activation = self.get_activation(activation_id)
        updated = replace(activation, enabled=enabled)
        self._write_activation(updated)
        return updated

    def run_activation(self, activation_id: str) -> WorkflowRunReport:
        activation = self.get_activation(activation_id)
        if not activation.enabled:
            raise ValueError(f"workflow activation is disabled: {activation.activation_id}")
        package_status = self._package_status(activation)
        if package_status["package_status"] != "current":
            raise ValueError(
                "workflow activation package fingerprint changed: "
                f"{activation.activation_id} expected {activation.package_fingerprint} "
                f"got {package_status['current_package_fingerprint']}"
            )

        report = run_workflow(
            WorkflowRunRequest(
                package_path=activation.package_path,
                fixture_name=activation.fixture_name,
                mode=activation.mode,
                run_root=self._activation_dir(activation.activation_id) / "runs",
                approve_all=activation.approve_all,
                activation_id=activation.activation_id,
                activation_trigger_type=activation.trigger_type,
            )
        )
        updated = replace(activation, last_run_id=report.run_id, last_status=report.status)
        self._write_activation(updated)
        self._append_history(updated.activation_id, report.run_id)
        return report

    def history(self, activation_id: str) -> tuple[str, ...]:
        self.get_activation(activation_id)
        path = self._activation_dir(activation_id) / "runs.jsonl"
        if not path.is_file():
            return ()
        run_ids: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            run_ids.append(str(value["run_id"]))
        return tuple(run_ids)

    def status(self, activation_id: str) -> dict[str, Any]:
        activation = self.get_activation(activation_id)
        return self._status_for_activation(activation)

    def _status_for_activation(self, activation: WorkflowActivationRecord) -> dict[str, Any]:
        package_status = self._package_status(activation)
        return {
            "activation_id": activation.activation_id,
            "workflow_name": activation.workflow_name,
            "trigger_type": activation.trigger_type,
            "fixture_name": activation.fixture_name,
            "enabled": activation.enabled,
            "last_run_id": activation.last_run_id,
            "last_status": activation.last_status,
            "runs": len(self.history(activation.activation_id)),
            "package_fingerprint": activation.package_fingerprint,
            "current_package_fingerprint": package_status["current_package_fingerprint"],
            "package_status": package_status["package_status"],
        }

    def _package_status(self, activation: WorkflowActivationRecord) -> dict[str, str]:
        current_fingerprint = fingerprint_package_documents(load_package_documents(activation.package_path))
        return {
            "current_package_fingerprint": current_fingerprint,
            "package_status": "current" if current_fingerprint == activation.package_fingerprint else "changed",
        }

    def _activation_dir(self, activation_id: str) -> Path:
        return self.root / activation_id

    def _write_activation(self, activation: WorkflowActivationRecord) -> None:
        activation_dir = self._activation_dir(activation.activation_id)
        activation_dir.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(activation.to_dict(), sort_keys=True, indent=2)
        if SECRET_VALUE_RE.search(encoded):
            raise ValueError("workflow activation contains a raw secret-looking value")
        (activation_dir / "activation.json").write_text(encoded + "\n", encoding="utf-8")

    def _append_history(self, activation_id: str, run_id: str) -> None:
        activation_dir = self._activation_dir(activation_id)
        encoded = json.dumps({"run_id": run_id}, sort_keys=True)
        with (activation_dir / "runs.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")


def format_workflow_activation_list(records: tuple[Mapping[str, Any] | WorkflowActivationRecord, ...]) -> str:
    lines = ["Workflow activations:"]
    if not records:
        lines.append("none")
        return "\n".join(lines)
    for item in records:
        record = _activation_display_mapping(item)
        last_status = record["last_status"] or "none"
        lines.append(
            "- "
            f"{record['activation_id']} "
            f"{record['workflow_name']} "
            f"trigger={record['trigger_type']} "
            f"fixture={record['fixture_name']} "
            f"enabled={record['enabled']} "
            f"package={record['package_status']} "
            f"last={last_status}"
        )
    return "\n".join(lines)


def _activation_display_mapping(item: Mapping[str, Any] | WorkflowActivationRecord) -> Mapping[str, Any]:
    if isinstance(item, WorkflowActivationRecord):
        return {
            "activation_id": item.activation_id,
            "workflow_name": item.workflow_name,
            "trigger_type": item.trigger_type,
            "fixture_name": item.fixture_name,
            "enabled": item.enabled,
            "package_status": "unknown",
            "last_status": item.last_status,
        }
    return item
