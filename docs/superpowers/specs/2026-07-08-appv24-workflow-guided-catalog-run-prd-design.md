# appv24 Workflow Guided Catalog Run PRD Design

Date: 2026-07-08
Status: approved direction for MVP planning
Scope: appV2.4 / appv24 workflow profile

## Summary

appv24 workflow profile needs a user-facing guided run for catalog-backed
workflows. The user should first see the available workflow capabilities, then
enter a natural-language workflow request. The app should generate a package
using only cataloged connector actions and automatically move the package
through validation, live-read, local resume, readiness, and live-production
after the user approves the exact external side effect.

The MVP must keep the existing safety model intact. "Automated by the app"
means the app orchestrates the existing gates. It does not mean automatic
approval, live side effects without approval, connector semantics inside the
workflow runtime, or runtime calls to the coding profile.

## Product Goals

- Make the workflow profile usable by an end user from the TUI.
- Show the user what workflow capabilities are available before they prompt.
- Generate workflows only from the connector contract catalog.
- Hide operator command choreography behind one guided session.
- Keep the user approval moment explicit and focused on the external side
  effect.
- Preserve existing live execution safety gates and audit artifacts.

## Non-Goals

This MVP does not include:

- Auto-approval of external sends or production mutations.
- Telegram `/start` subscriber persistence.
- Arbitrary browser scraping.
- Generic generated code nodes.
- Cross-profile coding-profile execution.
- Service deployment, cron, polling, webhook ingress, or cloud export.
- npm or GHCR release.
- Kernel/red-zone changes.

The parked cross-profile code-node idea remains separate. It can become a
future build-time authoring feature for missing deterministic nodes, but it must
not be mixed into this guided connector-run MVP.

## Current Product Gap

The current implementation can already run the AI-news Telegram scenario through
manual TUI slash commands:

```text
workflow package generation
-> package validation
-> live-read-to-approval
-> approval ledger
-> production readiness
-> live-production
```

That path works for an operator, but it asks the user to understand run IDs,
allowlists, approval-run IDs, readiness commands, and production commands. The
MVP should keep those commands for debugging while adding a guided user path
above them.

## User Experience

The user-facing flow is:

```text
1. User opens workflow profile in TUI.
2. App shows the workflow capability catalog.
3. User enters a natural-language workflow prompt.
4. App drafts a workflow package using catalog actions only.
5. App validates and repairs the package automatically.
6. App saves the package to a user-visible path.
7. App builds a live plan automatically.
8. App runs live-read automatically.
9. App resumes local steps automatically until an approval request exists.
10. App shows one approval screen for the external side effect.
11. User approves or denies.
12. If approved, app runs readiness automatically.
13. If readiness passes, app runs live-production automatically.
14. App shows the final report.
```

The only required user interruption after prompt submission is the approval
decision for the external side effect.

## Capability Catalog UX

The app should render a user-facing capability catalog derived from
`builtin_connector_contract_catalog()`. The catalog must not be hand-maintained
separately from the runtime contract source of truth.

The catalog view should group actions by user intent and risk:

```text
Read data
- Search web/news with Brave Search
- Fetch an allowed URL with HTTP fetch
- Fetch source documents/items
- List Telegram subscribers

Transform locally
- Summarize text/items
- Classify text
- Extract structured fields
- Check deterministic policy

Draft or send
- Create Gmail draft
- Create notification draft
- Send Telegram message to one allowed chat
- Broadcast Telegram message to registered subscribers
```

Each catalog item should show:

- Capability label.
- Action ref, such as `brave.search`.
- Inputs the user can mention.
- Outputs the workflow can use.
- Mode: local, live-read, live-draft, or live-production.
- Side-effect class.
- Approval requirement.
- Required environment variable names, never values.

The user can still type naturally. The catalog exists to set expectations and
keep the generator honest.

## Prompt-To-Package Rules

The workflow generator must:

- Use only built-in catalog actions or strict custom connector interfaces.
- Prefer `brave.search` for public web or news search.
- Use `telegram.list_subscribers -> telegram.broadcast_message` for registered
  Telegram delivery.
- Attach approval to the mutating connector step.
- Bind required connector inputs in `workflow.yaml` step `with` blocks.
- Include top-level `outputs.allowed` values from connector contracts.
- Reject or explain missing capabilities instead of inventing executable action
  names.

If a user asks for behavior outside the catalog, the app should produce a
missing-capability explanation. Later, this may become a structured
`code_node_requirement`, but this MVP should not execute generated custom code.

## Guided Execution Architecture

Use a thin orchestration layer above the existing phase runner:

```text
Workflow TUI
  -> capability catalog renderer
  -> package generation/repair/save
  -> guided execution session
       -> package validation
       -> live plan
       -> live-read-to-approval phase runner
       -> approval ledger command/view
       -> readiness check
       -> approved live-production phase runner
       -> final report
```

