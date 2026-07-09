"""Build-time coding-profile handoff artifacts for workflow extensions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any

import yaml

from appv24.workflows.errors import WorkflowIssue
from appv24.workflows.extension_qa import validate_extension_qa_manifest
from appv24.workflows.extension_planner import (
    CatalogRegistration,
    WorkflowExtensionBuildRequest,
    build_extension_requests_for_validation_issues,
    render_extension_prd_prompt,
)
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.loader import PackageDocuments, load_package_documents

QA_REQUIRED_FIELDS = (
    "version: appv24.extension_qa/v1",
    "status: passed",
    "qa.schema_validation: passed",
    "qa.side_effect_policy: passed",
    "qa.runtime_boundary: passed",
    "qa.adapter_contract: passed",
    "contract.required_scopes",
    "contract.allowed_outputs",
    "fingerprints.spec",
    "fingerprints.implementation",
    "fingerprints.tests",
    "fingerprints.fixtures",
)
HANDOFF_MODE = "coding-profile-command"
DISPATCH_STATUS_PENDING = "pending_coding_profile"
DISPATCH_RECORD_STATUS = "queued_for_coding_profile"
DISPATCH_STATUS_DISPATCHED = "dispatched_to_coding_profile"
RUNTIME_BOUNDARY = "build-time handoff only; workflow runtime must not call the coding profile"
DISPATCH_OUTBOX_FILENAME = "coding-profile.dispatch.jsonl"
DISPATCH_SCRIPT_FILENAME = "coding-profile.dispatch.sh"
RETURNED_QA_DIRNAME = "returned-qa"


def validation_issues_require_extension_handoff(issues: Iterable[WorkflowIssue]) -> bool:
    """Return true when validation is blocked only by generated-extension QA evidence."""

    issue_tuple = tuple(issues)
    return bool(issue_tuple) and all(issue.code.startswith("extension_qa") for issue in issue_tuple)


@dataclass(frozen=True)
class WorkflowExtensionHandoff:
    status: str
    target_profile: str
    package_path: Path
    package_fingerprint: str
    output_dir: Path | None
    manifest_path: Path | None
    dispatch_path: Path | None
    dispatch_script_path: Path | None
    prompt_paths: tuple[Path, ...]
    prompts: tuple[str, ...]
    requests: tuple[WorkflowExtensionBuildRequest, ...]
    handoff_mode: str = HANDOFF_MODE
    dispatch_status: str = DISPATCH_STATUS_PENDING
    coding_profile_commands: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowExtensionHandoffApplyReport:
    status: str
    manifest_path: Path
    qa_manifest_path: Path
    package_path: Path | None = None
    extension_id: str = ""
    qa_return_target: str = ""
    next_commands: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowExtensionReturnedQaApplyReport:
    status: str
    package_path: Path
    handoff_manifest_path: Path | None = None
    returned_qa_dir: Path | None = None
    applied_reports: tuple[WorkflowExtensionHandoffApplyReport, ...] = ()
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowExtensionDispatchRecord:
    request_id: str
    status: str
    target_profile: str
    kind: str
    extension_id: str
    prompt_path: str
    qa_return_target: str
    qa_return_manifest_path: str
    coding_profile_command: str
    runtime_boundary: str


@dataclass(frozen=True)
class WorkflowExtensionHandoffDispatchReport:
    status: str
    manifest_path: Path
    dispatch_path: Path | None
    dispatch_script_path: Path | None = None
    marked_dispatched: bool = False
    records: tuple[WorkflowExtensionDispatchRecord, ...] = ()
    issues: tuple[str, ...] = ()


def write_extension_handoff_for_validation_issues(
    documents: PackageDocuments,
    issues: Iterable[WorkflowIssue],
    *,
    output_dir: str | Path | None = None,
    catalog_registration: CatalogRegistration = "package",
) -> WorkflowExtensionHandoff:
    """Write coding-profile PRD prompt files for extension QA validation failures."""

    requests = build_extension_requests_for_validation_issues(
        documents,
        issues,
        catalog_registration=catalog_registration,
    )
    package = Path(documents.root).expanduser().resolve()
    package_fingerprint = fingerprint_package_documents(documents)
    if not requests:
        return WorkflowExtensionHandoff(
            status="skipped",
            target_profile="coding",
            package_path=package,
            package_fingerprint=package_fingerprint,
            handoff_mode=HANDOFF_MODE,
            dispatch_status="not_applicable",
            output_dir=None,
            manifest_path=None,
            dispatch_path=None,
            dispatch_script_path=None,
            prompt_paths=(),
            prompts=(),
            requests=(),
            coding_profile_commands=(),
            issues=("no extension QA validation failures found",),
        )

    target = Path(output_dir).expanduser().resolve() if output_dir is not None else package / ".workflow-extension-handoff"
    target.mkdir(parents=True, exist_ok=True)
    returned_qa_dir = target / RETURNED_QA_DIRNAME
    returned_qa_dir.mkdir(parents=True, exist_ok=True)
    prompts: list[str] = []
    prompt_paths: list[Path] = []
    coding_profile_commands: list[str] = []
    manifest_requests: list[dict[str, object]] = []
    dispatch_requests: list[dict[str, object]] = []
    dispatch_records: list[dict[str, object]] = []
    for request in requests:
        qa_return_manifest_path = _qa_return_manifest_path(target, request)
        prompt = render_extension_prd_prompt(request, qa_return_manifest_path=str(qa_return_manifest_path))
        prompt_path = target / _prompt_filename(request)
        prompt_path.write_text(prompt + "\n", encoding="utf-8")
        command = _coding_profile_command(package=package, prompt_path=prompt_path)
        prompt_sha = _sha256(prompt + "\n")
        request_id = _dispatch_request_id(package_fingerprint, request, prompt_sha)
        qa_return_target = _qa_return_target(request)
        prompts.append(prompt)
        prompt_paths.append(prompt_path)
        coding_profile_commands.append(command)
        manifest_requests.append(
            _manifest_request(
                request,
                prompt_path,
                target,
                prompt_sha,
                command,
                request_id,
                qa_return_target,
                qa_return_manifest_path,
            )
        )
        dispatch_record = _dispatch_record(
            request=request,
            request_id=request_id,
            package=package,
            package_fingerprint=package_fingerprint,
            prompt_path=prompt_path,
            output_dir=target,
            command=command,
            qa_return_target=qa_return_target,
            qa_return_manifest_path=qa_return_manifest_path,
        )
        dispatch_records.append(dispatch_record)
        dispatch_requests.append(_manifest_dispatch_request(dispatch_record))

    manifest_path = target / "extension-handoff.manifest.json"
    dispatch_path = target / DISPATCH_OUTBOX_FILENAME
    dispatch_script_path = target / DISPATCH_SCRIPT_FILENAME
    dispatch_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in dispatch_records),
        encoding="utf-8",
    )
    _write_dispatch_script(dispatch_script_path, coding_profile_commands)
    manifest_path.write_text(
        json.dumps(
            {
                "version": "appv24.workflow-extension-handoff/v1",
                "target_profile": "coding",
                "handoff_mode": HANDOFF_MODE,
                "dispatch_status": DISPATCH_STATUS_PENDING,
                "dispatch_outbox_path": DISPATCH_OUTBOX_FILENAME,
                "dispatch_script_path": DISPATCH_SCRIPT_FILENAME,
                "dispatch_requests": dispatch_requests,
                "returned_qa_dir": RETURNED_QA_DIRNAME,
                "runtime_boundary": RUNTIME_BOUNDARY,
                "package_path": str(package),
                "package_fingerprint": package_fingerprint,
                "catalog_registration": catalog_registration,
                "qa_manifest_required": "appv24.extension_qa/v1",
                "qa_required_fields": list(QA_REQUIRED_FIELDS),
                "coding_profile_commands": coding_profile_commands,
                "requests": manifest_requests,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return WorkflowExtensionHandoff(
        status="ready",
        target_profile="coding",
        package_path=package,
        package_fingerprint=package_fingerprint,
        handoff_mode=HANDOFF_MODE,
        dispatch_status=DISPATCH_STATUS_PENDING,
        output_dir=target,
        manifest_path=manifest_path,
        dispatch_path=dispatch_path,
        dispatch_script_path=dispatch_script_path,
        prompt_paths=tuple(prompt_paths),
        prompts=tuple(prompts),
        requests=requests,
        coding_profile_commands=tuple(coding_profile_commands),
    )


def dispatch_extension_handoff(
    manifest_path: str | Path,
    *,
    mark_dispatched: bool = False,
) -> WorkflowExtensionHandoffDispatchReport:
    """Inspect or mark the build-time coding-profile dispatch outbox."""

    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest, manifest_issues = _read_json_mapping(manifest_file)
    if manifest_issues:
        return WorkflowExtensionHandoffDispatchReport(
            status="blocked",
            manifest_path=manifest_file,
            dispatch_path=None,
            dispatch_script_path=None,
            marked_dispatched=mark_dispatched,
            issues=manifest_issues,
        )
    if manifest.get("version") != "appv24.workflow-extension-handoff/v1":
        return WorkflowExtensionHandoffDispatchReport(
            status="blocked",
            manifest_path=manifest_file,
            dispatch_path=None,
            dispatch_script_path=None,
            marked_dispatched=mark_dispatched,
            issues=("unsupported handoff manifest version",),
        )

    dispatch_path = _dispatch_path_for_manifest(manifest_file, manifest)
    dispatch_script_path = _dispatch_script_path_for_manifest(manifest_file, manifest)
    raw_records, record_issues = _read_dispatch_records(dispatch_path)
    if record_issues:
        return WorkflowExtensionHandoffDispatchReport(
            status="blocked",
            manifest_path=manifest_file,
            dispatch_path=dispatch_path,
            dispatch_script_path=dispatch_script_path,
            marked_dispatched=mark_dispatched,
            issues=record_issues,
        )
    if mark_dispatched:
        raw_records = tuple({**record, "status": DISPATCH_STATUS_DISPATCHED} for record in raw_records)
        dispatch_path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in raw_records),
            encoding="utf-8",
        )
        _write_dispatch_manifest_status(manifest_file, manifest, raw_records)

    records = tuple(_dispatch_record_from_mapping(record) for record in raw_records)
    return WorkflowExtensionHandoffDispatchReport(
        status=_dispatch_report_status(records),
        manifest_path=manifest_file,
        dispatch_path=dispatch_path,
        dispatch_script_path=dispatch_script_path,
        marked_dispatched=mark_dispatched,
        records=records,
    )


def format_workflow_extension_handoff_dispatch_report(report: WorkflowExtensionHandoffDispatchReport) -> str:
    lines = [f"Workflow extension dispatch: {report.status}"]
    lines.append(f"handoff manifest: {report.manifest_path}")
    if report.dispatch_path is not None:
        lines.append(f"dispatch outbox: {report.dispatch_path}")
    if report.dispatch_script_path is not None:
        lines.append(f"dispatch script: {report.dispatch_script_path}")
    if report.marked_dispatched:
        lines.append("marked dispatched: yes")
    if report.records:
        lines.append("records:")
        for record in report.records:
            lines.append(f"- {record.request_id} {record.extension_id} {record.status}")
            lines.append(f"  target profile: {record.target_profile}")
            lines.append(f"  prompt: {record.prompt_path}")
            lines.append(f"  QA return target: {record.qa_return_target}")
            lines.append(f"  QA return manifest: {record.qa_return_manifest_path}")
            lines.append(f"  command: {record.coding_profile_command}")
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)


def apply_extension_handoff_result(
    manifest_path: str | Path,
    qa_manifest_path: str | Path,
) -> WorkflowExtensionHandoffApplyReport:
    """Attach a coding-profile QA manifest to the exact handoff return target."""

    manifest_file = Path(manifest_path).expanduser().resolve()
    qa_file = Path(qa_manifest_path).expanduser().resolve()
    manifest, manifest_issues = _read_json_mapping(manifest_file)
    qa, qa_issues = _read_qa_mapping(qa_file)
    if manifest_issues or qa_issues:
        return WorkflowExtensionHandoffApplyReport(
            status="blocked",
            manifest_path=manifest_file,
            qa_manifest_path=qa_file,
            issues=(*manifest_issues, *qa_issues),
        )
    if manifest.get("version") != "appv24.workflow-extension-handoff/v1":
        return WorkflowExtensionHandoffApplyReport(
            status="blocked",
            manifest_path=manifest_file,
            qa_manifest_path=qa_file,
            issues=("unsupported handoff manifest version",),
        )

    package_path = Path(str(manifest.get("package_path", ""))).expanduser().resolve()
    extension_id = _string(qa.get("extension_id")) or ""
    request = _handoff_request_for_extension(manifest, extension_id)
    if request is None:
        return WorkflowExtensionHandoffApplyReport(
            status="blocked",
            manifest_path=manifest_file,
            qa_manifest_path=qa_file,
            package_path=package_path,
            extension_id=extension_id,
            issues=(f"QA manifest extension_id is not in this handoff: {extension_id or '<missing>'}",),
        )
    qa_return_target = _string(request.get("qa_return_target")) or ""

    documents = load_package_documents(package_path)
    current_fingerprint = fingerprint_package_documents(documents)
    expected_fingerprint = _string(manifest.get("package_fingerprint"))
    if expected_fingerprint and current_fingerprint != expected_fingerprint:
        return WorkflowExtensionHandoffApplyReport(
            status="blocked",
            manifest_path=manifest_file,
            qa_manifest_path=qa_file,
            package_path=package_path,
            extension_id=extension_id,
            qa_return_target=qa_return_target,
            issues=("package fingerprint changed after handoff; regenerate the extension handoff",),
        )

    kind = _string(request.get("kind")) or _string(qa.get("kind")) or ""
    target_report = _target_validation_report(kind, extension_id, package_path, qa)
    if target_report.status != "ready":
        return WorkflowExtensionHandoffApplyReport(
            status="blocked",
            manifest_path=manifest_file,
            qa_manifest_path=qa_file,
            package_path=package_path,
            extension_id=extension_id,
            qa_return_target=qa_return_target,
            issues=target_report.issues,
        )

    writer = _attach_connector_qa if kind == "connector" else _attach_middleware_qa
    attach_issues = writer(package_path, extension_id, qa)
    if attach_issues:
        return WorkflowExtensionHandoffApplyReport(
            status="blocked",
            manifest_path=manifest_file,
            qa_manifest_path=qa_file,
            package_path=package_path,
            extension_id=extension_id,
            qa_return_target=qa_return_target,
            issues=attach_issues,
        )
    return WorkflowExtensionHandoffApplyReport(
        status="applied",
        manifest_path=manifest_file,
        qa_manifest_path=qa_file,
        package_path=package_path,
        extension_id=extension_id,
        qa_return_target=qa_return_target,
        next_commands=_next_commands_after_apply(package_path, extension_id),
    )


def apply_returned_extension_handoff_results(
    package_path: str | Path,
) -> WorkflowExtensionReturnedQaApplyReport:
    """Apply returned coding-profile QA manifests from the standard handoff inbox."""

    package = Path(package_path).expanduser().resolve()
    handoff_dir = package / ".workflow-extension-handoff"
    manifest_path = handoff_dir / "extension-handoff.manifest.json"
    returned_qa_dir = handoff_dir / RETURNED_QA_DIRNAME
    if not manifest_path.is_file():
        return WorkflowExtensionReturnedQaApplyReport(
            status="skipped",
            package_path=package,
            handoff_manifest_path=manifest_path,
            returned_qa_dir=returned_qa_dir,
        )
    if not returned_qa_dir.is_dir():
        return WorkflowExtensionReturnedQaApplyReport(
            status="skipped",
            package_path=package,
            handoff_manifest_path=manifest_path,
            returned_qa_dir=returned_qa_dir,
        )

    qa_paths = _returned_qa_paths(returned_qa_dir)
    if not qa_paths:
        return WorkflowExtensionReturnedQaApplyReport(
            status="skipped",
            package_path=package,
            handoff_manifest_path=manifest_path,
            returned_qa_dir=returned_qa_dir,
        )

    applied: list[WorkflowExtensionHandoffApplyReport] = []
    issues: list[str] = []
    for qa_path in qa_paths:
        report = apply_extension_handoff_result(manifest_path, qa_path)
        if report.status == "applied":
            applied.append(report)
            _refresh_handoff_package_fingerprint(manifest_path, package)
            continue
        joined = "; ".join(report.issues) if report.issues else report.status
        issues.append(f"{qa_path.name}: {joined}")

    if issues:
        status = "blocked"
    elif applied:
        status = "applied"
    else:
        status = "skipped"
    return WorkflowExtensionReturnedQaApplyReport(
        status=status,
        package_path=package,
        handoff_manifest_path=manifest_path,
        returned_qa_dir=returned_qa_dir,
        applied_reports=tuple(applied),
        issues=tuple(issues),
    )


def format_workflow_extension_handoff_apply_report(report: WorkflowExtensionHandoffApplyReport) -> str:
    lines = [f"Workflow extension handoff apply: {report.status}"]
    if report.package_path is not None:
        lines.append(f"package: {report.package_path}")
    if report.extension_id:
        lines.append(f"extension: {report.extension_id}")
    if report.qa_return_target:
        lines.append(f"QA return target: {report.qa_return_target}")
    lines.append(f"handoff manifest: {report.manifest_path}")
    lines.append(f"QA manifest: {report.qa_manifest_path}")
    if report.next_commands:
        lines.append("next commands:")
        lines.extend(f"- {command}" for command in report.next_commands)
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)


def format_workflow_extension_returned_qa_apply_report(report: WorkflowExtensionReturnedQaApplyReport) -> str:
    lines = [f"Workflow extension returned QA apply: {report.status}", f"package: {report.package_path}"]
    if report.handoff_manifest_path is not None:
        lines.append(f"handoff manifest: {report.handoff_manifest_path}")
    if report.returned_qa_dir is not None:
        lines.append(f"returned QA dir: {report.returned_qa_dir}")
    if report.applied_reports:
        lines.append("applied QA manifests:")
        lines.extend(f"- {applied.qa_manifest_path.name}: {applied.extension_id}" for applied in report.applied_reports)
        promotion_commands = _catalog_promotion_commands_from_apply_reports(report.applied_reports)
        if promotion_commands:
            lines.append("extension catalog choice:")
            lines.append("- keep package-only: no command needed")
            lines.append("- promote for reuse:")
            lines.extend(f"  - {command}" for command in promotion_commands)
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)


def format_workflow_extension_handoff(handoff: WorkflowExtensionHandoff) -> str:
    lines = [
        f"Workflow extension handoff: {handoff.status}",
        f"target profile: {handoff.target_profile}",
        f"handoff mode: {handoff.handoff_mode}",
        f"dispatch status: {handoff.dispatch_status}",
        f"package: {handoff.package_path}",
        f"package fingerprint: {handoff.package_fingerprint}",
    ]
    if handoff.output_dir is not None:
        lines.append(f"output: {handoff.output_dir}")
    if handoff.manifest_path is not None:
        lines.append(f"manifest: {handoff.manifest_path}")
    if handoff.dispatch_path is not None:
        lines.append(f"dispatch outbox: {handoff.dispatch_path}")
        lines.append(f"dispatch queue: {DISPATCH_RECORD_STATUS}")
    if handoff.dispatch_script_path is not None:
        lines.append(f"dispatch script: {handoff.dispatch_script_path}")
    if handoff.prompt_paths:
        lines.append("prompt files:")
        lines.extend(f"- {path}" for path in handoff.prompt_paths)
    qa_return_targets = tuple(_qa_return_target(request) for request in handoff.requests)
    if qa_return_targets:
        lines.append("QA return targets:")
        lines.extend(f"- {target}" for target in qa_return_targets)
    qa_return_manifest_paths = _qa_return_manifest_paths_for_handoff(handoff)
    if qa_return_manifest_paths:
        lines.append("QA return manifest paths:")
        lines.extend(f"- {path}" for path in qa_return_manifest_paths)
    if handoff.coding_profile_commands:
        lines.append("coding profile commands:")
        lines.extend(f"- {command}" for command in handoff.coding_profile_commands)
    if handoff.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in handoff.issues)
    return "\n".join(lines)


def _manifest_request(
    request: WorkflowExtensionBuildRequest,
    prompt_path: Path,
    output_dir: Path,
    prompt_sha: str,
    coding_profile_command: str,
    dispatch_request_id: str,
    qa_return_target: str,
    qa_return_manifest_path: Path,
) -> dict[str, object]:
    return {
        "kind": request.kind,
        "extension_id": request.extension_id,
        "prompt_path": str(prompt_path.relative_to(output_dir)),
        "prompt_sha256": prompt_sha,
        "dispatch_request_id": dispatch_request_id,
        "catalog_registration": request.catalog_registration,
        "qa_manifest_required": "appv24.extension_qa/v1",
        "qa_required_fields": list(QA_REQUIRED_FIELDS),
        "qa_return_target": qa_return_target,
        "qa_return_manifest_path": str(qa_return_manifest_path.relative_to(output_dir)),
        "coding_profile_command": coding_profile_command,
        "scopes": list(request.scopes),
        "env_requirements": list(request.env_requirements),
    }


def _write_dispatch_script(path: Path, commands: list[str]) -> None:
    lines = [
        "#!/usr/bin/env sh",
        "set -eu",
        "# appv24 workflow extension coding-profile dispatch",
        "# build-time only; workflow runtime must not execute this script",
        "",
    ]
    lines.extend(commands)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def _next_commands_after_apply(package_path: Path, extension_id: str) -> tuple[str, ...]:
    catalog_path = package_path.parent / "workflow-extension-catalog.yaml"
    commands = [f"/workflow-approve {package_path} happy_path.yaml"]
    if extension_id:
        commands.append(f"/workflow-extension-promote {package_path} {catalog_path} {extension_id}")
        commands.append(f"/workflow-checkin {catalog_path}")
    return tuple(commands)


def _catalog_promotion_commands_from_apply_reports(
    reports: tuple[WorkflowExtensionHandoffApplyReport, ...],
) -> tuple[str, ...]:
    commands: list[str] = []
    seen: set[str] = set()
    for report in reports:
        for command in report.next_commands:
            if not (
                command.startswith("/workflow-extension-promote ")
                or command.startswith("/workflow-checkin ")
            ):
                continue
            if command in seen:
                continue
            seen.add(command)
            commands.append(command)
    return tuple(commands)


def _qa_return_manifest_path(output_dir: Path, request: WorkflowExtensionBuildRequest) -> Path:
    return output_dir / RETURNED_QA_DIRNAME / f"{_safe_filename_part(request.extension_id)}.qa.yaml"


def _qa_return_manifest_paths_for_handoff(handoff: WorkflowExtensionHandoff) -> tuple[str, ...]:
    if handoff.output_dir is None:
        return ()
    paths: list[str] = []
    for request in handoff.requests:
        path = _qa_return_manifest_path(handoff.output_dir, request)
        try:
            paths.append(str(path.relative_to(handoff.output_dir)))
        except ValueError:
            paths.append(str(path))
    return tuple(paths)


def _returned_qa_paths(returned_qa_dir: Path) -> tuple[Path, ...]:
    suffixes = {".yaml", ".yml", ".json"}
    return tuple(
        path
        for path in sorted(returned_qa_dir.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.suffix in suffixes
    )


def _refresh_handoff_package_fingerprint(manifest_path: Path, package_path: Path) -> None:
    manifest, issues = _read_json_mapping(manifest_path)
    if issues:
        return
    documents = load_package_documents(package_path)
    manifest["package_fingerprint"] = fingerprint_package_documents(documents)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dispatch_record(
    *,
    request: WorkflowExtensionBuildRequest,
    request_id: str,
    package: Path,
    package_fingerprint: str,
    prompt_path: Path,
    output_dir: Path,
    command: str,
    qa_return_target: str,
    qa_return_manifest_path: Path,
) -> dict[str, object]:
    return {
        "version": "appv24.workflow-extension-dispatch/v1",
        "request_id": request_id,
        "status": DISPATCH_RECORD_STATUS,
        "target_profile": "coding",
        "handoff_manifest": "extension-handoff.manifest.json",
        "runtime_boundary": RUNTIME_BOUNDARY,
        "package_path": str(package),
        "package_fingerprint": package_fingerprint,
        "kind": request.kind,
        "extension_id": request.extension_id,
        "prompt_path": str(prompt_path.relative_to(output_dir)),
        "qa_return_target": qa_return_target,
        "qa_return_manifest_path": str(qa_return_manifest_path.relative_to(output_dir)),
        "qa_manifest_required": "appv24.extension_qa/v1",
        "coding_profile_command": command,
        "catalog_registration": request.catalog_registration,
    }


def _manifest_dispatch_request(record: Mapping[str, object]) -> dict[str, object]:
    return {
        "request_id": record["request_id"],
        "status": record["status"],
        "target_profile": record["target_profile"],
        "kind": record["kind"],
        "extension_id": record["extension_id"],
        "prompt_path": record["prompt_path"],
        "qa_return_target": record["qa_return_target"],
        "qa_return_manifest_path": record["qa_return_manifest_path"],
    }


@dataclass(frozen=True)
class _TargetValidationReport:
    status: str
    issues: tuple[str, ...] = ()


def _target_validation_report(
    kind: str,
    extension_id: str,
    package_path: Path,
    qa: Mapping[str, Any],
) -> _TargetValidationReport:
    if kind == "connector":
        action = _find_connector_action(package_path, extension_id)
        if action is None:
            return _TargetValidationReport("blocked", (f"connector action not found for QA return target: {extension_id}",))
        qa_report = validate_extension_qa_manifest(
            qa,
            expected_kind="connector",
            expected_extension_id=extension_id,
            expected_required_scopes=_string_tuple(action.get("required_scopes")),
            expected_allowed_outputs=_allowed_outputs(action),
        )
    elif kind == "middleware":
        if _find_middleware_entry(package_path, extension_id) is None:
            return _TargetValidationReport("blocked", (f"middleware not found for QA return target: {extension_id}",))
        qa_report = validate_extension_qa_manifest(
            qa,
            expected_kind="middleware",
            expected_extension_id=extension_id,
        )
    else:
        return _TargetValidationReport("blocked", (f"unsupported handoff request kind: {kind or '<missing>'}",))
    if qa_report.ok:
        return _TargetValidationReport("ready")
    return _TargetValidationReport(
        "blocked",
        tuple(f"{'.'.join(str(part) for part in issue.path)}: {issue.message}" for issue in qa_report.issues),
    )


def _attach_connector_qa(package_path: Path, extension_id: str, qa: Mapping[str, Any]) -> tuple[str, ...]:
    connector_id, _, action_id = extension_id.partition(".")
    document, issues = _read_yaml_mapping(package_path / "connectors.yaml")
    if issues:
        return issues
    for connector in _list_of_mappings(document.get("connectors")):
        if connector.get("id") != connector_id:
            continue
        for action in _list_of_mappings(connector.get("actions")):
            if action.get("id") == action_id:
                action["qa"] = dict(qa)
                _write_yaml_mapping(package_path / "connectors.yaml", document)
                return ()
    return (f"connector action not found for QA return target: {extension_id}",)


def _attach_middleware_qa(package_path: Path, extension_id: str, qa: Mapping[str, Any]) -> tuple[str, ...]:
    document, issues = _read_yaml_mapping(package_path / "middlewares.yaml")
    if issues:
        return issues
    for entry in _list_of_mappings(document.get("middlewares")):
        if entry.get("id") == extension_id:
            entry["qa"] = dict(qa)
            _write_yaml_mapping(package_path / "middlewares.yaml", document)
            return ()
    return (f"middleware not found for QA return target: {extension_id}",)


def _find_connector_action(package_path: Path, extension_id: str) -> Mapping[str, Any] | None:
    connector_id, _, action_id = extension_id.partition(".")
    if not connector_id or not action_id:
        return None
    document, issues = _read_yaml_mapping(package_path / "connectors.yaml")
    if issues:
        return None
    for connector in _list_of_mappings(document.get("connectors")):
        if connector.get("id") != connector_id:
            continue
        for action in _list_of_mappings(connector.get("actions")):
            if action.get("id") == action_id:
                return action
    return None


def _find_middleware_entry(package_path: Path, middleware_id: str) -> Mapping[str, Any] | None:
    document, issues = _read_yaml_mapping(package_path / "middlewares.yaml")
    if issues:
        return None
    for entry in _list_of_mappings(document.get("middlewares")):
        if entry.get("id") == middleware_id:
            return entry
    return None


def _handoff_request_for_extension(manifest: Mapping[str, Any], extension_id: str) -> Mapping[str, Any] | None:
    requests = manifest.get("requests")
    if not isinstance(requests, list):
        return None
    for request in requests:
        if isinstance(request, Mapping) and request.get("extension_id") == extension_id:
            return request
    return None


def _qa_return_target(request: WorkflowExtensionBuildRequest) -> str:
    if request.kind == "connector":
        connector_id, _, action_id = request.extension_id.partition(".")
        return f"connectors.yaml:connectors[id={connector_id}].actions[id={action_id}].qa"
    return f"middlewares.yaml:middlewares[id={request.extension_id}].qa"


def _read_json_mapping(path: Path) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return {}, (f"failed to read handoff manifest: {error}",)
    if not isinstance(parsed, Mapping):
        return {}, ("handoff manifest must be a mapping",)
    return parsed, ()


def _read_qa_mapping(path: Path) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    document, issues = _read_yaml_mapping(path)
    if issues:
        return {}, tuple(f"failed to read QA manifest: {issue}" for issue in issues)
    return document, ()


def _dispatch_path_for_manifest(manifest_path: Path, manifest: Mapping[str, Any]) -> Path:
    path_text = _string(manifest.get("dispatch_outbox_path")) or DISPATCH_OUTBOX_FILENAME
    raw_path = Path(path_text).expanduser()
    return raw_path.resolve() if raw_path.is_absolute() else (manifest_path.parent / raw_path).resolve()


def _dispatch_script_path_for_manifest(manifest_path: Path, manifest: Mapping[str, Any]) -> Path:
    path_text = _string(manifest.get("dispatch_script_path")) or DISPATCH_SCRIPT_FILENAME
    raw_path = Path(path_text).expanduser()
    return raw_path.resolve() if raw_path.is_absolute() else (manifest_path.parent / raw_path).resolve()


def _read_dispatch_records(path: Path) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        return (), (f"failed to read dispatch outbox: {error}",)
    records: list[Mapping[str, Any]] = []
    issues: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            issues.append(f"dispatch outbox line {line_number} is invalid JSON: {error}")
            continue
        if not isinstance(parsed, Mapping):
            issues.append(f"dispatch outbox line {line_number} must be a mapping")
            continue
        records.append(parsed)
    return tuple(records), tuple(issues)


def _write_dispatch_manifest_status(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    records: tuple[Mapping[str, Any], ...],
) -> None:
    record_status_by_id = {
        _string(record.get("request_id")): _string(record.get("status"))
        for record in records
        if _string(record.get("request_id")) is not None
    }
    updated = dict(manifest)
    updated["dispatch_status"] = _manifest_dispatch_status(records)
    dispatch_requests: list[object] = []
    raw_dispatch_requests = manifest.get("dispatch_requests")
    if isinstance(raw_dispatch_requests, list):
        for request in raw_dispatch_requests:
            if not isinstance(request, Mapping):
                dispatch_requests.append(request)
                continue
            request_id = _string(request.get("request_id"))
            dispatch_requests.append(
                {
                    **dict(request),
                    "status": record_status_by_id.get(request_id, _string(request.get("status")) or ""),
                }
            )
    updated["dispatch_requests"] = dispatch_requests
    manifest_path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dispatch_record_from_mapping(record: Mapping[str, Any]) -> WorkflowExtensionDispatchRecord:
    return WorkflowExtensionDispatchRecord(
        request_id=_string(record.get("request_id")) or "",
        status=_string(record.get("status")) or "",
        target_profile=_string(record.get("target_profile")) or "",
        kind=_string(record.get("kind")) or "",
        extension_id=_string(record.get("extension_id")) or "",
        prompt_path=_string(record.get("prompt_path")) or "",
        qa_return_target=_string(record.get("qa_return_target")) or "",
        qa_return_manifest_path=_string(record.get("qa_return_manifest_path")) or "",
        coding_profile_command=_string(record.get("coding_profile_command")) or "",
        runtime_boundary=_string(record.get("runtime_boundary")) or "",
    )


def _dispatch_report_status(records: tuple[WorkflowExtensionDispatchRecord, ...]) -> str:
    if not records:
        return "empty"
    if all(record.status == DISPATCH_STATUS_DISPATCHED for record in records):
        return "dispatched"
    if any(record.status == DISPATCH_STATUS_DISPATCHED for record in records):
        return "partially-dispatched"
    return "queued"


def _manifest_dispatch_status(records: tuple[Mapping[str, Any], ...]) -> str:
    statuses = tuple(_string(record.get("status")) or "" for record in records)
    if statuses and all(status == DISPATCH_STATUS_DISPATCHED for status in statuses):
        return DISPATCH_STATUS_DISPATCHED
    if any(status == DISPATCH_STATUS_DISPATCHED for status in statuses):
        return "partial_coding_profile_dispatch"
    return DISPATCH_STATUS_PENDING


def _read_yaml_mapping(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        return {}, (str(error),)
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return {}, (f"{path.name} must be a mapping",)
    return parsed, ()


def _write_yaml_mapping(path: Path, document: Mapping[str, Any]) -> None:
    path.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")


def _list_of_mappings(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _allowed_outputs(action: Mapping[str, Any]) -> tuple[str, ...]:
    outputs = action.get("outputs")
    if not isinstance(outputs, Mapping):
        return ()
    return _string_tuple(outputs.get("allowed"))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _coding_profile_command(*, package: Path, prompt_path: Path) -> str:
    return (
        f"appv24 --cwd {shlex.quote(str(package))} --profile coding --plain "
        f"\"$(cat {shlex.quote(str(prompt_path))})\""
    )


def _dispatch_request_id(
    package_fingerprint: str,
    request: WorkflowExtensionBuildRequest,
    prompt_sha: str,
) -> str:
    return _sha256("\n".join((package_fingerprint, request.kind, request.extension_id, prompt_sha)))


def _prompt_filename(request: WorkflowExtensionBuildRequest) -> str:
    return f"{request.kind}-{_safe_filename_part(request.extension_id)}.prd.md"


def _safe_filename_part(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_", "."} else "-" for character in value)


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
