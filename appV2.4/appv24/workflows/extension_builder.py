"""Build-time execution for workflow extension handoff requests."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import yaml

from appv24.workflows.extension_handoff import (
    DISPATCH_OUTBOX_FILENAME,
    RETURNED_QA_DIRNAME,
    WorkflowExtensionReturnedQaApplyReport,
    apply_returned_extension_handoff_results,
    format_workflow_extension_returned_qa_apply_report,
)
from appv24.workflows.validator import validate_workflow_package


HANDOFF_MANIFEST_FILENAME = "extension-handoff.manifest.json"
BUILD_LOG_DIRNAME = "build-logs"
ARTIFACT_BUNDLE_VERSION = "appv24.workflow-extension-artifact-bundle/v1"
ARTIFACT_BUNDLE_BEGIN = "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_BEGIN"
ARTIFACT_BUNDLE_END = "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_END"
DEFAULT_BUILD_TIMEOUT_SECONDS = 180.0
BUILD_TIMEOUT_ENV = "APPV24_WORKFLOW_EXTENSION_BUILD_TIMEOUT_SECONDS"
BUILD_LOG_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(=|:)\S+"),
)


@dataclass(frozen=True)
class WorkflowExtensionBuildInvocation:
    extension_id: str
    kind: str
    package_path: Path
    manifest_path: Path
    prompt_path: Path
    qa_return_manifest_path: Path
    log_path: Path
    argv: tuple[str, ...]
    env: Mapping[str, str]
    request_id: str = ""


@dataclass(frozen=True)
class WorkflowExtensionBuildCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class WorkflowExtensionBuildResult:
    extension_id: str
    request_id: str
    status: str
    log_path: Path
    returncode: int = 0
    timed_out: bool = False
    timeout_seconds: float | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowExtensionBuildReport:
    status: str
    package_path: Path
    manifest_path: Path | None = None
    dispatch_path: Path | None = None
    built_extensions: tuple[str, ...] = ()
    applied_extensions: tuple[str, ...] = ()
    validation_ok: bool = False
    validation_issues: tuple[str, ...] = ()
    build_results: tuple[WorkflowExtensionBuildResult, ...] = ()
    apply_report: WorkflowExtensionReturnedQaApplyReport | None = None
    next_commands: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowExtensionBuildEvent:
    phase: str
    extension_id: str | None = None
    message: str = ""
    log_path: Path | None = None


WorkflowExtensionBuildRunner = Callable[[WorkflowExtensionBuildInvocation], WorkflowExtensionBuildCommandResult]
WorkflowExtensionBuildProgress = Callable[[WorkflowExtensionBuildEvent], None]


def build_workflow_extension_handoff(
    package_or_manifest: str | Path,
    *,
    runner: WorkflowExtensionBuildRunner | None = None,
    timeout_seconds: float | None = None,
    progress: WorkflowExtensionBuildProgress | None = None,
) -> WorkflowExtensionBuildReport:
    """Run queued build-time coding-profile handoff requests and apply returned QA."""

    resolved = _resolve_handoff(package_or_manifest)
    if resolved.issues:
        return WorkflowExtensionBuildReport(
            status="blocked",
            package_path=resolved.package_path,
            manifest_path=resolved.manifest_path,
            dispatch_path=resolved.dispatch_path,
            issues=resolved.issues,
        )

    records, record_issues = _read_dispatch_records(resolved.dispatch_path)
    if record_issues:
        return WorkflowExtensionBuildReport(
            status="blocked",
            package_path=resolved.package_path,
            manifest_path=resolved.manifest_path,
            dispatch_path=resolved.dispatch_path,
            issues=record_issues,
        )

    effective_timeout = normalize_workflow_extension_build_timeout(timeout_seconds)
    command_runner = runner or run_workflow_extension_contract_scaffold_invocation
    build_results: list[WorkflowExtensionBuildResult] = []
    issues: list[str] = []
    for record in records:
        invocation, invocation_issues = _build_invocation(resolved, record)
        if invocation_issues:
            issues.extend(invocation_issues)
            continue
        assert invocation is not None
        _emit_progress(
            progress,
            WorkflowExtensionBuildEvent(
                phase="extension-started",
                extension_id=invocation.extension_id,
                message=f"building {invocation.extension_id}",
                log_path=invocation.log_path,
            ),
        )
        result = command_runner(invocation)
        _write_build_log(invocation, result)
        if result.timed_out:
            timeout_label = _format_seconds(result.timeout_seconds or effective_timeout)
            issue = f"coding profile build timed out for {invocation.extension_id} after {timeout_label}"
            issues.append(issue)
            build_results.append(
                WorkflowExtensionBuildResult(
                    extension_id=invocation.extension_id,
                    request_id=invocation.request_id,
                    status="timed-out",
                    returncode=result.returncode,
                    log_path=invocation.log_path,
                    timed_out=True,
                    timeout_seconds=result.timeout_seconds or effective_timeout,
                    issues=(issue,),
                )
            )
            _emit_progress(
                progress,
                WorkflowExtensionBuildEvent(
                    phase="extension-timed-out",
                    extension_id=invocation.extension_id,
                    message=f"{invocation.extension_id} timed out after {timeout_label}",
                    log_path=invocation.log_path,
                ),
            )
            continue
        if result.returncode != 0:
            issue = f"coding profile build failed for {invocation.extension_id}: exit {result.returncode}"
            issues.append(issue)
            build_results.append(
                WorkflowExtensionBuildResult(
                    extension_id=invocation.extension_id,
                    request_id=invocation.request_id,
                    status="failed",
                    returncode=result.returncode,
                    log_path=invocation.log_path,
                    issues=(issue,),
                )
            )
            _emit_progress(
                progress,
                WorkflowExtensionBuildEvent(
                    phase="extension-failed",
                    extension_id=invocation.extension_id,
                    message=f"{invocation.extension_id} failed with exit {result.returncode}",
                    log_path=invocation.log_path,
                ),
            )
            continue
        bundle_issues = _apply_stdout_artifact_bundle(invocation, result.stdout)
        if bundle_issues:
            issues.extend(bundle_issues)
            build_results.append(
                WorkflowExtensionBuildResult(
                    extension_id=invocation.extension_id,
                    request_id=invocation.request_id,
                    status="bundle-rejected",
                    log_path=invocation.log_path,
                    issues=bundle_issues,
                )
            )
            _emit_progress(
                progress,
                WorkflowExtensionBuildEvent(
                    phase="extension-bundle-rejected",
                    extension_id=invocation.extension_id,
                    message=f"{invocation.extension_id} returned an invalid artifact bundle",
                    log_path=invocation.log_path,
                ),
            )
            continue
        if not invocation.qa_return_manifest_path.is_file():
            issue = f"returned QA manifest was not created for {invocation.extension_id}: {invocation.qa_return_manifest_path}"
            issues.append(issue)
            build_results.append(
                WorkflowExtensionBuildResult(
                    extension_id=invocation.extension_id,
                    request_id=invocation.request_id,
                    status="missing-qa",
                    log_path=invocation.log_path,
                    issues=(issue,),
                )
            )
            _emit_progress(
                progress,
                WorkflowExtensionBuildEvent(
                    phase="extension-missing-qa",
                    extension_id=invocation.extension_id,
                    message=f"{invocation.extension_id} did not return QA evidence",
                    log_path=invocation.log_path,
                ),
            )
            continue
        build_results.append(
            WorkflowExtensionBuildResult(
                extension_id=invocation.extension_id,
                request_id=invocation.request_id,
                status="built",
                log_path=invocation.log_path,
            )
        )
        _emit_progress(
            progress,
            WorkflowExtensionBuildEvent(
                phase="extension-built",
                extension_id=invocation.extension_id,
                message=f"{invocation.extension_id} returned QA evidence",
                log_path=invocation.log_path,
            ),
        )

    apply_report = None
    if not issues:
        apply_report = apply_returned_extension_handoff_results(resolved.package_path)
        if apply_report.status == "blocked":
            issues.extend(apply_report.issues)
        elif apply_report.status != "applied":
            issues.append("no returned extension QA manifests were applied")

    validation = validate_workflow_package(resolved.package_path)
    validation_issues = tuple(validation.messages())
    applied_extensions = _applied_extensions(apply_report)
    built_extensions = tuple(result.extension_id for result in build_results if result.status == "built")
    next_commands = _next_commands(apply_report)
    status = _build_report_status(
        issues=tuple(issues),
        built_extensions=built_extensions,
        applied_extensions=applied_extensions,
        validation_ok=validation.ok,
    )
    report = WorkflowExtensionBuildReport(
        status=status,
        package_path=resolved.package_path,
        manifest_path=resolved.manifest_path,
        dispatch_path=resolved.dispatch_path,
        built_extensions=built_extensions,
        applied_extensions=applied_extensions,
        validation_ok=validation.ok,
        validation_issues=validation_issues,
        build_results=tuple(build_results),
        apply_report=apply_report,
        next_commands=next_commands,
        issues=tuple(issues),
    )
    _emit_progress(
        progress,
        WorkflowExtensionBuildEvent(
            phase="finished",
            message=f"workflow extension build {report.status}",
        ),
    )
    return report


def run_workflow_extension_build_invocation(
    invocation: WorkflowExtensionBuildInvocation,
    *,
    timeout_seconds: float | None = None,
) -> WorkflowExtensionBuildCommandResult:
    try:
        completed = subprocess.run(  # noqa: S603 - argv is constructed locally; shell is not used.
            invocation.argv,
            cwd=invocation.package_path,
            env=dict(invocation.env),
            capture_output=True,
            text=True,
            check=False,
            timeout=normalize_workflow_extension_build_timeout(timeout_seconds),
        )
    except subprocess.TimeoutExpired as error:
        timeout_label = _format_seconds(normalize_workflow_extension_build_timeout(timeout_seconds))
        stdout = _completed_output_text(error.stdout)
        stderr = _completed_output_text(error.stderr)
        timeout_message = f"coding profile build timed out after {timeout_label}"
        return WorkflowExtensionBuildCommandResult(
            returncode=-9,
            stdout=stdout,
            stderr="\n".join(part for part in (stderr, timeout_message) if part).strip(),
            timed_out=True,
            timeout_seconds=normalize_workflow_extension_build_timeout(timeout_seconds),
        )
    return WorkflowExtensionBuildCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_workflow_extension_contract_scaffold_invocation(
    invocation: WorkflowExtensionBuildInvocation,
) -> WorkflowExtensionBuildCommandResult:
    """Generate a package-local connector scaffold from the handoff contract."""

    if invocation.kind != "connector":
        return WorkflowExtensionBuildCommandResult(
            returncode=2,
            stderr=f"contract scaffold supports connector handoff only: {invocation.kind}",
        )
    spec, spec_issues = _connector_scaffold_spec(invocation)
    if spec_issues:
        return WorkflowExtensionBuildCommandResult(returncode=2, stderr="\n".join(spec_issues))
    assert spec is not None

    source = _render_connector_scaffold_source(spec)
    test_source = _render_connector_scaffold_test(spec)
    file_specs = (
        (invocation.package_path / spec.source_path, source),
        (invocation.package_path / spec.test_path, test_source),
    )
    write_issues = _write_scaffold_files(invocation, file_specs)
    if write_issues:
        return WorkflowExtensionBuildCommandResult(returncode=2, stderr="\n".join(write_issues))

    test_result = subprocess.run(  # noqa: S603 - argv is constructed locally; shell is not used.
        (sys.executable, str(spec.test_path)),
        cwd=invocation.package_path,
        env=dict(invocation.env),
        capture_output=True,
        text=True,
        check=False,
    )
    command_label = f"{sys.executable} {spec.test_path.as_posix()}"
    if test_result.returncode != 0:
        return WorkflowExtensionBuildCommandResult(
            returncode=test_result.returncode,
            stdout=test_result.stdout,
            stderr=test_result.stderr,
        )

    qa_manifest = _connector_scaffold_qa_manifest(
        invocation=invocation,
        spec=spec,
        source=source,
        test_source=test_source,
        command_label=command_label,
    )
    invocation.qa_return_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    invocation.qa_return_manifest_path.write_text(yaml.safe_dump(qa_manifest, sort_keys=False), encoding="utf-8")
    return WorkflowExtensionBuildCommandResult(
        returncode=0,
        stdout="\n".join(
            (
                f"generated connector scaffold: {spec.source_path.as_posix()}",
                f"generated connector test: {spec.test_path.as_posix()}",
                f"wrote returned QA: {invocation.qa_return_manifest_path}",
                f"test command passed: {command_label}",
            )
        ),
        stderr="",
    )


def format_workflow_extension_build_report(report: WorkflowExtensionBuildReport) -> str:
    lines = [f"Workflow extension build: {report.status}", f"package: {report.package_path}"]
    if report.manifest_path is not None:
        lines.append(f"handoff manifest: {report.manifest_path}")
    if report.dispatch_path is not None:
        lines.append(f"dispatch outbox: {report.dispatch_path}")
    if report.build_results:
        lines.append("build results:")
        for result in report.build_results:
            lines.append(f"- {result.extension_id}: {result.status}")
            lines.append(f"  log: {result.log_path}")
            if result.timed_out:
                lines.append(f"  timeout: {_format_seconds(result.timeout_seconds)}")
    if report.apply_report is not None and report.apply_report.status != "skipped":
        lines.append(format_workflow_extension_returned_qa_apply_report(report.apply_report))
    lines.append(f"validation: {'passed' if report.validation_ok else 'blocked'}")
    if report.validation_issues:
        lines.append("validation issues:")
        lines.extend(f"- {issue}" for issue in report.validation_issues)
    if report.next_commands:
        lines.append("next commands:")
        lines.extend(f"- {command}" for command in report.next_commands)
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)


@dataclass(frozen=True)
class _ResolvedHandoff:
    package_path: Path
    manifest_path: Path | None
    dispatch_path: Path | None
    manifest: Mapping[str, Any]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ConnectorScaffoldSpec:
    extension_id: str
    connector_id: str
    action_id: str
    source_path: Path
    test_path: Path
    required_inputs: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    required_scopes: tuple[str, ...]
    side_effect_class: str


def _connector_scaffold_spec(
    invocation: WorkflowExtensionBuildInvocation,
) -> tuple[_ConnectorScaffoldSpec | None, tuple[str, ...]]:
    connector_id, _, action_id = invocation.extension_id.partition(".")
    if not connector_id or not action_id:
        return None, (f"connector extension id must be connector.action: {invocation.extension_id}",)
    document, document_issues = _read_yaml_mapping(invocation.package_path / "connectors.yaml")
    if document_issues:
        return None, document_issues
    action = _find_connector_action(document, connector_id, action_id)
    if action is None:
        return None, (f"connector action not found for scaffold: {invocation.extension_id}",)
    hints = action.get("workflow_hints")
    hints_map = hints if isinstance(hints, Mapping) else {}
    required_inputs = _string_tuple(hints_map.get("required_input_fields")) or _string_tuple(hints_map.get("input_fields"))
    allowed_outputs = _allowed_outputs(action)
    required_scopes = _string_tuple(action.get("required_scopes"))
    source_path = Path("connectors") / connector_id / f"{_safe_python_file_stem(action_id)}.py"
    test_path = Path("connectors") / connector_id / f"test_{_safe_python_file_stem(action_id)}.py"
    return (
        _ConnectorScaffoldSpec(
            extension_id=invocation.extension_id,
            connector_id=connector_id,
            action_id=action_id,
            source_path=source_path,
            test_path=test_path,
            required_inputs=required_inputs,
            allowed_outputs=allowed_outputs,
            required_scopes=required_scopes,
            side_effect_class=_string(action.get("side_effect_class")) or "read",
        ),
        (),
    )


def _render_connector_scaffold_source(spec: _ConnectorScaffoldSpec) -> str:
    required_inputs = json.dumps(list(spec.required_inputs), sort_keys=True)
    allowed_outputs = json.dumps(list(spec.allowed_outputs), sort_keys=True)
    return f'''"""Package-local connector scaffold for {spec.extension_id}."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


