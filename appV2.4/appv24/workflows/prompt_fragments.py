"""Shared prompt fragments for appv24 workflow package authoring."""

from __future__ import annotations

from appv24.workflows.catalog import BUILTIN_WORKFLOW_ACTIONS
from appv24.workflows.connector_contracts import builtin_connector_contract_catalog
from appv24.workflows.contracts import BUILTIN_LLM_OUTPUT_CONTRACTS


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
            "- workflow.yaml top-level shape: version: appv24.workflow/v1, name:, description:, design:, triggers:, entrypoint:, steps:.",
            "- workflow.yaml version must be appv24.workflow/v1.",
            "- workflow.yaml must include design metadata with intent, trigger, inputs, outputs, assumptions, side_effects, approval_gates, failure_modes, and rollback.",
            "- workflow.yaml triggers must be a list.",
            "- triggers must be a YAML list with items containing id and type.",
            "- Cron-like triggers must use type: schedule and schedule: <cron>.",
            "- For cron-like schedules, use type: schedule and put the cron expression in schedule:.",
            "- Never use type: cron.",
            "- entrypoint must name the first step id.",
            "- steps must be a YAML list with items containing id and either uses: connector.action or type: terminal, decision, manual_approval, or action.",
            "- Non-terminal action and manual approval steps must set next: <target-step>; terminal steps end the workflow.",
            "- Decision steps must use branches: with branch-name to target-step mappings.",
            "- Do not use condition plus next/reject_next as runtime branch syntax; reject_next is ignored by the runtime.",
            "- Every multi-branch decision step must have matching dry-run fixture decisions.",
            "- dry_run_fixtures/*.yaml must include decision: mappings for each multi-branch decision step.",
            "- Fixture decision values must be branch names from workflow.yaml steps[].branches.",
            "- expected_path is documentation only; it does not drive runtime simulation.",
            "- workflow.yaml steps must use approval: <approval-id> for mutating side effects.",
            "- Mutating workflow steps must use approval: <approval-id>; never use approval_gate:.",
            "- connectors.yaml top-level shape: version: appv24.connectors/v1, connectors:.",
            "- connectors.yaml version must be appv24.connectors/v1.",
            "- connectors.yaml connectors must be a list.",
            "- connectors must be a YAML list, not a mapping keyed by connector name.",
            "- connectors.yaml actions must be objects with id and side_effect boolean.",
            "- Each connector item needs id, adapter, and actions.",
            "- adapter must be one of mcp, openapi, asyncapi, webhook, direct_api, iac, llm, manual, or platform_export.",
            "- actions must be a YAML list of objects, not strings.",
            "- Each action item must be a YAML object with id:, plus optional operationId:, tool:, side_effect:, and required_scopes:.",
            "- side_effect must be boolean true or false.",
            "- permissions.yaml top-level shape: version: appv24.permissions/v1, permissions:, and optional approvals:.",
            "- permissions.yaml version must be appv24.permissions/v1.",
            "- permissions.yaml permissions must be a list.",
            "- approvals must be a YAML list of objects, not gate_id/connector/action entries.",
            "- Each approval item must be a YAML object with id:, mode: explicit, and required_for:.",
            "- required_for values must be workflow step id strings only, for example required_for: [create_issue].",
            "- Do not put connector/action objects inside required_for.",
            "- permissions must be a YAML list, not a mapping keyed by connector name.",
            "- Each permission item needs connector and actions.",
            "- permissions.yaml permissions[].actions must be connector action id strings.",
            "- permissions[].actions must be workflow connector action id strings.",
            "- permissions.yaml permissions[].scopes must grant every required_scopes value used by granted actions.",
            "- permissions[].scopes must include every required_scopes value for the granted connector actions; do not use '*' wildcard scopes.",
            "- For example, if github.create_issue requires issues and github.get_dependabot_alerts requires security_events, the github permission must include scopes: [issues, security_events].",
            "- permissions.yaml approvals[].required_for must be workflow step id strings.",
            "- dry_run_fixtures must use top-level decision: to choose decision branches.",
            "- dry_run_fixtures must not use decisions: for branch choices.",
            "- dry_run_fixtures decision values must be branch keys, not target step ids.",
            "- Example: if workflow.yaml decision step approve_path has branches {approved: create_ticket, rejected: done}, use decision: {approve_path: approved}, not decision: {approve_path: create_ticket}.",
        ]
    )


def render_capability_catalog_fragment() -> str:
    contract_catalog = builtin_connector_contract_catalog()
    lines = [
        "Built-in executable workflow capability catalog:",
        *[f"- {action}" for action in sorted(BUILTIN_WORKFLOW_ACTIONS)],
        "Compact action contracts:",
        *[f"- {contract_catalog[action].prompt_summary}" for action in sorted(contract_catalog)],
        "- workflow.yaml steps[].uses values must match one of these actions exactly.",
        "- For public web/news search, prefer brave.search over placeholder source.fetch_items URLs.",
        "- Prefer draft actions for user-visible messages: telegram.create_message_draft, gmail.create_draft, notification.create_draft.",
        "- Scalar connector inputs must stay scalar: telegram.send_message.chat_id, telegram.create_message_draft.chat_id, brave.search.query, and http.fetch.url cannot receive arrays, maps, subscriber lists, or broadcast lists.",
        "- Do not invent connector action names, even for read-only actions.",
        "- Do not invent receive_webhook, send_live, close_ticket, post_chat, or cloud mutation actions.",
        "- Incoming events, webhooks, and fixture inputs are triggers or input data, not executable connector action steps unless the action is listed above.",
        "- If no catalog action matches, model the need as trigger metadata, fixture input, a decision branch, or a local artifact design output.",
        "- For telegram registered-user workflows, use telegram.list_subscribers before telegram.broadcast_message.",
        "- Registered-user delivery must be modeled as connector interfaces, not workflow-local subscriber lookup or formatter actions.",
        "- Registered-user delivery must be modeled as connector interfaces: telegram.list_subscribers then telegram.broadcast_message.",
        "- Approval for registered-user telegram delivery belongs on telegram.broadcast_message, not on a separate manual approval step.",
        "- Only claim live execution for cataloged actions and configured target policies.",
        "- connectors.yaml outputs.allowed must name top-level provider outputs, not nested fields inside items.",
        "- For brave.search, use top-level outputs such as items, source_count, query, or artifact_path; put title, url, description, and age inside fixture item records or downstream input refs.",
        "- For source.fetch_items, use top-level outputs such as items, source_count, or artifact_path; put sender, subject, body, timestamp, url, and similar item fields inside dry-run fixture item records or downstream input refs.",
        "- LLM actions are typed transforms, not autonomous agents or tool-calling loops.",
        "- For llm.* steps, prefer compact with.output_contract over inline with.output_schema to avoid token bloat.",
        f"- Built-in LLM output_contract values: {', '.join(sorted(BUILTIN_LLM_OUTPUT_CONTRACTS))}.",
        "- LLM steps must set exactly one of with.output_contract or with.output_schema.",
        "- Do not use llm.do_anything, agent.run, http.request, finance.*, trading.*, or broker.place_order as executable actions.",
        "- policy.check steps must use deterministic with.checks; do not rely on LLM judgment as the policy gate.",
    ]
    return "\n".join(lines)


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
            "- If a step has side_effect_class create, update, delete, send, publish, or provision, add an explicit approval gate in permissions.yaml and reference it from that step with approval:.",
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
