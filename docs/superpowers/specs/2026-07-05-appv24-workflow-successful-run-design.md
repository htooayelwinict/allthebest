# appv24 Workflow Successful Run Design

Date: 2026-07-05
Status: proposed design
Scope: appV2.4 / appv24 workflow runtime path from generated package to successful controlled run

## Summary

appv24 should become a natural-language workflow runtime, not only a workflow package generator. A successful run means a saved workflow package can move through every hard gate and produce an auditable run result:

```text
natural-language request
  -> workflow-designer-lite package draft
  -> validator and repair loop
  -> saved package
  -> strict IR
  -> policy gate
  -> connector capability check
  -> approval gate
  -> broker execution
  -> local or live outputs
  -> append-only ledger
  -> final run report
```

The next implementation target is a real **local successful run**. Local run is not another preview. It executes the workflow graph and broker actions, but broker actions produce local artifacts only. No Telegram, Gmail, cloud, MCP, browser, shell, or network call is made in local mode.

This creates the bridge from workflow design to workflow enablement without violating the kernel boundary.

## Current Baseline

The current appv24 workflow layer already contains:

```text
workflow draft extraction and save
schema validation
semantic validation
repair directives
prompt fragments and examples
workflow staging
package loader
strict IR compiler
policy checker
capability/catalog checks
deterministic simulator
local dry-run broker
run-preview report
run store
local artifact providers
approval gate contracts
credential preflight contracts
idempotency store
IaC/export intent
disabled live broker
```

Recent verification proved:

```text
TUI can generate and save a workflow package.
Validator catches runtime dead-end action steps.
Dry-run preview can traverse happy and rejection fixtures.
The full appV2.4 pytest suite passes.
```

The missing product layer is an appv24-owned runner that turns these pieces into a completed run record and local artifacts.

## Zone Contract

This work must respect `rules.md`.

### Red Zone

Do not touch:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

The workflow runtime must sit beside the coding agent and must not change agent loop semantics, provider streaming, context compaction, or session storage.

### Yellow Zone

Only touch after the green-zone runner API is proven:

```text
appV2.4/appv24/tui/
appV2.4/appv24/cli.py
appV2.4/appv24/profiles/
packages/appv24-cli/
```

Allowed yellow-zone change for this feature family:

```text
thin command bridge:
  /workflow-run <package> <fixture> --mode local --approve-all
  /workflow-preview <package> <fixture>
```

No broad TUI refactor.

### Green Zone

