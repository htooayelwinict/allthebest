# appv24 Workflow Debug, Repair, and Connector Pack Design

Date: 2026-07-06
Scope: appV2.4/appv24 only
Status: design and plan input

## Goal

Design the next appv24 workflow-platform layer across three options:

- A: workflow run explainability and debugger
- C: repair cost optimizer and scoped repair reporting
- B: connector pack v0 for read-only and draft-only workflows

The design must respect `rules.md`: do not touch red-zone/kernel files, keep changes mostly in green-zone workflow modules, and treat yellow-zone CLI/TUI wiring as narrow and test-backed.

## Current Evidence

Repo state already includes:

- workflow package validation
- workflow IR compilation
- policy evaluation
- local runner and local artifact broker
- live-read runner with credential preflight and connector allowlist
- typed LLM nodes through compact output contracts
- run store artifacts: `run.json`, `events.jsonl`, `trace.json`, `approvals.json`, `report.json`
- workflow draft repair loop
- TUI commands: `/workflow-save`, `/workflow-run-local`, and planned local `/workflow-run`

Recent Mimo TUI evidence:

- model `xiaomi/mimo-v2.5-pro`
- support-ticket-triage package passed after repair attempt 3
- saved package validation returned `ok=True`
- local run passed with terminal `done_escalated`
- brokered simulated actions: `source.fetch_document`, `llm.classify`, `policy.check`, `notification.create_draft`
- Mimo repair was functional but verbose, regenerating large package text instead of a tight patch

MCP-backed research:

- Open Bridge recommended hardening repair before connectors and warned about secret leakage, run-state drift, malformed patches, and connector side effects.
- Context7 Telegram Bot API docs confirmed `sendMessage` sends messages and must be treated as mutating. Receiving webhook/update payloads should be modeled as input/read data, not as a send action.
- Context7 Gmail API docs confirmed `drafts.create` creates a Gmail draft and `drafts.send` sends it; draft creation is still a mailbox mutation, while sending is a stronger external communication mutation.

## Recommended Build Order

Build A, then C, then B.

Reasoning:

- A gives the platform the ability to explain validation, repair, and run behavior from artifacts already produced today.
- C becomes easier and safer after A, because repair-cost work can be measured and explained rather than guessed.
- B introduces external API vocabulary and higher safety risk. It should wait until explainability and repair reports make connector behavior auditable.

MCP suggested C first. That is reasonable for cost control, but local product evidence points to A first because appv24 already has enough run and validation data to expose useful explanations without changing the repair algorithm yet.

## Option A: Workflow Explainability and Debugger

### User Experience

Add explanation surfaces for saved packages, latest draft validation, repair attempts, and run artifacts.

Planned commands:

```text
/workflow-explain <package>
/workflow-run-explain <package> [run-id]
/workflow-repair-report
```

Example output:

```text
Package: support-ticket-triage
Validation: passed
Compile: passed
Policy: passed

Run path:
load_tickets -> classify_tickets -> policy_check -> escalation_decision -> draft_escalation -> done_escalated

Brokered actions:
- load_tickets: source.fetch_document -> simulated
- classify_tickets: llm.classify -> simulated
- policy_check: policy.check -> simulated
- draft_escalation: notification.create_draft -> simulated

Approvals required: 0
Terminal: done_escalated
```

### Architecture

Create a new green-zone module:

```text
appV2.4/appv24/workflows/explain.py
```

Responsibilities:

- load package documents
- run validation, compile, and policy checks
- summarize capability/catalog matches
- read run artifacts through `WorkflowRunStore`
- format concise human-readable explanations
- never execute workflow steps
- never call providers, APIs, shell, MCP, or live brokers

Narrow TUI/CLI wiring can call formatter functions. The core explain logic stays in `workflows/explain.py`.

### Data Flow

```text
package path
  -> load_package_documents
  -> validate_workflow_documents
  -> compile_workflow_documents
  -> evaluate_policy
  -> WorkflowPackageExplanation
  -> format_workflow_package_explanation
```

```text
package path + run id
  -> WorkflowRunStore
  -> read run.json/report.json/trace.json/events.jsonl/approvals.json
  -> WorkflowRunExplanation
  -> format_workflow_run_explanation
```

### Error Handling

- Missing package path: return explain error, no exception traceback in TUI.
- Invalid package: show validation issues and stop before compile/policy.
- Missing run id: choose latest run if exactly one exists; otherwise ask user to specify run id through an error message listing available ids.
- Corrupt run artifact: report which artifact failed and continue with the remaining artifacts if possible.
- Raw secret-looking values: rely on existing run-store secret guard; explanation formatting must not print raw secret values.

### Tests

Must add tests for:

- valid package explanation summarizes validation, compile, policy, steps, connector actions, and approvals
- invalid package explanation lists validation issues without compiling
- run explanation reads local run artifacts and shows terminal path
- corrupt missing trace/report produces a controlled explanation error
- output contains no raw secret-looking strings
- TUI command renders explanation without running the workflow

## Option C: Repair Cost Optimizer

### User Experience

Keep the validator-driven repair loop, but add visibility and lower-cost repair prompts.

Planned surfaces:

```text
/workflow-repair-report
```

Repair report should show:

```text
Attempt 1: failed
- unknown_catalog_action workflow.yaml steps[2].uses
- missing_fixture_decision dry_run_fixtures/happy_path.yaml

Attempt 2: repeated same failure pattern
Action: strict scoped repair prompt used

Attempt 3: passed
```

