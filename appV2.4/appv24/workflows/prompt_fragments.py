"""Shared prompt fragments for appv24 workflow package authoring."""

from __future__ import annotations


REQUIRED_PACKAGE_BLOCK_LABELS = (
    "```yaml workflow.yaml",
    "```yaml connectors.yaml",
    "```yaml permissions.yaml",
    "```yaml dry_run_fixtures/happy_path.yaml",
    "```yaml dry_run_fixtures/rejection_path.yaml",
    "```markdown rollback.md",
)


def render_required_package_blocks_fragment() -> str:
    lines = ["Use these exact labeled fenced sections:"]
    lines.extend(f"- {label}" for label in REQUIRED_PACKAGE_BLOCK_LABELS)
    return "\n".join(lines)


def render_passive_safety_fragment() -> str:
    return "\n".join(
        [
            "- Do not call tools.",
            "- Do not call external APIs.",
            "- Do not run shell commands.",
            "- Do not write files.",
            "- Do not request raw secrets.",
            "- Do not execute, publish, provision, send, or mutate anything.",
        ]
    )


def render_schema_reminders_fragment() -> str:
    return "\n".join(
        [
            "- workflow.yaml version must be appv24.workflow/v1.",
            "- workflow.yaml must include design metadata with intent, trigger, inputs, outputs, assumptions, side_effects, approval_gates, failure_modes, and rollback.",
            "- workflow.yaml triggers must be a list.",
            "- Cron-like triggers must use type: schedule and schedule: <cron>.",
            "- workflow.yaml steps must use approval: <approval-id> for mutating side effects.",
            "- connectors.yaml version must be appv24.connectors/v1.",
            "- connectors.yaml connectors must be a list.",
            "- connectors.yaml actions must be objects with id and side_effect boolean.",
            "- permissions.yaml version must be appv24.permissions/v1.",
            "- permissions.yaml permissions must be a list.",
            "- permissions.yaml permissions[].actions must be connector action id strings.",
            "- permissions.yaml approvals[].required_for must be workflow step id strings.",
        ]
    )


def render_side_effect_contract_fragment() -> str:
    return "\n".join(
        [
            "- Read, classify, analyze, summarize, draft, format, and local artifact steps must use side_effect: false unless they mutate external systems.",
            "- Only external create, update, delete, send, publish, or provision actions should be mutating.",
            "- Mutating connector actions must include side_effect_class, execution_mode, approval_required, and dry_run_supported.",
            "- Brokered mutating actions require approval_required: true and dry_run_supported: true.",
            "- Brokered create/send/publish/provision actions require idempotency.key_fields.",
            "- Brokered mutating actions require outputs.allowed with explicit field names and no wildcard.",
            "- Design-only actions must use execution_mode: design_only and must not be called by workflow steps.",
            "- Draft-only outputs must be represented as local artifact drafts, not connector send/publish calls.",
            "- Notification, email, chat, message, or report drafts stored locally are local artifact drafts with side_effect: false.",
            "- Every mutating step must have an explicit approval gate.",
            "- The approval id in steps[].approval must exist in permissions.yaml approvals[].id.",
            "- approvals[].required_for must include the exact mutating workflow step id.",
        ]
    )


def render_secret_reference_fragment() -> str:
    return "\n".join(
        [
            "- Secret examples must use reference names such as secretRef: CONNECTOR_CREDENTIAL_REF, checksum fields, redacted: true, or non-secret references.",
            '- Prefer normal input keys with scalar secret reference values, for example github_token: "secretRef: GITHUB_TOKEN_REF".',
            "- Do not invent keys like github_token_secretRef: GITHUB_TOKEN.",
            "- secretRef values must be reference names only, never raw tokens, passwords, keys, or credentials.",
        ]
    )


def render_rollback_safety_fragment() -> str:
    return "\n".join(
        [
            "- rollback.md must be manual, non-executable rollback guidance.",
            "- Do not include API, CLI, shell, curl, Terraform, kubectl, cloud CLI, or token examples in rollback.md.",
            "- rollback.md must not include ${secrets.*}, Authorization headers, HTTP method examples, or copy-paste commands.",
        ]
    )


def render_workflow_passive_boundary_fragment() -> str:
    return "- appv24 only designs workflow packages; it does not execute workflows."
