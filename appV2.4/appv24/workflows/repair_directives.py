"""Structured workflow validation-to-repair directives."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from appv24.workflows.catalog import BUILTIN_WORKFLOW_ACTIONS, built_in_capability_catalog
from appv24.workflows.contracts import BUILTIN_LLM_OUTPUT_CONTRACTS
from appv24.workflows.quality_gate import check_workflow_quality_documents
from appv24.workflows.validator import WorkflowDraftValidationResult

MUTATING_SIDE_EFFECT_CLASSES = {"create", "update", "delete", "send", "publish", "provision"}
ACTION_REF_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+\b")
DESIGN_ONLY_ACTION_RE = re.compile(r"design-only action (?P<action>[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)")
QUOTED_VALUE_RE = re.compile(r"'(?P<value>[^']+)'")
NO_BRANCH_MATCH_RE = re.compile(r"no branch matched for (?P<step>[A-Za-z0-9_.-]+)")
DECISION_STEP_RE = re.compile(r"decision step (?P<step>[A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class WorkflowRepairDirective:
    code: str
    location: str
    instruction: str
    rules: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [f"- {self.code} {self.location}: {self.instruction}"]
        lines.extend(f"  - {rule}" for rule in self.rules)
        return "\n".join(lines)


def render_workflow_repair_directives(
    result: WorkflowDraftValidationResult,
    *,
    max_directives: int = 8,
) -> str:
    directives = build_workflow_repair_directives(result, max_directives=max_directives)
    return "\n".join(directive.render() for directive in directives)


def build_workflow_repair_directives(
    result: WorkflowDraftValidationResult,
    *,
    max_directives: int = 8,
) -> tuple[WorkflowRepairDirective, ...]:
    directives: list[WorkflowRepairDirective] = []
    for issue in _repair_issues(result):
        if len(directives) >= max(0, max_directives):
            break
        directive = _directive_for_issue(result, issue)
        if directive is not None:
            directives.append(directive)
    return tuple(directives)


def _directive_for_issue(
    result: WorkflowDraftValidationResult,
    issue: object,
) -> WorkflowRepairDirective | None:
    code = str(getattr(issue, "code", ""))
    location = str(getattr(issue, "location", ""))
    message = str(getattr(issue, "message", ""))

    if code == "raw_secret":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Replace the inline secret value at this exact path.",
            rules=(
                "Use a secret reference field, checksum, redacted marker, or non-secret reference.",
                "For secret references, use reference names such as secretRef: CONNECTOR_CREDENTIAL_REF, not raw secret values.",
                'Use normal input keys with scalar values like github_token: "secretRef: GITHUB_TOKEN_REF".',
                "Do not invent keys like github_token_secretRef: GITHUB_TOKEN.",
                "Do not preserve or restate the inline secret value.",
                "Do not move the secret into another workflow package field.",
            ),
        )

    if code == "missing_side_effect_class":
        directive = _missing_side_effect_class_directive(result, issue)
        if directive is not None:
            return directive

    if code in {"missing_side_effect_contract", "invalid_side_effect_contract"}:
        action_ref = _first_action_ref(message)
        target = f" for {action_ref}" if action_ref else ""
        action = _action_for_ref(result, action_ref)
        side_effect_class = action.get("side_effect_class") if action is not None else None
        if side_effect_class not in MUTATING_SIDE_EFFECT_CLASSES:
            return WorkflowRepairDirective(
                code=code,
                location=location,
                instruction=f"Correct the side-effect classification for that connector action{target}.",
                rules=(
                    message,
                    "If this action only reads, classifies, analyzes, summarizes, drafts, formats, or writes local artifacts, set side_effect: false and remove broker-only metadata.",
                    "If it truly creates, updates, deletes, sends, publishes, or provisions externally, keep side_effect: true and add the broker side-effect metadata.",
                    "Brokered mutating actions require approval_required: true, dry_run_supported: true, explicit outputs.allowed, and idempotency.key_fields where required.",
                ),
            )
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Add the missing broker side-effect metadata to that connector action{target}.",
            rules=(
                message,
                "Mutating connector actions must declare side_effect_class, execution_mode, approval_required, and dry_run_supported.",
                "Brokered mutating actions require approval_required: true, dry_run_supported: true, explicit outputs.allowed, and idempotency.key_fields where required.",
            ),
        )

    if code == "catalog_metadata_mismatch":
        action_ref = _first_action_ref(message)
        target = f" for {action_ref}" if action_ref else ""
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Update only the mismatched catalog metadata fields{target}.",
            rules=(
                message,
                "Keep the workflow step ids, branch names, fixtures, approvals, and rollback text unchanged.",
                "Use the built-in catalog action metadata exactly; do not invent alternate side-effect classes or execution modes.",
            ),
        )

    if code == "unknown_catalog_action":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Use a built-in action, strict external connector interface, or design-only metadata.",
            rules=(
                message,
                "Executable steps use built-ins or strict external interfaces declared in connectors.yaml.",
                f"Built-in workflow capability catalog actions: {', '.join(sorted(BUILTIN_WORKFLOW_ACTIONS))}.",
                "Strict external interface: custom: true, source@version, outputs.allowed.",
                "If this is trigger/input data, do not model it as a connector step.",
                "For future-only custom actions, set custom: true and execution_mode: design_only, and remove workflow step calls to it.",
            ),
        )

    if code == "orphan_generated_connector_action":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Attach the generated connector action to the workflow graph.",
            rules=(
                message,
                "Add a reachable workflow step that uses the connector action.",
                "Bind required connector inputs in workflow.yaml steps[].with; do not rely only on dry-run fixtures.",
                "Update transitions, permissions, approvals, and happy_path expected_path so the new step is reachable from entrypoint.",
                "Do not remove coding-profile generated connectors to make validation pass.",
                "Do not leave coding-profile generated connectors as unused package metadata.",
            ),
        )

    if code == "declared_external_system_unmodeled":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Model the design-declared external system as a reachable connector step.",
            rules=(
                message,
                "Add the connector-owned interface in connectors.yaml if it is missing.",
                "Add a reachable workflow.yaml step that calls that connector action.",
                "Bind required inputs in steps[].with and update permissions, approvals, and happy_path expected_path.",
                "Do not satisfy this by deleting the external system from design prose.",
            ),
        )

    if code == "unknown_approval_target":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Point the approval requirement at an existing workflow step or remove the unused approval.",
            rules=(
                message,
                "permissions.yaml approvals[].required_for must list workflow.yaml step ids exactly.",
                "If the approval is for a new connector step, add that step and update workflow transitions.",
                "If no workflow step needs this approval, remove the approval entry and related design approval-gate text.",
            ),
        )

    if code in {"future_step_output_reference", "unknown_step_output_reference"}:
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Fix the step output reference so it points to an existing upstream step.",
            rules=(
                message,
                "A workflow step may reference steps.<id>.outputs only when that step runs earlier in the workflow graph.",
                "Move the consuming step after the producing step, or change the reference to an output from an upstream step.",
                "Update transitions and dry_run_fixtures/happy_path.yaml expected_path to match the corrected graph.",
                "Do not leave a connector step reading outputs from a future or unreachable step.",
            ),
        )

    if code == "connector_scalar_input_collection":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Replace the collection-valued connector input with a scalar value.",
            rules=(
                message,
                "Do not pass arrays, maps, subscriber collections, or broadcast lists into scalar connector fields.",
                "For telegram.send_message, chat_id must be one explicit approved chat id or a scalar inputRef that resolves to one chat id.",
                "Until a cataloged broadcast/fanout action exists, model subscriber lists as fixture/input/future-connector design instead of a live send target.",
                "Preserve the approval gate and side-effect metadata.",
            ),
        )

    if code == "design_only_action_used":
        action_ref = _design_only_action_ref(message)
        if action_ref in BUILTIN_WORKFLOW_ACTIONS:
            return WorkflowRepairDirective(
                code=code,
                location=location,
                instruction=f"Correct connector metadata for built-in action {action_ref}.",
                rules=_builtin_action_metadata_rules(action_ref, message),
            )
        action_text = f" to design-only action {action_ref}" if action_ref else " to the design-only action"
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Remove the workflow step call{action_text}.",
            rules=(
                "Design-only connector actions may be declared but must not be called by workflow steps.",
                "keep the intended output as a non-executing local artifact draft instead.",
            ),
        )

    if code == "missing_design_contract":
        field = _missing_design_field(issue)
        if field is not None:
            rules = [
                message,
                "Preserve every existing workflow.yaml design field and add only the missing field.",
                "workflow.yaml design must include intent, trigger, inputs, outputs, assumptions, side_effects, approval_gates, failure_modes, and rollback.",
                "workflow.yaml design must be a top-level mapping, sibling to version, name, description, triggers, entrypoint, and steps.",
                "Put design before triggers and indent every design list item under design.",
            ]
            if field in {"inputs", "outputs", "assumptions", "side_effects", "approval_gates", "failure_modes"}:
                rules.extend(
                    [
                        "Use concise non-placeholder strings derived from the current workflow intent.",
                        "Do not leave examples such as Input needed by the workflow or Output produced by the workflow in the final package.",
                    ]
                )
            if field == "rollback":
                rules.extend(
                    [
                        "design.rollback must be a YAML list of manual rollback note strings.",
                        "Do not confuse design.rollback with rollback.md; both are required.",
                    ]
                )
            return WorkflowRepairDirective(
                code=code,
                location=location,
                instruction=f"Add the missing workflow.yaml design.{field} field.",
                rules=tuple(rules),
            )
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add the missing workflow.yaml design metadata.",
            rules=(
                message,
                "workflow.yaml design must include intent, trigger, inputs, outputs, assumptions, side_effects, approval_gates, failure_modes, and rollback.",
                "Preserve the workflow intent and existing package files.",
            ),
        )

    if code == "missing_decision_branches":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Replace the decision step runtime syntax with branches.",
            rules=(
                message,
                "Decision steps must use workflow.yaml steps[].branches with branch-name to target-step mappings.",
                "Do not use condition plus next/reject_next, true_next, false_next, or else_next as runtime branch syntax.",
                "Preserve the same outcome targets by moving them under branches.",
            ),
        )

    if code == "unsupported_decision_syntax":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Remove the unsupported decision transition key.",
            rules=(
                message,
                "Represent every decision outcome under workflow.yaml steps[].branches.",
                "After adding branches, make every dry-run fixture select a branch under decision:.",
            ),
        )

    if code in {"missing_fixture_decision", "unknown_fixture_decision_branch"}:
        decision_rules = _fixture_decision_rules(result, issue, message)
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add or correct the dry-run fixture decision map entry.",
            rules=(
                message,
                "Use one of the branch names from workflow.yaml steps[].branches.",
                "Place the value under decision: <step-id>: <branch-name> in the affected fixture.",
                *decision_rules,
                "Do not rely on expected_path; it is documentation only and does not drive simulation.",
            ),
        )

    if code == "unknown_catalog_action":
        catalog = ", ".join(sorted(BUILTIN_WORKFLOW_ACTIONS))
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Use built-in actions or strict external interfaces for executable connector steps.",
            rules=(
                message,
                f"Built-in action choices: {catalog}.",
                "Do not invent connector action names; declare a strict external interface for intentional new actions.",
                "Strict external interface: custom: true, source@version, outputs.allowed.",
                "If this is trigger or fixture input, model it as trigger metadata, fixture input, or a decision branch.",
                "If the workflow needs a local artifact or draft, use an existing catalog action that matches the intent or represent the artifact in design.outputs and rollback.md.",
            ),
        )

    if code in {
        "missing_llm_output_contract",
        "ambiguous_llm_output_contract",
        "unknown_llm_output_contract",
    }:
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Fix the LLM step output contract without redesigning the workflow.",
            rules=(
                message,
                "LLM nodes are typed transforms, not autonomous agents.",
                "Prefer compact with.output_contract names over inline with.output_schema to avoid token bloat.",
                f"Built-in output_contract choices: {', '.join(sorted(BUILTIN_LLM_OUTPUT_CONTRACTS))}.",
                "Set exactly one of with.output_contract or with.output_schema.",
            ),
        )

    if code == "builtin_output_contract_mismatch":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Fix connectors.yaml outputs.allowed to use supported top-level provider output fields.",
            rules=(
                message,
                "Do not put nested item fields directly in outputs.allowed.",
                "For brave.search, use top-level outputs such as items, source_count, query, or artifact_path.",
                "For source.fetch_items, use top-level outputs such as items, source_count, or artifact_path.",
                "Put sender, subject, body, timestamp, url, and similar item fields inside dry-run fixture item records or downstream input refs.",
            ),
        )

    if code in {"invalid_llm_side_effect", "invalid_llm_approval", "invalid_llm_idempotency"}:
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Keep the LLM node as a read-only typed transform.",
            rules=(
                message,
                "Do not put approval, idempotency, send, create, update, delete, publish, or provision behavior on llm.* actions.",
                "Move external effects into separate cataloged notification, ticket, email, or send steps.",
                "Use side_effect_class: read on workflow.yaml llm.* steps when a side_effect_class is present.",
            ),
        )

    if code == "missing_policy_checks":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add deterministic policy checks to the policy.check step.",
            rules=(
                message,
                "policy.check must use with.checks.",
                "Do not rely on LLM judgment as the policy gate.",
                "Each check key must be a stable snake_case policy name.",
                "Each check value must be deterministic data: booleans, thresholds, refs, lists, or small mappings.",
                "Keep policy.check read-only; approvals belong on downstream mutating create/send/publish steps.",
            ),
        )

    if code == "missing_step_transition":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add a runtime transition for this non-terminal workflow step.",
            rules=(
                message,
                "Non-terminal action/manual steps must use next: <target-step>.",
                "If the step should finish the workflow, add or target a terminal step such as next: done.",
                "Do not use branches on non-decision steps; branches are only runtime transitions for decision steps.",
            ),
        )

    if code == "missing_approval":
        step_id = _issue_step_id(issue)
        target = f" {step_id}" if step_id else ""
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Resolve approval handling for workflow step{target}.",
            rules=(
                "If the step only reads, classifies, analyzes, summarizes, drafts, formats, or writes local artifacts, set the connector action side_effect: false instead of adding approval.",
                "If the step truly creates, updates, deletes, sends, publishes, or provisions externally, keep it mutating and add an explicit approval gate.",
                "Use workflow.yaml steps[].approval, not approval_gate.",
                "The approval id must exist in permissions.yaml approvals[].id.",
                "The approval's required_for list must include the exact mutating workflow step id.",
            ),
        )

    if code == "permission_wildcard_scope":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Replace wildcard permission scopes with explicit connector scopes.",
            rules=(
                message,
                "Remove '*' from permissions.yaml permissions[].scopes.",
                "Grant only the exact required_scopes used by that connector's granted actions.",
            ),
        )

    if code == "permission_missing_scope":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Grant the exact connector scope required by the referenced action.",
            rules=(
                message,
                "Add only the missing required scope; do not use wildcard scopes.",
            ),
        )

    if code in {
        "unknown_connector_contract",
        "connector_contract_mismatch",
        "connector_contract_scope_mismatch",
        "connector_contract_output_mismatch",
    }:
        return _connector_contract_directive(code, location, message, issue)

    if code == "unsafe_rollback":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Rewrite rollback.md as manual prose only.",
            rules=(
                "Remove command examples, shell snippets, API examples, auth headers, and destructive verbs.",
                "Keep rollback guidance as operator review steps, audit notes, and manual correction guidance.",
            ),
        )

    if code in {"missing_rollback", "missing_fixture"}:
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add the missing required workflow package artifact.",
            rules=(message,),
        )

    if code in {"schema", "parse_error", "invalid_document", "missing_block", "duplicate_block"}:
        schema_directive = _schema_repair_directive(code, location, message, issue)
        if schema_directive is not None:
            return schema_directive
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Fix this exact package structure issue while preserving the workflow intent.",
            rules=(message,),
        )

    if code == "missing_transition":
        step_id = _issue_step_id(issue)
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add the smallest correct next transition for this non-terminal step.",
            rules=(
                message,
                "Preserve existing step ids and step order.",
                "Prefer connecting to the next existing step in the intended flow.",
                "Do not redesign unrelated connector actions, permissions, fixtures, or rollback notes.",
                f"Target step: {step_id}." if step_id else "Use the failed path exactly as reported.",
            ),
        )

    if code == "missing_branch_transition":
        step_id = _issue_step_id(issue)
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add explicit branches for this decision step.",
            rules=(
                message,
                "Use existing downstream step ids.",
                "Do not rename the decision step.",
                f"Target decision step: {step_id}." if step_id else "Use the failed path exactly as reported.",
            ),
        )

    if code == "terminal_unreachable":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Connect the entrypoint path to an existing terminal step.",
            rules=(
                message,
                "Prefer adding the smallest missing next transition or branch.",
                "Do not invent a new terminal if an existing terminal step can be reached.",
            ),
        )

    if code == "cycle_without_terminal_escape":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add an exit branch or transition from the cycle to an existing terminal path.",
            rules=(
                message,
                "Keep the loop only if it is part of the user intent.",
                "Do not remove approval gates or side-effect metadata.",
            ),
        )

    if code in {"fixture_no_terminal", "fixture_branch_unmatched", "fixture_transition_budget_exceeded", "fixture_broker_blocked"}:
        decision_rules = _fixture_decision_rules(result, issue, message)
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Fix the workflow transitions, fixture decisions, or dry-run support so this fixture reaches terminal.",
            rules=(
                message,
                "For decision failures, add the smallest valid dry_run_fixtures decision mapping.",
                *decision_rules,
                "For transition failures, fix workflow.yaml next or branches.",
                "For broker failures, preserve the connector action and make sure the dry-run path uses declared allowed outputs.",
            ),
        )

    return None


def _builtin_action_metadata_rules(action_ref: str, message: str) -> tuple[str, ...]:
    capability = built_in_capability_catalog().get(action_ref)
    rules = [
        message,
        f"Keep the workflow.yaml step that uses {action_ref}; do not remove or rename it.",
    ]
    if capability is None:
        rules.append("Use the built-in catalog metadata for this action exactly.")
        return tuple(rules)

    rules.extend(
        [
            f"Set connectors.yaml {action_ref} side_effect to {str(capability.side_effect).lower()}.",
            f"Set connectors.yaml {action_ref} execution_mode to {capability.execution_mode}.",
            f"Set connectors.yaml {action_ref} dry_run_supported to {str(capability.dry_run_supported).lower()}.",
        ]
    )
    if capability.side_effect_class is None:
        rules.append(f"For read-only {action_ref}, use side_effect_class: read or omit side_effect_class.")
    else:
        rules.append(f"Set connectors.yaml {action_ref} side_effect_class to {capability.side_effect_class}.")
    if capability.approval_required:
        rules.append(f"Set connectors.yaml {action_ref} approval_required to true and keep the workflow approval gate.")
    else:
        rules.append(f"Do not add approval_required to read-only {action_ref}.")
    if capability.required_scopes:
        rules.append(f"Required scopes: {', '.join(capability.required_scopes)}.")
    if capability.allowed_outputs:
        rules.append(f"Allowed outputs must be top-level fields: {', '.join(capability.allowed_outputs)}.")
    if capability.idempotency_key_fields:
        rules.append(f"Idempotency key_fields: {', '.join(capability.idempotency_key_fields)}.")
    rules.append("Edit connectors.yaml metadata only unless another validation issue names a workflow or fixture path.")
    return tuple(rules)


def _connector_contract_directive(
    code: str,
    location: str,
    message: str,
    issue: object,
) -> WorkflowRepairDirective:
    action_ref = _action_ref_from_issue_path(issue) or _first_action_ref(message)
    target = action_ref or "this action"
    expected = str(getattr(issue, "expected", "") or "")
    if code == "unknown_connector_contract":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Replace {target} with a built-in contract, strict external interface, or future-only metadata.",
            rules=(
                message,
                f"Built-in workflow capability catalog actions: {', '.join(sorted(BUILTIN_WORKFLOW_ACTIONS))}.",
                "Executable steps use built-in contracts or strict external interfaces declared in connectors.yaml.",
                "Strict external interface: custom: true, source@version, outputs.allowed.",
                "For future-only custom actions, set custom: true and execution_mode: design_only, then remove workflow step calls to that action.",
            ),
        )

    rules = [
        message,
        "Preserve workflow step ids, fixture decisions, approvals, and rollback text.",
        "Change only the connector action fields named by the validation location.",
    ]
    if code == "connector_contract_scope_mismatch" and expected:
        rules.append(f"Add required_scopes entry `{expected}` to the connector action.")
    elif code == "connector_contract_output_mismatch" and expected:
        rules.append(f"Set outputs.allowed to supported top-level fields from the contract: {expected}.")
        rules.append("Do not put nested item fields directly in outputs.allowed.")
    elif expected:
        rules.append(f"Set the mismatched connector metadata value to `{expected}`.")
    else:
        rules.append("Use the built-in connector contract metadata exactly.")

    return WorkflowRepairDirective(
        code=code,
        location=location,
        instruction=f"Update connectors.yaml metadata for {target} to match the built-in connector contract.",
        rules=tuple(rules),
    )


def _repair_issues(result: WorkflowDraftValidationResult) -> tuple[object, ...]:
    if not result.ok:
        return tuple(issue for issue in result.validation.issues if not _is_extension_handoff_issue(issue))
    if result.documents is None:
        return ()
    quality = check_workflow_quality_documents(result.documents)
    return () if quality.ok else quality.issues


def _is_extension_handoff_issue(issue: object) -> bool:
    return str(getattr(issue, "code", "")).startswith("extension_qa")


def _schema_repair_directive(
    code: str,
    location: str,
    message: str,
    issue: object,
) -> WorkflowRepairDirective | None:
    file = str(getattr(issue, "file", ""))
    path = getattr(issue, "path", ())
    if not isinstance(path, tuple) or not path:
        return None

    if file == "workflow.yaml" and _path_matches(path, "triggers", "type"):
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Normalize the workflow trigger type to the schema enum.",
            rules=(
                message,
                "workflow.yaml trigger type must be one of: schedule, webhook, manual, event.",
                "For cron-like triggers, use type: schedule with a schedule value.",
                "For event-like or signal-like triggers, use type: event and put signal metadata under event.",
                "Do not invent trigger enum values.",
            ),
        )

    if file == "workflow.yaml" and _path_matches(path, "steps", "uses"):
        value = _quoted_value(message)
        bare_action_hint = f" such as {value}" if value else ""
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Rewrite the workflow step action reference to match the connector action contract.",
            rules=(
                message,
                "workflow.yaml steps[].uses must be connector.action.",
                f"Do not use bare action ids{bare_action_hint}; use <connector-id>.<action-id>.",
                "The connector id and action id must exist in connectors.yaml.",
                "If needed, create a generic connector action for the intended local read, classify, analyze, draft, or approval-gated mutation.",
            ),
        )

    if file == "connectors.yaml" and _path_matches(path, "connectors", "actions", "execution_mode"):
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Normalize the connector action execution mode to the schema enum.",
            rules=(
                message,
                "connectors.yaml actions[].execution_mode must be brokered, manual, or design_only.",
                "Replace sync, async, automatic, or auto with brokered for real external mutations.",
                "Use manual only for manually performed actions.",
                "Use design_only only for actions that workflow steps do not call.",
                "Do not invent execution_mode enum values.",
            ),
        )

    return None


def _path_matches(path: tuple[str | int, ...], *tail: str) -> bool:
    text_parts = tuple(str(part) for part in path)
    if len(text_parts) < len(tail):
        return False
    return text_parts[0] == tail[0] and text_parts[-1] == tail[-1]


def _missing_design_field(issue: object) -> str | None:
    path = getattr(issue, "path", ())
    if not isinstance(path, tuple) or len(path) < 2 or path[0] != "design":
        return None
    field = path[1]
    return field if isinstance(field, str) and field else None


def _missing_side_effect_class_directive(
    result: WorkflowDraftValidationResult,
    issue: object,
) -> WorkflowRepairDirective | None:
    if result.documents is None or str(getattr(issue, "file", "")) != "workflow.yaml":
        return None

    step_id = _issue_step_id(issue)
    if step_id is None:
        return None

    steps = _workflow_steps_by_id(result.documents.workflow)
    step = steps.get(step_id)
    if step is None:
        return None

    uses = step.get("uses")
    if not isinstance(uses, str):
        return None

    connector_id, _, action_id = uses.partition(".")
    action = _connector_actions(result.documents.connectors).get((connector_id, action_id))
    if action is None or action.get("side_effect") is not True:
        return None

    side_effect_class = action.get("side_effect_class")
    if side_effect_class not in MUTATING_SIDE_EFFECT_CLASSES:
        return None

    return WorkflowRepairDirective(
        code=str(getattr(issue, "code", "")),
        location=str(getattr(issue, "location", "")),
        instruction=(
            f"workflow.yaml step {step_id} uses {uses}. "
            f"Add side_effect_class: {side_effect_class} to workflow.yaml step {step_id}."
        ),
        rules=(
            "Keep the existing approval gate on the mutating step.",
            "Do not change connector ids, action ids, or user workflow intent.",
        ),
    )


def _workflow_steps_by_id(workflow: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        return {}
    return {
        str(step["id"]): step
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("id"), str)
    }


def _connector_actions(connectors_document: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    actions: dict[tuple[str, str], Mapping[str, Any]] = {}
    connectors = connectors_document.get("connectors")
    if not isinstance(connectors, list):
        return actions
    for connector in connectors:
        if not isinstance(connector, dict) or not isinstance(connector.get("id"), str):
            continue
        connector_id = str(connector["id"])
        raw_actions = connector.get("actions")
        if not isinstance(raw_actions, list):
            continue
        for action in raw_actions:
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                actions[(connector_id, str(action["id"]))] = action
    return actions


def _action_for_ref(
    result: WorkflowDraftValidationResult,
    action_ref: str | None,
) -> Mapping[str, Any] | None:
    if result.documents is None or action_ref is None:
        return None
    connector_id, separator, action_id = action_ref.partition(".")
    if not separator:
        return None
    return _connector_actions(result.documents.connectors).get((connector_id, action_id))


def _issue_step_id(issue: object) -> str | None:
    path = getattr(issue, "path", ())
    if not isinstance(path, tuple) or len(path) < 2 or path[0] != "steps":
        return None
    step_id = path[1]
    return step_id if isinstance(step_id, str) and step_id else None


def _fixture_decision_rules(result: WorkflowDraftValidationResult, issue: object, message: str) -> tuple[str, ...]:
    step_id = _decision_step_id_from_message(message)
    base_rules = [
        "Use top-level `decision:` in the fixture, not `decisions:`.",
        "Use branch keys as values, not target step ids.",
    ]
    if result.documents is None or step_id is None:
        return tuple(base_rules)

    branches = _workflow_decision_branches(result.documents.workflow, step_id)
    if not branches:
        return tuple(base_rules)

    branch_text = ", ".join(f"`{key}` -> `{target}`" for key, target in branches.items())
    first_key, first_target = next(iter(branches.items()))
    base_rules.extend(
        [
            f"Decision mapping key must be exactly `{step_id}`.",
            f"Decision step `{step_id}` branch map: {branch_text}.",
            f"Use `{step_id}: {first_key}`, not `{step_id}: {first_target}`.",
        ]
    )
    base_rules.extend(_current_fixture_decision_replacements(result, issue, step_id, branches))
    return tuple(base_rules)


def _decision_step_id_from_message(message: str) -> str | None:
    match = NO_BRANCH_MATCH_RE.search(message)
    if match is not None:
        return match.group("step")
    match = DECISION_STEP_RE.search(message)
    if match is not None:
        return match.group("step")
    return None


def _workflow_decision_branches(workflow: Mapping[str, Any], step_id: str) -> Mapping[str, str]:
    step = _workflow_steps_by_id(workflow).get(step_id)
    if step is None:
        return {}
    branches = step.get("branches")
    if not isinstance(branches, Mapping):
        return {}
    return {str(key): str(value) for key, value in branches.items() if isinstance(value, str)}


def _current_fixture_decision_replacements(
    result: WorkflowDraftValidationResult,
    issue: object,
    step_id: str,
    branches: Mapping[str, str],
) -> tuple[str, ...]:
    if result.documents is None:
        return ()

    fixture_name = _issue_fixture_name(issue)
    if fixture_name is None:
        return ()
    fixture = result.documents.dry_run_fixtures.get(fixture_name)
    if fixture is None:
        return ()

    decisions = _fixture_decisions(fixture)
    if not decisions:
        first_key = next(iter(branches))
        return (f"Add `decision: {{{step_id}: {first_key}}}` to {fixture_name}.",)

    target_to_branch = {target: key for key, target in branches.items()}
    known_decision_steps = _workflow_decision_step_ids(result.documents.workflow)
    rules: list[str] = []
    current_value = decisions.get(step_id)
    if current_value is not None:
        replacement_value = target_to_branch.get(current_value, current_value)
        if replacement_value != current_value:
            rules.append(f"Replace `{step_id}: {current_value}` with `{step_id}: {replacement_value}`.")
    else:
        for current_key, current_value in decisions.items():
            if current_key in known_decision_steps:
                continue
            replacement_value = target_to_branch.get(current_value, current_value)
            if replacement_value in branches:
                rules.append(f"Replace `{current_key}: {current_value}` with `{step_id}: {replacement_value}`.")
    if not rules:
        first_key = next(iter(branches))
        rules.append(f"Add `{step_id}: {first_key}` under decision: in {fixture_name}.")
    rules.append("Preserve existing decision entries for other decision steps.")
    return tuple(rules)


def _workflow_decision_step_ids(workflow: Mapping[str, Any]) -> set[str]:
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        return set()
    return {
        str(step["id"])
        for step in steps
        if isinstance(step, Mapping) and isinstance(step.get("id"), str) and step.get("type") == "decision"
    }


def _issue_fixture_name(issue: object) -> str | None:
    file = str(getattr(issue, "file", ""))
    prefix = "dry_run_fixtures/"
    if file.startswith(prefix):
        return file.removeprefix(prefix)
    location = str(getattr(issue, "location", ""))
    if location.startswith(prefix):
        return location.removeprefix(prefix).partition(":")[0]
    return None


def _fixture_decisions(fixture: Mapping[str, Any]) -> Mapping[str, str]:
    for key in ("decision", "decisions"):
        value = fixture.get(key)
        if isinstance(value, Mapping):
            return {str(decision_key): str(decision_value) for decision_key, decision_value in value.items()}
    nested = fixture.get("fixture")
    if isinstance(nested, Mapping):
        for key in ("decision", "decisions"):
            value = nested.get(key)
            if isinstance(value, Mapping):
                return {str(decision_key): str(decision_value) for decision_key, decision_value in value.items()}
    return {}


def _first_action_ref(message: str) -> str | None:
    match = ACTION_REF_RE.search(message)
    return match.group(0) if match is not None else None


def _design_only_action_ref(message: str) -> str | None:
    match = DESIGN_ONLY_ACTION_RE.search(message)
    if match is not None:
        return match.group("action")
    return _first_action_ref(message)


def _action_ref_from_issue_path(issue: object) -> str | None:
    path = getattr(issue, "path", ())
    if not isinstance(path, tuple) or len(path) < 4:
        return None
    if path[0] != "connectors" or path[2] != "actions":
        return None
    connector_id = path[1]
    action_id = path[3]
    if not isinstance(connector_id, str) or not isinstance(action_id, str):
        return None
    return f"{connector_id}.{action_id}"


def _quoted_value(message: str) -> str | None:
    match = QUOTED_VALUE_RE.search(message)
    return match.group("value") if match is not None else None
