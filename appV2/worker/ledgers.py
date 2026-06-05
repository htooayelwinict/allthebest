"""Append-only ledgers for AppV2 worker execution."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from appV2.schemas import ArtifactRecord, FileOperation


class ArtifactLedger:
    def __init__(self, artifacts: list[ArtifactRecord] | None = None) -> None:
        self._records: list[ArtifactRecord] = list(artifacts or [])

    def append(self, record: ArtifactRecord) -> ArtifactRecord:
        self._records.append(record)
        return record

    def extend(self, records: list[ArtifactRecord]) -> None:
        for record in records:
            self.append(record)

    def all(self) -> list[ArtifactRecord]:
        return list(self._records)

    def by_id(self, artifact_id: str) -> ArtifactRecord | None:
        for record in reversed(self._records):
            if record.id == artifact_id:
                return record
        return None

    def completed_by_id(self, artifact_id: str) -> ArtifactRecord | None:
        for record in reversed(self._records):
            if record.id == artifact_id and record.lifecycle == "completed":
                return record
        return None

    def completed(self) -> list[ArtifactRecord]:
        return [record for record in self._records if record.lifecycle == "completed"]

    def evidence(self) -> list[ArtifactRecord]:
        return [
            record
            for record in self._records
            if record.kind in {"tool_observation", "verification_evidence", "mutation_record"}
        ]

    def context_records(self, artifact_ids: list[str]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for artifact_id in artifact_ids:
            record = self.completed_by_id(artifact_id) or self.by_id(artifact_id)
            if record is None or record.id in seen:
                continue
            seen.add(record.id)
            records.append(record.model_dump(mode="json"))
        return records

    def compact_view(self, *, phase_id: str | None = None, limit: int = 12) -> dict[str, Any]:
        records = self._records[-limit:]
        return {
            "total_records": len(self._records),
            "completed_ids": [record.id for record in self.completed()],
            "recent": [
                {
                    "id": record.id,
                    "kind": record.kind,
                    "producer": record.producer,
                    "phase_id": record.phase_id,
                    "lifecycle": record.lifecycle,
                    "summary": _summarize_content(record.content),
                }
                for record in records
                if phase_id is None or record.phase_id in {None, phase_id}
            ],
        }


@dataclass
class MutationRecord:
    operation_batch_id: str
    phase_id: str
    proposed_operations: list[FileOperation]
    applied_operations: list[dict[str, Any]] = field(default_factory=list)
    denied_operations: list[dict[str, Any]] = field(default_factory=list)
    preimages: dict[str, str | None] = field(default_factory=dict)
    postimages: dict[str, str | None] = field(default_factory=dict)
    touched_paths: list[str] = field(default_factory=list)

    def to_artifact(self) -> ArtifactRecord:
        return ArtifactRecord(
            id=f"mutation_{self.operation_batch_id}",
            kind="mutation_record",
            content={
                "operation_batch_id": self.operation_batch_id,
                "applied_operations": self.applied_operations,
                "denied_operations": self.denied_operations,
                "touched_paths": self.touched_paths,
                "patch_diff": self.patch_diff(),
                "rollback_patch": self.rollback_patch(),
            },
            producer="appv2_mutation_ledger",
            phase_id=self.phase_id,
            trust_level="runtime_verified",
            lifecycle="completed" if self.applied_operations else "failed",
        )

    def patch_diff(self) -> str:
        chunks: list[str] = []
        for path in self.touched_paths:
            before = (self.preimages.get(path) or "").splitlines(keepends=True)
            after = (self.postimages.get(path) or "").splitlines(keepends=True)
            chunks.extend(difflib.unified_diff(before, after, fromfile=f"a/{path}", tofile=f"b/{path}"))
        return "".join(chunks)

    def rollback_patch(self) -> str:
        chunks: list[str] = []
        for path in self.touched_paths:
            before = (self.postimages.get(path) or "").splitlines(keepends=True)
            after = (self.preimages.get(path) or "").splitlines(keepends=True)
            chunks.extend(difflib.unified_diff(before, after, fromfile=f"b/{path}", tofile=f"a/{path}"))
        return "".join(chunks)


class MutationLedger:
    def __init__(self, records: list[MutationRecord] | None = None) -> None:
        self._records: list[MutationRecord] = list(records or [])

    def append(self, record: MutationRecord) -> MutationRecord:
        self._records.append(record)
        return record

    def all(self) -> list[MutationRecord]:
        return list(self._records)

    def compact_view(self) -> dict[str, Any]:
        return {
            "mutation_count": len(self._records),
            "touched_paths": sorted({path for record in self._records for path in record.touched_paths}),
            "denial_count": sum(len(record.denied_operations) for record in self._records),
        }


def snapshot_preimages(root: Path, operations: list[FileOperation]) -> dict[str, str | None]:
    paths = sorted(_operation_paths(root, operations))
    snapshots: dict[str, str | None] = {}
    for path in paths:
        target = (root / path).resolve()
        if target.exists() and target.is_file():
            try:
                snapshots[path] = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                snapshots[path] = None
        else:
            snapshots[path] = None
    return snapshots


def snapshot_postimages(root: Path, paths: list[str]) -> dict[str, str | None]:
    snapshots: dict[str, str | None] = {}
    for path in sorted(set(paths)):
        target = (root / path).resolve()
        if target.exists() and target.is_file():
            try:
                snapshots[path] = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                snapshots[path] = None
        else:
            snapshots[path] = None
    return snapshots


def _operation_paths(root: Path, operations: list[FileOperation]) -> set[str]:
    paths: set[str] = set()
    for operation in operations:
        for value in (operation.path, operation.source, operation.destination):
            if value:
                normalized = _normalize_repo_path(root, value)
                if normalized is not None:
                    paths.add(normalized)
    return paths


def _normalize_repo_path(root: Path, value: str) -> str | None:
    candidate = Path(value)
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return target.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _summarize_content(content: Any) -> str:
    if content is None:
        return "null"
    if isinstance(content, str):
        return content[:180]
    if isinstance(content, dict):
        preview = {key: content[key] for key in list(content.keys())[:4]}
        return str(preview)[:180]
    if isinstance(content, list):
        preview = content[:3]
        return f"{len(content)} items: {str(preview)[:140]}"
    return str(content)[:180]