The guided execution session should call existing workflow APIs. It should not
duplicate connector transport logic, approval ledger logic, live readiness
checks, idempotency handling, or target policy enforcement.

The workflow runtime still owns orchestration only. Connector contracts and
adapters still own connector-specific interfaces, scopes, target policy,
transport behavior, and output shaping.

## MVP Components

### User Capability Catalog

Create a workflow capability view from the connector contract catalog. The view
should be safe to render in CLI/TUI and in prompt context. It should be tested
against catalog drift.

### Guided Run Report

Create a structured report for the guided session. It should record:

- Package path.
- Fixture name.
- Live plan status.
- Validation status.
- Live-read run ID.
- Local approval run ID.
- Approval request IDs.
- Approval decision status.
- Readiness status.
- Live-production run ID.
- Final status.
- Issues and next safe action.

### Guided Run Orchestrator

Create a green-zone workflow module that chains existing operations:

```text
plan
-> live-read-to-approval
-> wait for approval decision
-> live-production readiness
-> live-production
```

The first implementation can be split into two calls:

```text
prepare_to_approval(package, fixture)
continue_after_approval(package, fixture, approval_run_id)
```

This keeps the approval boundary explicit while removing manual run-id copying.

### TUI Guided Command

Expose the guided flow in the TUI with one user-oriented command. Existing
operator commands stay available.

Recommended command names:

```text
/workflow-catalog
/workflow-run-guided <path> [fixture]
/workflow-continue-guided <path> <approval-run-id>
```

If implementation can safely infer the latest pending approval from the package
run store, the continue command may omit `<approval-run-id>` in a later slice.

### CLI Parity

CLI parity can mirror the TUI commands for testability and automation, but the
product MVP is the TUI user flow.

## Approval Boundary

The app may automate every phase except the approval decision. The approval view
must show:

- Workflow name.
- Package fingerprint.
- Mutating connector action.
- Step ID.
- Recipient count or target summary.
- Message/body preview.
- Required scopes.
- Idempotency key material summary.
- Approve and deny commands/actions.

The app must not run live-production until the approval ledger records an
approved decision for the same package fingerprint and approval request.

## Error Handling

Errors should be user-actionable:

- Unknown action: show catalog action choices or missing-capability message.
- Invalid package: show the validation issue and repair status.
- Live-read blocked: show missing env names, scopes, or allowlist actions.
- Approval missing: show the exact approval command/action needed.
- Readiness blocked: show safe setup hints without secrets.
- Target policy blocked: name the connector policy and blocked target class.
- Empty subscriber list: block before live-production.
- Production failure: show sent/failed counts without raw provider secrets.

## Testing Strategy

Add tests before implementation:

- Capability catalog renders built-in actions from
  `builtin_connector_contract_catalog()`.
- Catalog display includes mode, approval, side-effect, inputs, outputs, and env
  names without env values.
- AI-news prompt guidance still maps to
  `brave.search -> telegram.list_subscribers -> llm.summarize -> telegram.broadcast_message`.
- Guided run prepare phase calls live-read-to-approval and returns approval
  request IDs.
- Guided run continue phase refuses live-production before approval.
- Guided run continue phase runs readiness before live-production.
- Guided run continue phase runs approved live-production after approval.
- TUI command renders catalog.
- TUI guided command renders a single report instead of next-command
  choreography.

## Zone Plan

Preferred green-zone files:

```text
appV2.4/appv24/workflows/capability_catalog.py
appV2.4/appv24/workflows/guided_run.py
appV2.4/tests/test_workflow_capability_catalog.py
appV2.4/tests/test_workflow_guided_run.py
docs/superpowers/specs/
docs/superpowers/plans/
```

Careful yellow-zone files only for thin command exposure:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
```

No red-zone files are required.

## Acceptance Criteria

The MVP is accepted when a human can run the TUI and complete this scenario:

```text
Create a workflow that searches latest AI news, summarizes it, lists registered
Telegram bot subscribers, and sends the digest from the Telegram bot to those
registered users after approval.
```

The app must:

- Show the user the available catalog capabilities.
- Generate a workflow package using only catalog actions.
- Save the package.
- Automatically validate the package.
- Automatically run live-read to approval.
- Show one explicit approval view for `telegram.broadcast_message`.
- Record the approval decision.
- Automatically check live-production readiness.
- Automatically run live-production after approval.
- Show final status and run IDs.
- Preserve all existing safety gates.

## PRD Decision

Build this as a guided workflow execution layer over the current connector and
phase-runner design. Do not weaken the live execution gates, do not move
connector semantics into workflow orchestration, and do not introduce
cross-profile custom code nodes in this MVP.