### Architecture

Create two green-zone modules:

```text
appV2.4/appv24/workflows/repair_report.py
appV2.4/appv24/workflows/repair_scope.py
```

Responsibilities:

- convert validation issues into stable fingerprints
- detect repeated failure patterns
- summarize changed issue sets between attempts
- generate scoped repair payloads based on issue location and directive
- record repair attempt summaries in memory during a TUI/CLI turn

This design does not require diff-based patch application yet. The validator still consumes complete drafts. The optimizer reduces prompt size and increases specificity; it does not introduce a fragile patch interpreter.

### Data Flow

```text
validation result
  -> issue fingerprint
  -> failed edit scope
  -> repair directives
  -> scoped repair prompt
  -> next assistant draft
  -> validation result
```

```text
attempt summaries
  -> repair report formatter
  -> TUI/CLI status output
```

### Error Handling

- If scoped repair prompt omits necessary context, fall back to the existing complete-package repair prompt.
- If the same issue fingerprint repeats more than the configured repeated-result threshold, add a stronger "same failure pattern" section.
- If repair exceeds configured attempts or seconds, stop with a compact report instead of silently looping.
- Do not change the infinite-loop product direction without documenting the safety bound. In implementation, any practical bound must be explicit in docs and tests.

### Tests

Must add tests for:

- issue fingerprint stability
- repeated issue pattern detection
- scoped prompt includes only failed files/paths plus required schema reminders
- fallback to full repair prompt when scope cannot be derived
- repair report lists attempts and convergence reason
- Mimo-style large full-regeneration remains accepted if final validation passes

## Option B: Connector Pack V0

### User Experience

Add a conservative built-in connector vocabulary for common workflows without enabling live production sends.

Connector groups:

```text
source.*
gmail.*
telegram.*
ticket.*
notification.*
llm.*
policy.*
```

V0 actions:

```text
source.fetch_items
source.fetch_document
gmail.create_draft
telegram.create_message_draft
ticket.create_draft
notification.create_draft
llm.extract_structured
llm.classify
llm.summarize
policy.check
```

Explicitly blocked or future-only:

```text
gmail.send
gmail.drafts.send
telegram.send_message live execution
ticket.create live execution
cloud.provision
shell.run
browser.act
```

The existing `telegram.send_message` catalog entry should be treated carefully. For V0, prefer `telegram.create_message_draft` for new generated packages. If `telegram.send_message` remains for backward tests, it must stay local-simulated or approval-gated and not live-production enabled.

### Architecture

Create a connector catalog model without adding real network clients:

```text
appV2.4/appv24/workflows/connector_catalog.py
appV2.4/appv24/workflows/connector_packs.py
```

Responsibilities:

- typed connector action metadata
- action side-effect classification
- required scopes
- allowed outputs
- live-read eligibility
- live-draft eligibility
- live-production disabled flag
- provider notes for docs and explanations

Keep `catalog.py` as the simple public built-in action set or make it delegate to the new catalog model after tests prove compatibility.

### Data Flow

```text
connector pack definitions
  -> built-in action set
  -> validator unknown action checks
  -> policy side-effect checks
  -> explain/debug output
  -> live-read allowlist checks
```

### Error Handling

- Unknown connector action remains a hard validation or policy failure.
- Design-only actions may appear in docs but must not be executable workflow steps.
- Mutating actions require approval, idempotency, output allowlist, rollback, and live mode support before they can execute.
- Raw credentials must never be embedded in packages or run artifacts.
- Connector docs must distinguish draft creation from sending.

### Tests

Must add tests for:

- built-in connector catalog includes expected V0 actions
- unknown read-only-looking action is blocked
- Gmail draft action is classified as draft/mutating local-only unless explicitly live-draft supported
- Telegram send action is not live-read eligible
- `telegram.create_message_draft` is local/draft-safe
- prompt fragments tell models to prefer draft actions over send actions
- validator rejects executable design-only actions

## Zone Impact

Green-zone expected:

- `appV2.4/appv24/workflows/explain.py`
- `appV2.4/appv24/workflows/repair_report.py`
- `appV2.4/appv24/workflows/repair_scope.py`
- `appV2.4/appv24/workflows/connector_catalog.py`
- `appV2.4/appv24/workflows/connector_packs.py`
- `appV2.4/tests/test_workflow_explain.py`
- `appV2.4/tests/test_workflow_repair_report.py`
- `appV2.4/tests/test_workflow_connector_catalog.py`
- docs under `docs/appv24/`

Yellow-zone possible, narrow and test-backed:

- `appV2.4/appv24/tui/interactive_mode.py`
- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/workflows/catalog.py`
- `appV2.4/appv24/workflows/prompt_fragments.py`
- `appV2.4/tests/test_tui.py`
- `appV2.4/tests/test_cli.py`

Red-zone prohibited:

- appv24 agent loop, provider stream, app kernel, compaction, and session-store files.

## Acceptance Criteria

- A package explanation can run without executing workflow steps.
- A run explanation can summarize a saved `.workflow-runs/run-*` directory.
- Repair reports expose attempt count, issue fingerprints, repeated failures, and convergence.
- Connector catalog v0 blocks invented actions and prefers draft-safe actions.
- Full pytest suite passes.
- Live production remains disabled.
- No red-zone file is modified.
