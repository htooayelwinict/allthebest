"""Deterministic appv24 workflow bundle export."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_registry import inspect_workflow_connector_registry
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.deployment_intent import deployment_intent_to_dict
from appv24.workflows.iac_export import export_deployment_intent
from appv24.workflows.ir import WorkflowIR
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_env_template import build_workflow_live_env_template, format_workflow_live_env_template
from appv24.workflows.live_plan import WorkflowLivePlan, build_workflow_live_plan_from_path
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.policy import PolicyReport, evaluate_policy
from appv24.workflows.service_export import build_local_service_export
from appv24.workflows.validator import validate_workflow_documents


@dataclass(frozen=True)
class WorkflowBundleExport:
    bundle_id: str
    workflow_name: str
    package_path: Path
    package_fingerprint: str
    files: Mapping[str, str]
    next_commands: tuple[str, ...] = ()
    live_plan_status: str | None = None
    output_dir: Path | None = None


@dataclass(frozen=True)
class WorkflowBundleCheckReport:
    bundle_dir: Path
    bundle_id: str | None
    package_fingerprint: str | None
    files_checked: tuple[str, ...]
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def build_workflow_bundle(
    package_path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
) -> WorkflowBundleExport:
    """Build an export-only workflow runtime bundle without running connectors."""

    documents = load_package_documents(package_path)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        issues = "; ".join(issue.message for issue in validation.issues[:5])
        raise ValueError(f"workflow package is not bundle-exportable: {issues}")

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        issues = "; ".join(issue.message for issue in compiled.issues[:5])
        raise ValueError(f"workflow package is not bundle-exportable: {issues}")

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        issues = "; ".join(issue.message for issue in policy.issues[:5])
        raise ValueError(f"workflow package is not bundle-exportable: {issues}")

    package_fingerprint = fingerprint_package_documents(documents)
    bundle_id = f"bundle-{package_fingerprint.removeprefix('sha256:')[:12]}"
    registry = inspect_workflow_connector_registry(documents.root, env=env or {})
    intent = export_deployment_intent(compiled.ir, documents)
    service_export = build_local_service_export(intent, package_path=documents.root)
    live_plan = _bundle_live_plan(documents)
    files = _bundle_files(
        bundle_id=bundle_id,
        documents=documents,
        ir=compiled.ir,
        policy=policy,
        package_fingerprint=package_fingerprint,
        registry=registry,
        live_plan=live_plan,
        deployment_intent=deployment_intent_to_dict(intent),
        service_files=service_export.files if service_export.services else {},
    )
    return WorkflowBundleExport(
        bundle_id=bundle_id,
        workflow_name=compiled.ir.name,
        package_path=documents.root,
        package_fingerprint=package_fingerprint,
        files=files,
        next_commands=live_plan.next_commands if live_plan is not None else (),
        live_plan_status=live_plan.status if live_plan is not None else None,
    )


def write_workflow_bundle(
    package_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> WorkflowBundleExport:
    """Write an export-only workflow bundle directory."""

    bundle = build_workflow_bundle(package_path, env=env)
    target = Path(output_dir).expanduser().resolve() if output_dir is not None else bundle.package_path / ".workflow-bundle"
    target.mkdir(parents=True, exist_ok=True)
    for filename, content in bundle.files.items():
        file_path = target / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    return WorkflowBundleExport(
        bundle_id=bundle.bundle_id,
        workflow_name=bundle.workflow_name,
        package_path=bundle.package_path,
        package_fingerprint=bundle.package_fingerprint,
        files=bundle.files,
        next_commands=bundle.next_commands,
        live_plan_status=bundle.live_plan_status,
        output_dir=target,
    )


def format_workflow_bundle_export(bundle: WorkflowBundleExport) -> str:
    lines = [
        "Workflow bundle exported:",
        f"workflow: {bundle.workflow_name}",
        f"bundle id: {bundle.bundle_id}",
        f"package: {bundle.package_path}",
        f"output: {bundle.output_dir or '<not written>'}",
        "runtime mode: export-only",
        "side effects: none",
        "files:",
    ]
    lines.extend(f"- {filename}" for filename in sorted(bundle.files))
    if bundle.next_commands:
        lines.append("next commands:")
        lines.extend(f"- {command}" for command in bundle.next_commands)
    return "\n".join(lines)


def check_workflow_bundle(bundle_dir: str | Path) -> WorkflowBundleCheckReport:
    root = Path(bundle_dir).expanduser().resolve()
    manifest_path = root / "bundle.manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return WorkflowBundleCheckReport(root, None, None, (), ("missing bundle.manifest.json",))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return WorkflowBundleCheckReport(root, None, None, (), (f"invalid bundle.manifest.json: {error}",))

    if not isinstance(manifest, dict):
        return WorkflowBundleCheckReport(root, None, None, (), ("invalid bundle.manifest.json: expected object",))
    files = manifest.get("files")
    if not isinstance(files, dict):
        return WorkflowBundleCheckReport(
            root,
            _optional_string(manifest.get("bundle_id")),
            _optional_string(manifest.get("package_fingerprint")),
            (),
            ("invalid bundle.manifest.json: files must be an object",),
        )

    checked: list[str] = []
    issues: list[str] = []
    runtime_payload: Mapping[str, Any] | None = None
    for filename, expected_digest in sorted(files.items()):
        if not isinstance(filename, str) or not isinstance(expected_digest, str):
            issues.append("invalid bundle.manifest.json: file entries must be string digests")
            continue
        file_path = root / filename
        try:
            content = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            issues.append(f"missing file: {filename}")
            continue
        except (OSError, UnicodeDecodeError) as error:
            issues.append(f"failed to read {filename}: {error}")
            continue
        actual_digest = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
        if actual_digest != expected_digest:
            issues.append(f"checksum mismatch: {filename}")
            continue
        if filename == "runtime.json":
            try:
                parsed_runtime = json.loads(content)
            except json.JSONDecodeError as error:
                issues.append(f"invalid runtime.json: {error}")
                continue
            if isinstance(parsed_runtime, dict):
                runtime_payload = parsed_runtime
            else:
                issues.append("invalid runtime.json: expected object")
                continue
        checked.append(filename)

    if not issues:
        issues.extend(_source_package_fingerprint_issues(manifest, runtime_payload))

    return WorkflowBundleCheckReport(
        bundle_dir=root,
        bundle_id=_optional_string(manifest.get("bundle_id")),
        package_fingerprint=_optional_string(manifest.get("package_fingerprint")),
        files_checked=tuple(checked) if not issues else (),
        issues=tuple(issues),
    )


def format_workflow_bundle_check(report: WorkflowBundleCheckReport) -> str:
    lines = [
        f"Workflow bundle check: {'passed' if report.ok else 'failed'}",
        f"bundle: {report.bundle_dir}",
    ]
    if report.bundle_id:
        lines.append(f"bundle id: {report.bundle_id}")
    if report.package_fingerprint:
        lines.append(f"package fingerprint: {report.package_fingerprint}")
    if report.files_checked:
        lines.append("files checked:")
        lines.extend(f"- {filename}" for filename in report.files_checked)
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)


def _source_package_fingerprint_issues(
    manifest: Mapping[str, Any],
    runtime_payload: Mapping[str, Any] | None,
) -> list[str]:
    if runtime_payload is None:
        return []
    package_path = runtime_payload.get("package_path")
    expected = _optional_string(manifest.get("package_fingerprint"))
    if not isinstance(package_path, str) or expected is None:
        return []
    try:
        current = fingerprint_package_documents(load_package_documents(package_path))
    except Exception as error:  # noqa: BLE001 - bundle check should report stale source problems, not crash.
        return [f"source package check failed: {error}"]
    if current != expected:
        return [f"source package fingerprint changed: expected {expected} got {current}"]
    return []


def _bundle_files(
    *,
    bundle_id: str,
    documents: PackageDocuments,
    ir: WorkflowIR,
    policy: PolicyReport,
    package_fingerprint: str,
    registry: Any,
    live_plan: WorkflowLivePlan | None,
    deployment_intent: Mapping[str, Any],
    service_files: Mapping[str, str],
) -> dict[str, str]:
    connector_actions = [_connector_action_payload(action) for action in registry.actions]
    required_credential_env = sorted({hint for action in registry.actions for hint in action.required_credential_env})
    required_transport_env = sorted({hint for action in registry.actions for hint in action.required_transport_env})
    files = {
        "workflow.ir.json": _json(
            {
                "version": "appv24.workflow-ir/v1",
                "workflow_name": ir.name,
                "workflow_version": ir.version,
                "entrypoint": ir.entrypoint,
                "triggers": [_trigger_payload(trigger) for trigger in ir.triggers],
                "steps": [_step_payload(step) for step in _ordered_steps(ir, documents.workflow)],
                "design": {
                    "intent": ir.design.intent,
                    "trigger": ir.design.trigger,
                    "inputs": list(ir.design.inputs),
                    "outputs": list(ir.design.outputs),
                    "assumptions": list(ir.design.assumptions),
                    "side_effects": list(ir.design.side_effects),
                    "approval_gates": list(ir.design.approval_gates),
                    "failure_modes": list(ir.design.failure_modes),
                    "rollback": list(ir.design.rollback),
                },
            }
        ),
        "connectors.manifest.json": _json(
            {
                "version": "appv24.connectors-manifest/v1",
                "workflow_name": ir.name,
                "actions": connector_actions,
                "issues": list(registry.issues),
            }
        ),
        "deployment.intent.json": _json(deployment_intent),
        "live.env.example": _live_env_example(documents, registry),
        "policy.json": _json(
            {
                "version": "appv24.policy/v1",
                "status": "pass" if policy.ok else "fail",
                "issues": [_policy_issue_payload(issue) for issue in policy.issues],
                "deployment_intent": deployment_intent,
            }
        ),
        "secrets.required.json": _json(
            {
                "version": "appv24.secrets-required/v1",
                "required_credential_env": required_credential_env,
                "required_transport_env": required_transport_env,
                "secret_values_included": False,
            }
        ),
        "triggers.json": _json(
            {
                "version": "appv24.triggers/v1",
                "triggers": [_trigger_payload(trigger) for trigger in ir.triggers],
                "dry_run_fixtures": sorted(documents.dry_run_fixtures),
            }
        ),
        "runtime.json": _json(
            {
                "version": "appv24.runtime-bundle/v1",
                "bundle_id": bundle_id,
                "workflow_name": ir.name,
                "package_path": str(documents.root),
                "package_fingerprint": package_fingerprint,
                "runtime_mode": "export-only",
                "execution_status": "not-started",
                "side_effects": "none",
                "live_plan_status": live_plan.status if live_plan is not None else None,
                "next_commands": list(live_plan.next_commands) if live_plan is not None else [],
            }
        ),
        "README.md": _readme(
            bundle_id=bundle_id,
            workflow_name=ir.name,
            package_fingerprint=package_fingerprint,
            next_commands=live_plan.next_commands if live_plan is not None else (),
        ),
    }
    files.update(service_files)
    files["bundle.manifest.json"] = _bundle_manifest(
        bundle_id=bundle_id,
        package_fingerprint=package_fingerprint,
        files=files,
    )
    return files


def _connector_action_payload(action: Any) -> dict[str, Any]:
    return {
        "action_ref": action.action_ref,
        "connector_id": action.connector_id,
        "action_id": action.action_id,
        "cataloged": action.cataloged,
        "side_effect": action.side_effect,
        "side_effect_class": action.side_effect_class,
        "required_scopes": list(action.required_scopes),
        "allowed_outputs": list(action.allowed_outputs),
        "live_modes": list(action.live_modes),
        "configured_live_modes": list(action.configured_live_modes),
        "required_credential_env": list(action.required_credential_env),
        "required_transport_env": list(action.required_transport_env),
        "issues": list(action.issues),
    }


def _bundle_manifest(*, bundle_id: str, package_fingerprint: str, files: Mapping[str, str]) -> str:
    return _json(
        {
            "version": "appv24.workflow-bundle-manifest/v1",
            "bundle_id": bundle_id,
            "package_fingerprint": package_fingerprint,
            "files": {
                filename: f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
                for filename, content in sorted(files.items())
            },
        }
    )


def _live_env_example(documents: PackageDocuments, registry: Any) -> str:
    sections: list[str] = []
    refs_by_mode = _live_action_refs_by_mode(registry)
    for mode in (WorkflowRunMode.LIVE_READ, WorkflowRunMode.LIVE_DRAFT, WorkflowRunMode.LIVE_PRODUCTION):
        refs = refs_by_mode.get(mode.value, ())
        if not refs:
            continue
        template = build_workflow_live_env_template(
            WorkflowRunRequest(package_path=documents.root, mode=mode, connector_allowlist=refs)
        )
        sections.append(format_workflow_live_env_template(template))
    if not sections:
        return "# no live connector env required\n"
    return "\n\n".join(sections) + "\n"


def _live_action_refs_by_mode(registry: Any) -> dict[str, tuple[str, ...]]:
    refs_by_mode: dict[str, list[str]] = {}
    for action in registry.actions:
        for mode in action.live_modes:
            refs_by_mode.setdefault(str(mode), []).append(action.action_ref)
    return {mode: tuple(dict.fromkeys(refs)) for mode, refs in refs_by_mode.items()}


def _step_payload(step: Any) -> dict[str, Any]:
    return {
        "id": step.step_id,
        "kind": step.kind.value,
        "uses": step.uses.dotted if step.uses is not None else None,
        "side_effect_class": step.side_effect_class,
        "approval": step.approval,
        "next": step.next_step,
        "branches": dict(step.branches),
    }


def _ordered_steps(ir: WorkflowIR, workflow: Mapping[str, Any]) -> list[Any]:
    ordered: list[Any] = []
    for raw_step in workflow.get("steps", []):
        if not isinstance(raw_step, Mapping) or not isinstance(raw_step.get("id"), str):
            continue
        step = ir.steps.get(str(raw_step["id"]))
        if step is not None:
            ordered.append(step)
    return ordered


def _trigger_payload(trigger: Any) -> dict[str, Any]:
    return {
        "id": trigger.trigger_id,
        "type": trigger.trigger_type,
    }


def _policy_issue_payload(issue: Any) -> dict[str, Any]:
    return {
        "code": issue.code,
        "message": issue.message,
        "severity": issue.severity.value,
        "step_id": issue.step_id,
        "source": issue.source,
    }


def _bundle_live_plan(documents: PackageDocuments) -> WorkflowLivePlan | None:
    if "happy_path.yaml" not in documents.dry_run_fixtures:
        return None
    return build_workflow_live_plan_from_path(documents.root, "happy_path.yaml")


def _readme(
    *,
    bundle_id: str,
    workflow_name: str,
    package_fingerprint: str,
    next_commands: tuple[str, ...],
) -> str:
    text = (
        "# appv24 workflow bundle\n\n"
        f"- workflow: {workflow_name}\n"
        f"- bundle id: {bundle_id}\n"
        f"- package fingerprint: {package_fingerprint}\n"
        "- runtime mode: export-only\n"
        "- side effects: none\n\n"
        "This bundle is an inspectable runtime artifact. It does not contain secret values and does not execute connectors.\n"
    )
    if next_commands:
        text += "\n## Next commands\n\n"
        text += "\n".join(f"- {command}" for command in next_commands)
        text += "\n"
    return text


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