REQUIRED_INPUTS = tuple({required_inputs})
ALLOWED_OUTPUTS = tuple({allowed_outputs})


def run(
    inputs: Mapping[str, Any],
    *,
    transport: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute the connector action through an injected transport."""

    missing = [field for field in REQUIRED_INPUTS if _missing_input(inputs.get(field))]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"missing required input(s): {{joined}}")

    raw_result: Mapping[str, Any]
    if transport is None:
        raw_result = {{}}
    else:
        raw_result = transport(dict(inputs))
        if not isinstance(raw_result, Mapping):
            raise TypeError("connector transport must return a mapping")

    return {{key: raw_result.get(key, _default_output(key)) for key in ALLOWED_OUTPUTS}}


def _missing_input(value: object) -> bool:
    return value is None or value == "" or value == []


def _default_output(key: str) -> object:
    if key == "count" or key.endswith("_count"):
        return 0
    if key.endswith("_hash"):
        return ""
    if key.endswith("s") or key in {{"items", "records", "failures"}}:
        return []
    return None
'''


def _render_connector_scaffold_test(spec: _ConnectorScaffoldSpec) -> str:
    valid_inputs = {field: _example_input_value(field) for field in spec.required_inputs}
    transport_outputs = {key: _example_output_value(key) for key in spec.allowed_outputs}
    return f'''from __future__ import annotations

from pathlib import Path
import importlib.util


MODULE_PATH = Path(__file__).with_name({json.dumps(spec.source_path.name)})
SPEC = importlib.util.spec_from_file_location("connector_under_test", MODULE_PATH)
assert SPEC is not None
connector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(connector)


VALID_INPUTS = {json.dumps(valid_inputs, sort_keys=True)}
TRANSPORT_OUTPUTS = {json.dumps(transport_outputs, sort_keys=True)}
ALLOWED_OUTPUTS = {json.dumps(list(spec.allowed_outputs), sort_keys=True)}


def test_required_inputs_are_enforced() -> None:
    if not connector.REQUIRED_INPUTS:
        return
    try:
        connector.run({{}})
    except ValueError as error:
        assert "missing required input" in str(error)
    else:
        raise AssertionError("missing required input was accepted")


def test_transport_result_is_filtered_to_allowed_outputs() -> None:
    result = connector.run(VALID_INPUTS, transport=lambda inputs: {{**TRANSPORT_OUTPUTS, "unexpected": "blocked"}})
    assert set(result) == set(ALLOWED_OUTPUTS)
    for key in ALLOWED_OUTPUTS:
        assert result[key] == TRANSPORT_OUTPUTS[key]


def test_default_result_has_allowed_outputs() -> None:
    result = connector.run(VALID_INPUTS)
    assert set(result) == set(ALLOWED_OUTPUTS)


if __name__ == "__main__":
    test_required_inputs_are_enforced()
    test_transport_result_is_filtered_to_allowed_outputs()
    test_default_result_has_allowed_outputs()
'''


def _connector_scaffold_qa_manifest(
    *,
    invocation: WorkflowExtensionBuildInvocation,
    spec: _ConnectorScaffoldSpec,
    source: str,
    test_source: str,
    command_label: str,
) -> dict[str, object]:
    implementation_hash = _sha256_text(source)
    return {
        "version": "appv24.extension_qa/v1",
        "kind": "connector",
        "extension_id": spec.extension_id,
        "source": f"local://{spec.source_path.as_posix()}@{implementation_hash}",
        "status": "passed",
        "qa": {
            "schema_validation": "passed",
            "tests": [{"command": command_label, "status": "passed"}],
            "side_effect_policy": "passed",
            "runtime_boundary": "passed",
            "adapter_contract": "passed",
        },
        "contract": {
            "required_scopes": list(spec.required_scopes),
            "allowed_outputs": list(spec.allowed_outputs),
        },
        "fingerprints": {
            "spec": _sha256_path(invocation.prompt_path),
            "implementation": implementation_hash,
            "tests": _sha256_text(test_source),
            "fixtures": _sha256_fixture_dir(invocation.package_path / "dry_run_fixtures"),
        },
    }


def _write_scaffold_files(
    invocation: WorkflowExtensionBuildInvocation,
    file_specs: tuple[tuple[Path, str], ...],
) -> tuple[str, ...]:
    package_path = invocation.package_path.resolve()
    allowed_root = _allowed_artifact_bundle_root(invocation).resolve()
    issues: list[str] = []
    for target_path, content in file_specs:
        resolved = target_path.resolve()
        if not _is_relative_to(resolved, package_path):
            issues.append(f"scaffold file path is outside package: {target_path}")
            continue
        if not _is_relative_to(resolved, allowed_root):
            issues.append(
                "scaffold file path is outside allowed extension root: "
                f"{_relative_path_label(resolved, package_path)}"
            )
            continue
        syntax_issue = _python_syntax_issue(resolved, content)
        if syntax_issue:
            issues.append(syntax_issue)
    if issues:
        return tuple(issues)

    try:
        for target_path, content in file_specs:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target_path.with_name(f".{target_path.name}.appv24-tmp")
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(target_path)
    except OSError as error:
        return (f"failed to write scaffold files: {error}",)
    return ()


def _read_yaml_mapping(path: Path) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        return {}, (f"failed to read {path.name}: {error}",)
    if not isinstance(parsed, Mapping):
        return {}, (f"{path.name} must be a mapping",)
    return parsed, ()


def _find_connector_action(
    document: Mapping[str, Any],
    connector_id: str,
    action_id: str,
) -> Mapping[str, Any] | None:
    for connector in _list_of_mappings(document.get("connectors")):
        if connector.get("id") != connector_id:
            continue
        for action in _list_of_mappings(connector.get("actions")):
            if action.get("id") == action_id:
                return action
    return None


def _list_of_mappings(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _allowed_outputs(action: Mapping[str, Any]) -> tuple[str, ...]:
    outputs = action.get("outputs")
    if not isinstance(outputs, Mapping):
        return ()
    return _string_tuple(outputs.get("allowed"))


def _example_input_value(field: str) -> object:
    lowered = field.lower()
    if "domains" in lowered:
        return ["example.com"]
    if "date" in lowered:
        return "2026-07-09"
    if "email" in lowered:
        return "test@example.com"
    if "query" in lowered:
        return "example"
    return f"example-{field}"


def _example_output_value(field: str) -> object:
    if field == "count" or field.endswith("_count"):
        return 1
    if field.endswith("_hash"):
        return "example-hash"
    if field.endswith("s") or field in {"items", "records", "failures"}:
        return [{"id": "example"}] if field != "failures" else []
    return f"example-{field}"


def _resolve_handoff(package_or_manifest: str | Path) -> _ResolvedHandoff:
    raw = Path(package_or_manifest).expanduser()
    resolved = raw.resolve()
    manifest_path = resolved if resolved.is_file() else resolved / ".workflow-extension-handoff" / HANDOFF_MANIFEST_FILENAME
    manifest, manifest_issues = _read_json_mapping(manifest_path)
    if manifest_issues:
        package_path = resolved if resolved.is_dir() else resolved.parent
        return _ResolvedHandoff(package_path, manifest_path, None, {}, manifest_issues)
    if manifest.get("version") != "appv24.workflow-extension-handoff/v1":
        return _ResolvedHandoff(
            _package_path_from_manifest(manifest, fallback=manifest_path.parent),
            manifest_path,
            None,
            manifest,
            ("unsupported handoff manifest version",),
        )
    package_path = _package_path_from_manifest(manifest, fallback=manifest_path.parent.parent)
    dispatch_path = _path_from_manifest(manifest_path, manifest, "dispatch_outbox_path", DISPATCH_OUTBOX_FILENAME)
    return _ResolvedHandoff(package_path, manifest_path, dispatch_path, manifest)


def _read_dispatch_records(path: Path | None) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    if path is None:
        return (), ("dispatch outbox path is missing",)
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
    if not records and not issues:
        issues.append("dispatch outbox is empty")
    return tuple(records), tuple(issues)


def _build_invocation(
    resolved: _ResolvedHandoff,
    record: Mapping[str, Any],
) -> tuple[WorkflowExtensionBuildInvocation | None, tuple[str, ...]]:
    assert resolved.manifest_path is not None
    extension_id = _string(record.get("extension_id")) or ""
    kind = _string(record.get("kind")) or ""
    request_id = _string(record.get("request_id")) or extension_id
    if not extension_id:
        return None, ("dispatch record is missing extension_id",)
    if kind not in {"connector", "middleware"}:
        return None, (f"dispatch record for {extension_id} has unsupported kind: {kind or '<missing>'}",)
    prompt_path = _record_path(resolved.manifest_path, record, "prompt_path")
    qa_return_manifest_path = _record_path(resolved.manifest_path, record, "qa_return_manifest_path")
    if prompt_path is None:
        return None, (f"dispatch record for {extension_id} is missing prompt_path",)
    if qa_return_manifest_path is None:
        return None, (f"dispatch record for {extension_id} is missing qa_return_manifest_path",)
    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, (f"failed to read PRD prompt for {extension_id}: {error}",)
    log_path = (
        resolved.manifest_path.parent
        / BUILD_LOG_DIRNAME
        / f"{_safe_filename_part(request_id or extension_id)}.log"
    )
    argv = (
        sys.executable,
        "-m",
        "appv24.cli",
        "--cwd",
        str(resolved.package_path),
        "--profile",
        "coding",
        prompt_text,
    )
    return (
        WorkflowExtensionBuildInvocation(
            extension_id=extension_id,
            kind=kind,
            package_path=resolved.package_path,
            manifest_path=resolved.manifest_path,
            prompt_path=prompt_path,
            qa_return_manifest_path=qa_return_manifest_path,
            log_path=log_path,
            argv=argv,
            env=_build_subprocess_env(),
            request_id=request_id,
        ),
        (),
    )


def _apply_stdout_artifact_bundle(
    invocation: WorkflowExtensionBuildInvocation,
    stdout: str,
) -> tuple[str, ...]:
    bundle, extract_issues = _extract_stdout_artifact_bundle(stdout)
    if extract_issues or bundle is None:
        return extract_issues
    return _apply_artifact_bundle(invocation, bundle)


def _extract_stdout_artifact_bundle(stdout: str) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    begin = stdout.find(ARTIFACT_BUNDLE_BEGIN)
    end = stdout.find(ARTIFACT_BUNDLE_END, begin + len(ARTIFACT_BUNDLE_BEGIN)) if begin >= 0 else -1
    if begin < 0 and end < 0:
        return None, ()
    if begin < 0 or end < 0:
        return None, ("artifact bundle stdout markers are incomplete",)
    payload = stdout[begin + len(ARTIFACT_BUNDLE_BEGIN) : end].strip()
    if not payload:
        return None, ("artifact bundle payload is empty",)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        return None, (f"artifact bundle payload is invalid JSON: {error}",)
    if not isinstance(parsed, Mapping):
        return None, ("artifact bundle payload must be a JSON object",)
    return parsed, ()


def _apply_artifact_bundle(
    invocation: WorkflowExtensionBuildInvocation,
    bundle: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    if bundle.get("version") != ARTIFACT_BUNDLE_VERSION:
        issues.append(f"artifact bundle version must be {ARTIFACT_BUNDLE_VERSION}")
    if bundle.get("extension_id") != invocation.extension_id:
        issues.append(f"artifact bundle extension_id must be {invocation.extension_id}")
    if bundle.get("kind") != invocation.kind:
        issues.append(f"artifact bundle kind must be {invocation.kind}")

    file_specs, file_issues = _validated_artifact_bundle_files(invocation, bundle.get("files"))
    issues.extend(file_issues)
    qa_manifest = bundle.get("qa_manifest")
    if not isinstance(qa_manifest, Mapping):
        issues.append("artifact bundle qa_manifest must be a mapping")
    elif qa_manifest.get("extension_id") != invocation.extension_id:
        issues.append(f"artifact bundle qa_manifest.extension_id must be {invocation.extension_id}")
    if issues:
        return tuple(issues)

    write_issues = _write_artifact_bundle_files(file_specs)
    if write_issues:
        return write_issues
    assert isinstance(qa_manifest, Mapping)
    invocation.qa_return_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    invocation.qa_return_manifest_path.write_text(yaml.safe_dump(dict(qa_manifest), sort_keys=False), encoding="utf-8")
    return ()


def _validated_artifact_bundle_files(
    invocation: WorkflowExtensionBuildInvocation,
    raw_files: object,
) -> tuple[tuple[tuple[Path, str], ...], tuple[str, ...]]:
    if not isinstance(raw_files, list) or not raw_files:
        return (), ("artifact bundle files must be a non-empty list",)
    package_path = invocation.package_path.resolve()
    allowed_root = _allowed_artifact_bundle_root(invocation).resolve()
    issues: list[str] = []
    file_specs: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for index, raw_file in enumerate(raw_files):
        if not isinstance(raw_file, Mapping):
            issues.append(f"artifact bundle files[{index}] must be a mapping")
            continue
        raw_path = _string(raw_file.get("path"))
        content = raw_file.get("content")
        if raw_path is None:
            issues.append(f"artifact bundle files[{index}].path is required")
            continue
        if not isinstance(content, str):
            issues.append(f"artifact bundle files[{index}].content must be a string")
            continue
        relative_path = Path(raw_path)
        if relative_path.is_absolute():
            issues.append(f"artifact bundle file path is outside package: {raw_path}")
            continue
        target_path = (package_path / relative_path).resolve()
        if not _is_relative_to(target_path, package_path):
            issues.append(f"artifact bundle file path is outside package: {raw_path}")
            continue
        if not _is_relative_to(target_path, allowed_root):
            issues.append(
                "artifact bundle file path is outside allowed extension root: "
                f"{raw_path} (expected under {_relative_path_label(allowed_root, package_path)})"
            )
            continue
        if target_path in seen:
            issues.append(f"artifact bundle file path is duplicated: {raw_path}")
            continue
        syntax_issue = _python_syntax_issue(target_path, content)
        if syntax_issue:
            issues.append(syntax_issue)
            continue
        existing_issue = _existing_file_issue(target_path, content)
        if existing_issue:
            issues.append(existing_issue)
            continue
        seen.add(target_path)
        file_specs.append((target_path, content))
    if not file_specs and not issues:
        issues.append("artifact bundle files must include at least one file")
    return tuple(file_specs), tuple(issues)


def _allowed_artifact_bundle_root(invocation: WorkflowExtensionBuildInvocation) -> Path:
    if invocation.kind == "connector":
        connector_id, _, action_id = invocation.extension_id.partition(".")
        if connector_id and action_id:
            return invocation.package_path / "connectors" / connector_id
    return invocation.package_path / "middlewares" / _safe_filename_part(invocation.extension_id)


def _python_syntax_issue(path: Path, content: str) -> str | None:
    if path.suffix != ".py":
        return None
    try:
        ast.parse(content, filename=str(path))
    except SyntaxError as error:
        return f"artifact bundle python file is invalid: {path.name}: {error.msg}"
    return None


def _existing_file_issue(path: Path, content: str) -> str | None:
    if not path.exists():
        return None
    try:
        existing = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return f"failed to read existing artifact bundle file {path}: {error}"
    if existing != content:
        return f"artifact bundle refuses to overwrite existing file with different content: {path}"
    return None


def _write_artifact_bundle_files(file_specs: tuple[tuple[Path, str], ...]) -> tuple[str, ...]:
    temp_paths: list[Path] = []
    try:
        for target_path, content in file_specs:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target_path.with_name(f".{target_path.name}.appv24-tmp")
            temp_path.write_text(content, encoding="utf-8")
            temp_paths.append(temp_path)
        for (target_path, _content), temp_path in zip(file_specs, temp_paths, strict=True):
            temp_path.replace(target_path)
    except OSError as error:
        for temp_path in temp_paths:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return (f"failed to write artifact bundle files: {error}",)
    return ()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_path_label(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _write_build_log(invocation: WorkflowExtensionBuildInvocation, result: WorkflowExtensionBuildCommandResult) -> None:
    invocation.log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"extension_id: {invocation.extension_id}",
        f"request_id: {invocation.request_id}",
        f"returncode: {result.returncode}",
        f"timed_out: {str(result.timed_out).lower()}",
    ]
    if result.timeout_seconds is not None:
        lines.append(f"timeout_seconds: {_format_seconds(result.timeout_seconds)}")
    lines.extend(
        [
            "stdout:",
            _redact(result.stdout),
            "stderr:",
            _redact(result.stderr),
        ]
    )
    invocation.log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _applied_extensions(report: WorkflowExtensionReturnedQaApplyReport | None) -> tuple[str, ...]:
    if report is None:
        return ()
    return tuple(applied.extension_id for applied in report.applied_reports)


def _next_commands(report: WorkflowExtensionReturnedQaApplyReport | None) -> tuple[str, ...]:
    if report is None:
        return ()
    commands: list[str] = []
    seen: set[str] = set()
    for applied in report.applied_reports:
        for command in applied.next_commands:
            if command in seen:
                continue
            seen.add(command)
            commands.append(command)
    return tuple(commands)


def _build_report_status(
    *,
    issues: tuple[str, ...],
    built_extensions: tuple[str, ...],
    applied_extensions: tuple[str, ...],
    validation_ok: bool,
) -> str:
    if issues:
        return "blocked"
    if built_extensions and applied_extensions and validation_ok:
        return "applied"
    if built_extensions or applied_extensions:
        return "blocked"
    return "skipped"


def normalize_workflow_extension_build_timeout(value: float | int | str | None) -> float:
    if value is None:
        value = os.environ.get(BUILD_TIMEOUT_ENV)
    if value is None or value == "":
        return DEFAULT_BUILD_TIMEOUT_SECONDS
    try:
        timeout = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{BUILD_TIMEOUT_ENV} must be a positive number of seconds") from error
    if timeout <= 0:
        raise ValueError(f"{BUILD_TIMEOUT_ENV} must be a positive number of seconds")
    return timeout


def _emit_progress(
    progress: WorkflowExtensionBuildProgress | None,
    event: WorkflowExtensionBuildEvent,
) -> None:
    if progress is not None:
        progress(event)


def _completed_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _format_seconds(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    as_float = float(value)
    if as_float.is_integer():
        return f"{int(as_float)}s"
    return f"{as_float:g}s"


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_path(path: Path) -> str:
    try:
        return _sha256_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return _sha256_text("")


def _sha256_fixture_dir(path: Path) -> str:
    if not path.is_dir():
        return _sha256_text("")
    chunks: list[str] = []
    for fixture in sorted(path.glob("*.yaml")):
        try:
            chunks.append(f"{fixture.name}\n{fixture.read_text(encoding='utf-8')}")
        except (OSError, UnicodeDecodeError):
            chunks.append(f"{fixture.name}\n")
    return _sha256_text("\n".join(chunks))


def _record_path(manifest_path: Path, record: Mapping[str, Any], key: str) -> Path | None:
    value = _string(record.get(key))
    if value is None:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _path_from_manifest(manifest_path: Path, manifest: Mapping[str, Any], key: str, default: str) -> Path:
    value = _string(manifest.get(key)) or default
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _package_path_from_manifest(manifest: Mapping[str, Any], *, fallback: Path) -> Path:
    value = _string(manifest.get("package_path"))
    if value is None:
        return fallback.resolve()
    return Path(value).expanduser().resolve()


def _read_json_mapping(path: Path) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return {}, (f"failed to read handoff manifest: {error}",)
    if not isinstance(parsed, Mapping):
        return {}, ("handoff manifest must be a mapping",)
    return parsed, ()


def _redact(value: str) -> str:
    redacted = value
    for pattern in BUILD_LOG_SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _build_subprocess_env() -> Mapping[str, str]:
    env = dict(os.environ)
    source_root = Path(__file__).resolve().parents[2]
    current_pythonpath = env.get("PYTHONPATH", "")
    entries = [str(source_root)]
    if current_pythonpath:
        entries.extend(entry for entry in current_pythonpath.split(os.pathsep) if entry)
    env["PYTHONPATH"] = os.pathsep.join(_unique(entries))
    return env


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)


def _safe_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "extension"


def _safe_python_file_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "extension"


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
