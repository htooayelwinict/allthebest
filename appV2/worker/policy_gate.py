"""Runtime policy gate for AppV2 tool and mutation proposals."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from appV2.schemas import FileOperation, MutationPolicy, PhaseStep, ToolCallProposal


TOOL_GROUPS_BY_NAME = {
    "repo_snapshot": "repo_read",
    "list_dir": "repo_read",
    "read_file": "repo_read",
    "read_many_files": "repo_read",
    "file_search": "repo_read",
    "text_search": "repo_read",
    "json_query": "repo_read",
    "git_status": "repo_read",
    "git_diff": "repo_read",
    "diff_summary": "repo_read",
    "mutation_scope_check": "repo_read",
    "classify_file_management_candidates": "repo_read",
    "write_file": "file_write",
    "write_many_files": "file_write",
    "replace_in_file": "file_write",
    "apply_file_operations": "file_write",
    "move_file": "file_write",
    "delete_file": "file_write",
    "write_json_manifest": "file_write",
    "runtime_capabilities": "verify",
    "run_readonly_command": "verify",
    "run_project_tests": "verify",
    "run_focused_tests": "verify",
    "run_required_verification": "verify",
    "verify_file_state": "verify",
    "verify_file_state_against_manifest": "verify",
    "scope_audit": "verify",
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str = "allowed"
    message: str = "allowed"
    repairable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class PolicyGate:
    def __init__(self, *, root_path: str | Path) -> None:
        self.root = Path(root_path).resolve()

    def validate_tool_call(self, *, phase: PhaseStep, proposal: ToolCallProposal) -> PolicyDecision:
        group = TOOL_GROUPS_BY_NAME.get(proposal.tool_name)
        if group is None:
            return PolicyDecision(False, "unknown_tool", f"Unknown tool: {proposal.tool_name}", repairable=True)
        if group not in phase.allowed_tool_groups:
            return PolicyDecision(
                False,
                "tool_group_not_allowed",
                f"Tool {proposal.tool_name} belongs to {group}, which is not allowed in phase {phase.phase_id}",
                repairable=True,
                metadata={"tool_group": group, "allowed_tool_groups": list(phase.allowed_tool_groups)},
            )
        return PolicyDecision(True, metadata={"tool_group": group})

    def validate_mutation(self, *, phase: PhaseStep, operations: list[FileOperation]) -> PolicyDecision:
        policy = phase.mutation_policy
        if phase.phase != "MUTATE":
            return PolicyDecision(False, "mutation_outside_mutate_phase", "Mutation is only allowed in MUTATE phase", repairable=True)
        if policy is None:
            return PolicyDecision(False, "mutation_policy_missing", "Mutation policy is required", repairable=False)
        if not operations:
            return PolicyDecision(False, "empty_mutation", "Mutation proposal contains no operations", repairable=True)
        if len(_operation_touched_paths(operations)) > policy.max_files:
            return PolicyDecision(
                False,
                "mutation_too_many_files",
                f"Mutation touches more than max_files={policy.max_files}",
                repairable=True,
                metadata={"max_files": policy.max_files, "touched_paths": sorted(_operation_touched_paths(operations))},
            )
        for operation in operations:
            action_check = self._validate_action_allowed(operation, policy)
            if not action_check.allowed:
                return action_check
            for path in _paths_for_operation(operation):
                normalized = self.normalize_repo_path(path)
                if normalized is None:
                    return PolicyDecision(
                        False,
                        "path_outside_repo",
                        f"Path escapes repo root or is invalid: {path}",
                        repairable=False,
                        metadata={"path": path},
                    )
                forbidden = self._forbidden_reason(normalized, policy)
                if forbidden:
                    return PolicyDecision(
                        False,
                        "forbidden_path",
                        forbidden,
                        repairable=False,
                        metadata={"path": normalized},
                    )
                if policy.mode == "strict" and policy.allowed_paths:
                    allowed = {_strip(path) for path in policy.allowed_paths}
                    if normalized not in allowed:
                        return PolicyDecision(
                            False,
                            "path_not_in_strict_policy",
                            f"Path {normalized} is not allowed by strict mutation policy",
                            repairable=True,
                            metadata={"path": normalized, "allowed_paths": sorted(allowed)},
                        )
        return PolicyDecision(True, metadata={"touched_paths": sorted(_operation_touched_paths(operations))})

    def normalize_repo_path(self, path: str | None) -> str | None:
        if not path:
            return None
        candidate = Path(path)
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            target = (self.root / candidate).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None
        return target.relative_to(self.root).as_posix()

    def _validate_action_allowed(self, operation: FileOperation, policy: MutationPolicy) -> PolicyDecision:
        if operation.action == "write" and not (policy.allow_create or policy.allow_update):
            return PolicyDecision(False, "write_not_allowed", "Policy does not allow writes", repairable=True)
        if operation.action == "replace" and not policy.allow_update:
            return PolicyDecision(False, "replace_not_allowed", "Policy does not allow updates", repairable=True)
        if operation.action == "move" and not policy.allow_move:
            return PolicyDecision(False, "move_not_allowed", "Policy does not allow moves", repairable=True)
        if operation.action == "delete" and not policy.allow_delete:
            return PolicyDecision(False, "delete_not_allowed", "Policy does not allow deletes", repairable=True)
        return PolicyDecision(True)

    def _forbidden_reason(self, path: str, policy: MutationPolicy) -> str | None:
        forbidden_paths = {_strip(candidate) for candidate in policy.forbidden_paths}
        if path in forbidden_paths:
            return f"Path {path} is forbidden"
        for pattern in policy.forbidden_globs:
            if fnmatch.fnmatch(path, pattern):
                return f"Path {path} matches forbidden glob {pattern}"
        return None


def _paths_for_operation(operation: FileOperation) -> list[str]:
    if operation.action == "move":
        return [path for path in (operation.source, operation.destination) if path]
    return [path for path in (operation.path,) if path]


def _operation_touched_paths(operations: list[FileOperation]) -> set[str]:
    paths: set[str] = set()
    for operation in operations:
        paths.update(path for path in _paths_for_operation(operation) if path)
    return paths


def _strip(path: str) -> str:
    return path.strip().lstrip("./")