Primary implementation area:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_*.py
docs/superpowers/
docs/appv24/
```

## External Reference Insights

Context7 was used for current Temporal and n8n documentation. OpenBridge was used for architecture pressure-testing.

### Temporal-Derived Design Implications

Temporal documentation reinforces these runtime principles:

```text
workflow orchestration should stay deterministic
external side effects belong in activity-like boundaries
activity retries require idempotency keys
event history is the source of truth for what happened
approval/resume needs stable run identity and state
```

For appv24 this means:

```text
The runner step loop should be deterministic.
Connectors must run only through broker adapters.
Every mutating connector action needs idempotency material before live modes.
The ledger must record decisions and broker results before claiming success.
Retries and resume are later features, but their data contracts must start now.
```

### n8n-Derived Design Implications

n8n documentation reinforces these automation concepts:

```text
a workflow is a graph of nodes
triggers start executions
credentials are separate from workflow definitions
an execution is one workflow run
manual/testing execution and production execution are separate modes
```

For appv24 this means:

```text
Workflow packages must not contain raw credentials.
Triggers are package inputs, not connector actions.
Each run gets a durable run id.
Local/manual execution and production execution must remain separate modes.
```

### OpenBridge Pressure-Test Result

OpenBridge agreed with the staged ladder and broker-enforced connector boundary, but recommended more production machinery than appv24 should adopt now. appv24 should borrow the durable execution concepts while keeping the first runner simple, offline, and green-zone.

## Run Ladder

The appv24 workflow runtime should have explicit levels. A later level cannot bypass the gates of earlier levels.

### Level 0: Draft And Repair

Purpose:

```text
LLM produces a workflow package draft and the repair loop converges it toward schema and semantic validity.
```

Output:

```text
workflow.yaml
connectors.yaml
permissions.yaml
dry_run_fixtures/*.yaml
rollback.md
```

Hard gates:

```text
no raw secrets
known connector actions only
mutating actions require approval metadata
non-terminal action/manual steps require next
decision branches must target known steps
fixtures must model happy and rejection paths
```

### Level 1: Static Quality Gate

Purpose:

```text
Validate, compile, and policy-check the package before any execution.
```

Pipeline:

```text
load_package_documents
  -> validate_workflow_documents
  -> compile_workflow_documents
  -> evaluate_policy
```

Output:

```text
validation report
compile report
policy report
package fingerprint
```

Hard gates:

```text
validation_ok
compile_ok
policy_ok
package fingerprint locked for the run
```

### Level 2: Run Preview

Purpose:

```text
Show what would happen without creating run artifacts beyond explicit preview persistence.
```

Output:

```text
step trace
terminal step
brokered action previews
approvals required
idempotency fields
allowed outputs
ledger preview events
issues
```

Hard gates:

```text
no credentials
no external calls
no local artifacts unless preview is explicitly persisted
unknown broker action blocks
```

### Level 3: Local Actual Run

Purpose:

```text
Execute the workflow graph through the broker and create local artifacts only.
```

Command shape:

```text
/workflow-run telegram-support happy_path.yaml --mode local --approve-all
```

Python API shape:

```python
report = run_workflow(
    WorkflowRunRequest(
        package_path=package,
        fixture_name="happy_path.yaml",
        mode=WorkflowRunMode.LOCAL,
        run_root=Path(".workflow-runs"),
        approve_all=True,
    )
)
```

Output:

```text
.workflow-runs/run-<id>/
  run.json
  report.json
  trace.json
  events.jsonl
  approvals.json
  artifacts/
    local-ticket-create_ticket_draft.json
    local-telegram-notify_admin.json
```

Hard gates:

```text
static quality gate passed
fixture exists
broker provider exists for each executed action
approval policy satisfied
all writes stay inside run-owned directory
run store rejects raw secret-looking events
final report status is passed only when a terminal step is reached
```

### Level 4: Staged Live Run

Purpose:

```text
Run against non-production transports with fake or staging credentials.
```

Hard gates:

```text
same package fingerprint passed local run
connector action allowlisted for staged
credential refs resolve to staging scope
mutating actions require explicit approval
idempotency key exists for mutating action
live-production remains disabled
```

This level is not the immediate implementation target. Its contracts should be shaped now so local run can evolve into it.

### Level 5: Live Read

Purpose:

```text
Allow production read-only connector actions.
```

Examples:

```text
github.list_dependabot_alerts
gmail.search_messages
calendar.list_events
```

Hard gates:

```text
action is read-only in built-in catalog
connector allowlist includes action
credential refs resolve
requested scopes are read-only
no mutating step executes
outputs are filtered to allowed fields
```

### Level 6: Live Draft

Purpose:

```text
Allow draft-safe side effects that do not publish/send/delete/provision.
```

Examples:

```text
gmail.create_draft
ticket.create_draft
github.create_draft_issue if represented by a draft-safe connector
```

Hard gates:

```text
local run passed for same package fingerprint
credential refs resolve
explicit approval granted
idempotency key material resolved
rollback instructions present
connector action allowlisted for live-draft
```

### Level 7: Live Production

Purpose:

```text
Allow real external mutations such as send, publish, update, delete, provision, or deploy.
```

Status:

```text
disabled by default
not part of the next implementation
```

Hard gates:

```text
live-production explicitly enabled outside the workflow package
connector action allowlisted for production
credential refs resolve with exact scopes
approval granted for the current package fingerprint
idempotency enforced for retries
rollback/compensation instructions visible before run
ledger is append-only
partial failure preserves all side-effect evidence
```

### Level 8: Cloud Deployed Run

Purpose:

```text
Run workflow packages from a hosted worker with webhook ingress, scheduler, credentials, approval UI, and durable run storage.
```

This requires:

```text
deployment intent export
secret binding
webhook ingress
scheduler
hosted runner process
run ledger database
approval UI/API
kill switch
versioned package bundle
```

This is a later product track, not the local-run implementation.

## Connector Picture

Connectors are capability contracts, not free-form tool calls.

### Connector Contract Layers

```text
workflow.yaml
  step uses: telegram.send_message

connectors.yaml
  declares connector, action, scopes, side effect, outputs, idempotency

permissions.yaml
  grants connector scopes and approval requirements

built-in capability catalog
  appv24 recognizes whether the action exists and which modes can use it

policy
  checks side effects, approvals, scopes, dry-run support, unknown action blocks

broker
  actually executes, simulates, or blocks the action
```

### Connector Capability Shape

```python
@dataclass(frozen=True)
class ConnectorCapability:
    connector: str
    action: str
    dotted: str
    modes: tuple[WorkflowRunMode, ...]
    side_effect: bool
    side_effect_class: str | None
    approval_required: bool
    required_scopes: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    idempotency_key_fields: tuple[str, ...]
```

### Connector Classes

```text
local artifact connector
  writes files under run artifacts directory only

direct API connector
  calls provider API directly in live modes only

MCP connector
  delegates to a configured MCP server in live modes only

webhook trigger
  inbound event source, not a connector action

IaC/export connector
  produces deployment intent, not live provisioning
```

### First Built-In Local Connectors

Hard-enable for local run:

```text
ticket.create_draft
gmail.create_draft
telegram.send_message
```

Possible later local connectors:

```text
file.write_artifact
http.request_preview
webhook.emit_local
github.create_issue_preview
slack.send_message_preview
```

### Broker Rule

The broker is the hard boundary.

Prompt instruction:

```text
Do not call external APIs.
```

Runtime enforcement:

```text
broker receives action request
broker checks mode
broker checks action allowlist
broker checks capability catalog
broker checks approvals
broker checks credentials for live modes
broker filters outputs
broker blocks or executes
```

No connector action is successful because the model asked for it. It is successful only because the runtime broker allowed it.

## Successful Local Run Data Flow

```text
WorkflowRunRequest
  -> load package
  -> fingerprint package
  -> validate documents
  -> compile IR
  -> evaluate policy
  -> load fixture
  -> create run directory
  -> create local artifact broker
  -> execute deterministic step loop
  -> for each decision:
       select branch from fixture
       append step event
  -> for each action:
       check approval
       append approval event
       call broker provider
       write local artifact
       append broker event
       append outputs event
  -> for terminal:
       append terminal event
       write trace.json
       write report.json
       return WorkflowRunReport(status="passed")
```

## Approval Model

Approval must be hard runtime state, not prompt text.

### Local Mode

Local mode supports:

```text
approve_all=False
  mutating action blocks at approval_required

approve_all=True
  approval events are recorded as local_auto_approved
  broker action may run
```

This makes tests deterministic while keeping the approval gate visible.

### Live Modes

Live modes do not auto-approve. They require:

```text
approval request id
run id
package fingerprint
step id
connector action
side effect class
allowed outputs
idempotency key fields
rollback summary
decision granted or denied
```

Stale approval blocks if the package fingerprint changes.

## Run Record Layout

Default local run root:

```text
.workflow-runs/
  run-<uuid>/
    run.json
    report.json
    trace.json
    approvals.json
    events.jsonl
    artifacts/
      local-ticket-create_ticket_draft.json
      local-telegram-notify_admin.json
```

`run.json`:

```json
{
  "run_id": "run-...",
  "workflow_name": "telegram_support_ticket",
  "package_fingerprint": "sha256:..."
}
```

`events.jsonl`:

```json
{"event_type":"run_created","status":"running"}
{"event_type":"step_started","step_id":"check_authorized_customer","status":"running"}
{"event_type":"step_completed","step_id":"check_authorized_customer","status":"completed"}
{"event_type":"approval_requested","step_id":"create_ticket_draft","status":"pending"}
{"event_type":"approval_granted","step_id":"create_ticket_draft","status":"approved"}
{"event_type":"broker_action_started","step_id":"create_ticket_draft","status":"running"}
{"event_type":"broker_action_completed","step_id":"create_ticket_draft","status":"simulated"}
{"event_type":"run_completed","status":"passed"}
```

`trace.json`:

```json
{
  "trace": [
    {"step_id": "check_authorized_customer", "status": "completed"},
    {"step_id": "create_ticket_draft", "status": "completed"},
    {"step_id": "notify_admin", "status": "completed"},
    {"step_id": "done", "status": "terminal"}
  ]
}
```

`report.json`:

```json
{
  "run_id": "run-...",
  "workflow_name": "telegram_support_ticket",
  "mode": "local",
  "status": "passed",
  "terminal_step": "done",
  "artifact_count": 2
}
```

## Failure Model

### Validation Failure

Handling:

```text
No run directory is created.
WorkflowRunReport(status="blocked", validation_ok=False)
```

### Compile Or Policy Failure

Handling:

```text
No broker action executes.
Run may be recorded as blocked if caller requested persisted failure records.
Issues explain exact compile/policy blocker.
```

### Missing Approval

Handling:

```text
run created
approval_requested event appended
run status blocked
broker action does not start
no artifact for blocked step
```

### Broker Provider Missing

Handling:

```text
broker_action_started event may be recorded
broker_action_blocked event appended
run status blocked
issues include missing local provider
```

### Broker Provider Failure

Handling:

```text
broker_action_failed event appended
run status failed or blocked
trace preserves completed steps
no success report
```

### Terminal Not Reached

Handling:

```text
run status blocked
issues include workflow stopped without terminal step
trace written for diagnosis
```

### Raw Secret Leakage

Handling:

```text
validation blocks raw secrets in package
WorkflowRunStore rejects secret-looking event details
tests verify raw token patterns cannot be persisted
```

### Partial Side Effect

Local mode:

```text
local artifacts remain as evidence
report status blocked or failed
rollback.md is surfaced as operator guidance
automatic rollback is not performed
```

Live mode later:

```text
ledger records every completed external side effect
idempotency records protect retry
compensation/rollback is explicit and separate
```

## Test Strategy

### Unit Tests

```text
test_workflow_runner.py
  local happy path writes run files and artifacts
  local rejection path writes no artifacts
  missing approval blocks before broker
  missing provider blocks with issue
  broker failure records event and blocks
  terminal step produces passed report

test_workflow_local_artifacts.py
  ticket artifact provider writes under artifact root
  gmail artifact provider writes under artifact root
  telegram artifact provider writes under artifact root
  output filtering works

test_workflow_run_store.py
  run store persists report and trace
  secret-looking event details are rejected
  run directories cannot escape root

test_workflow_live_broker.py
  live-production blocks by default
  live-read blocks mutating actions
  live-draft blocks send/publish/delete/provision actions
```

### Integration Tests

```text
workflow package saved from fixture
  -> run_workflow(mode=LOCAL, approve_all=True)
  -> report.status == "passed"
  -> terminal_step == "done"
  -> artifact_count == expected

workflow rejection fixture
  -> run_workflow(mode=LOCAL, approve_all=True)
  -> report.status == "passed"
  -> terminal_step == "rejected"
  -> artifact_count == 0
```

### TUI Manual QA After Green API

```text
start appv24 --profile workflow-designer-lite --tui
ask for Telegram support workflow
/workflow-save telegram-support
/workflow-run telegram-support happy_path.yaml --mode local --approve-all
verify status passed and run directory printed
/workflow-run telegram-support rejection_path.yaml --mode local --approve-all
verify no artifacts created
```

## Implementation Phases

### Phase 1: Runner API

Build green-zone API:

```text
appV2.4/appv24/workflows/runner.py
```

Public entry:

```python
def run_workflow(
    request: WorkflowRunRequest,
    *,
    broker: LocalWorkflowBroker | None = None,
    approve_all: bool = False,
    max_transitions: int = 200,
) -> WorkflowRunReport:
    ...
```

### Phase 2: Local Artifact Providers

Extend:

```text
LocalArtifactProviders.telegram_send_message
LocalArtifactProviders.provider_map()
```

Provider map:

```python
{
    "ticket.create_draft": providers.ticket_create_draft,
    "gmail.create_draft": providers.gmail_create_draft,
    "telegram.send_message": providers.telegram_send_message,
}
```

### Phase 3: Run Store Persistence

Extend `WorkflowRunStore`:

```text
write_report(run_id, report)
write_trace(run_id, trace)
write_approvals(run_id, approvals)
artifact_root(run_id)
```

Keep append-only `events.jsonl`.

### Phase 4: Approval Handling

Local:

```text
approve_all=False blocks mutating approved steps
approve_all=True appends approval_granted and continues
```

Live:

```text
same contracts, no auto approve
resume later
```

### Phase 5: TUI Command Bridge

Only after runner API passes tests:

```text
/workflow-run <saved-package> <fixture> --mode local --approve-all
```

This should call the green-zone API and render a concise report.

### Phase 6: Staged Live Design Follow-Up

After local run:

```text
strict live-read/live-draft action mode matrix
credential resolver binding
idempotency resume
manual approvals
```

## Rollback

Rollback is simple for the next implementation:

```text
remove runner.py
remove new runner tests
remove local telegram provider if added
remove TUI command bridge if implemented
```

Because no red-zone files are touched, rollback cannot destabilize agent loop, provider streaming, compaction, or session store.

## Completion Criteria

The local successful run phase is complete when:

```text
run_workflow(mode=LOCAL, approve_all=True) passes happy path
run_workflow(mode=LOCAL, approve_all=True) passes rejection path
missing approval blocks before broker
local artifacts are written only under run artifacts directory
events.jsonl/report.json/trace.json are written
live-production remains blocked by default
workflow/profile/full appV2.4 tests pass
no red-zone files changed
yellow-zone untouched unless explicit TUI command phase is selected
```
