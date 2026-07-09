"""Offline QA evidence validation for workflow extensions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

SHA256_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class WorkflowExtensionQAIssue:
    code: str
    path: tuple[str | int, ...]
    message: str
    expected: str | None = None
    repair_hint: str | None = None


@dataclass(frozen=True)
class WorkflowExtensionQAReport:
    issues: tuple[WorkflowExtensionQAIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues


def validate_extension_qa_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_kind: str | None = None,
    expected_extension_id: str | None = None,
    expected_required_scopes: tuple[str, ...] | None = None,
    expected_allowed_outputs: tuple[str, ...] | None = None,
) -> WorkflowExtensionQAReport:
    issues: list[WorkflowExtensionQAIssue] = []
    _require_equal(issues, manifest, ("version",), "appv24.extension_qa/v1", code="extension_qa_version")
    if expected_kind is not None:
        _require_equal(issues, manifest, ("kind",), expected_kind, code="extension_qa_kind")
    else:
        _require_one_of(issues, manifest, ("kind",), {"connector", "middleware"}, code="extension_qa_kind")
    if expected_extension_id is not None:
        _require_equal(issues, manifest, ("extension_id",), expected_extension_id, code="extension_qa_extension_id")
    elif not _string(manifest.get("extension_id")):
        issues.append(_issue("extension_qa_extension_id", ("extension_id",), "extension QA requires extension_id"))
    if not _string(manifest.get("source")):
        issues.append(_issue("extension_qa_source", ("source",), "extension QA requires a source reference"))
    if manifest.get("status") != "passed":
        issues.append(
            _issue(
                "extension_qa_status",
                ("status",),
                "extension QA status must be passed",
                expected="passed",
            )
        )

    qa = manifest.get("qa")
    qa_map = qa if isinstance(qa, Mapping) else {}
    _require_equal(issues, qa_map, ("schema_validation",), "passed", code="extension_qa_schema_validation")
    _require_equal(issues, qa_map, ("side_effect_policy",), "passed", code="extension_qa_side_effect_policy")
    _require_equal(issues, qa_map, ("runtime_boundary",), "passed", code="extension_qa_runtime_boundary")
    _require_equal(issues, qa_map, ("adapter_contract",), "passed", code="extension_qa_adapter_contract")
    issues.extend(_test_issues(qa_map.get("tests")))
    issues.extend(
        _contract_issues(
            manifest.get("contract"),
            expected_required_scopes=expected_required_scopes,
            expected_allowed_outputs=expected_allowed_outputs,
        )
    )
    issues.extend(_fingerprint_issues(manifest.get("fingerprints")))
    return WorkflowExtensionQAReport(issues=tuple(issues))


def _test_issues(value: object) -> list[WorkflowExtensionQAIssue]:
    if not isinstance(value, list) or not value:
        return [_issue("extension_qa_tests_missing", ("qa", "tests"), "extension QA requires at least one test command")]
    issues: list[WorkflowExtensionQAIssue] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            issues.append(_issue("extension_qa_test_invalid", ("qa", "tests", index), "extension QA test entries must be objects"))
            continue
        command = _string(entry.get("command"))
        if command is None:
            issues.append(
                _issue(
                    "extension_qa_test_command_missing",
                    ("qa", "tests", index, "command"),
                    "extension QA test requires command",
                )
            )
        if entry.get("status") != "passed":
            issues.append(
                _issue(
                    "extension_qa_test_failed",
                    ("qa", "tests", index, "status"),
                    "extension QA test status must be passed",
                    expected="passed",
                )
            )
    return issues


def _fingerprint_issues(value: object) -> list[WorkflowExtensionQAIssue]:
    fingerprints = value if isinstance(value, Mapping) else {}
    issues: list[WorkflowExtensionQAIssue] = []
    for key in ("spec", "implementation", "tests", "fixtures"):
        fingerprint = _string(fingerprints.get(key))
        if fingerprint is None:
            issues.append(
                _issue(
                    "extension_qa_fingerprint_missing",
                    ("fingerprints", key),
                    f"extension QA requires {key} fingerprint",
                    expected=f"fingerprints.{key}",
                )
            )
            continue
        if SHA256_FINGERPRINT_RE.fullmatch(fingerprint) is None:
            issues.append(
                _issue(
                    "extension_qa_fingerprint_invalid",
                    ("fingerprints", key),
                    f"extension QA {key} fingerprint must be sha256:<64 lowercase hex characters>",
                    expected="sha256:<64 lowercase hex characters>",
                )
            )
    return issues


def _contract_issues(
    value: object,
    *,
    expected_required_scopes: tuple[str, ...] | None,
    expected_allowed_outputs: tuple[str, ...] | None,
) -> list[WorkflowExtensionQAIssue]:
    contract = value if isinstance(value, Mapping) else {}
    issues: list[WorkflowExtensionQAIssue] = []
    if expected_required_scopes is not None:
        actual = _string_tuple(contract.get("required_scopes"))
        if actual != expected_required_scopes:
            issues.append(
                _issue(
                    "extension_qa_required_scopes",
                    ("contract", "required_scopes"),
                    "contract.required_scopes must match the connector action required_scopes",
                    expected=_expected_list_label(expected_required_scopes),
                )
            )
    if expected_allowed_outputs is not None:
        actual = _string_tuple(contract.get("allowed_outputs"))
        if actual != expected_allowed_outputs:
            issues.append(
                _issue(
                    "extension_qa_allowed_outputs",
                    ("contract", "allowed_outputs"),
                    "contract.allowed_outputs must match the connector action outputs.allowed",
                    expected=_expected_list_label(expected_allowed_outputs),
                )
            )
    return issues


def _require_equal(
    issues: list[WorkflowExtensionQAIssue],
    mapping: Mapping[str, Any],
    path: tuple[str | int, ...],
    expected: str,
    *,
    code: str,
) -> None:
    value = _value_at(mapping, path)
    if value == expected:
        return
    issues.append(_issue(code, path, f"{'.'.join(str(part) for part in path)} must be {expected}", expected=expected))


def _require_one_of(
    issues: list[WorkflowExtensionQAIssue],
    mapping: Mapping[str, Any],
    path: tuple[str | int, ...],
    expected: set[str],
    *,
    code: str,
) -> None:
    value = _value_at(mapping, path)
    if value in expected:
        return
    issues.append(
        _issue(
            code,
            path,
            f"{'.'.join(str(part) for part in path)} must be one of {', '.join(sorted(expected))}",
            expected=", ".join(sorted(expected)),
        )
    )


def _value_at(mapping: Mapping[str, Any], path: tuple[str | int, ...]) -> object:
    current: object = mapping
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _issue(
    code: str,
    path: tuple[str | int, ...],
    message: str,
    *,
    expected: str | None = None,
) -> WorkflowExtensionQAIssue:
    return WorkflowExtensionQAIssue(
        code=code,
        path=path,
        message=message,
        expected=expected,
        repair_hint="Regenerate the extension artifact through the coding profile and rerun its QA command.",
    )


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _expected_list_label(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(values) + "]"
