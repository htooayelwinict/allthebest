# appv24 Workflow Run-Live Path Design

Date: 2026-07-05
Status: proposed design
Scope: appV2.4 / appv24 workflow execution path from dry-run preview to eventual live execution

## Summary

appv24 should not jump from valid workflow packages directly to live external automation. The correct next product shape is a staged workflow execution control plane:

```text
valid workflow package
  -> run-preview
  -> persisted run record
  -> local artifact execution
  -> approval checkpoint
  -> credential and scope preflight
  -> IaC/export intent
  -> live-read
  -> live-draft
  -> live-production
```

The first build target is still dry-run: it makes generated workflow packages feel usable by showing what would happen, which brokered actions would run, which approvals are required, which idempotency keys apply, and which outputs are allowed. Real API calls and real side effects remain blocked until the broker, policy, approval, credential, and ledger contracts are proven offline.

## Current Baseline

The current appv24 workflow layer has these pieces:

```text
workflow package loader
schema and semantic validator
draft repair loop
typed IR compiler
static policy evaluator
fixture simulator
simulation ledger
local dry-run broker
golden examples
workflow enablement report
run-preview API in working tree
```

The working-tree run-preview layer reports:

```text
step trace
terminal step
brokered actions
approvals required
idempotency key fields
allowed outputs
approval ledger preview events
simulated outputs
```

The next architecture must keep that direction and make it durable, auditable, and safe to extend.

## Zone Boundary

Run-live work must stay out of the kernel.

Red zone, do not touch:

```text
appV2.4/appv24/agent/
appV2.4/appv24/app.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
```

Yellow zone, only thin wrappers after the green APIs are stable:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/
appV2.4/appv24/profiles/
appV2.4/appv24/ai/providers/
packages/appv24-cli/
Dockerfile.appv24.release
.github/workflows/
```

Green zone for this feature family:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow*.py
docs/appv24/
docs/superpowers/
```

## External Design Facts

Context7 and Open Bridge were used for architecture references.

Temporal documentation supports these facts:

- Workflow orchestration logic must be deterministic for replay.
- External API calls, database queries, LLM calls, network I/O, and other non-deterministic operations belong outside workflow logic in activity-like boundaries.
- Retried external side effects need stable idempotency keys.

LangGraph documentation supports these facts:

- Human-in-loop approval should pause execution with a structured interrupt payload.
- Resume should carry an explicit approval/rejection value, optionally with edited arguments.
- A persistent checkpointer or equivalent durable store is required for long-running approval flows.

n8n documentation supports these facts:

- A workflow is a collection of nodes started by triggers.
- Credentials are separate from workflow definitions.
- An execution is a single run, with manual/testing and production modes separated.

Open Bridge advisory reinforced:

- Keep execution contracts in the green workflow layer.
- Gate live mutation behind policy, approval, credential, idempotency, and rollback checks.
- Treat stale approvals and package-hash drift as blockers.

## Approaches Considered

### A. Jump Straight To Live Connectors

This would wire Gmail, Telegram, GitHub, cloud, or MCP calls directly into the broker.

Pros:

- Fastest visible demo.
- Proves end-to-end external action quickly.

Cons:

- High risk of duplicate sends, credential leaks, bad approvals, and irreversible mutations.
- Forces yellow-zone UX and credential decisions before the core runner is proven.
- Violates the current product discipline by making live execution depend on unproven prompt output.

Decision: reject.

### B. Headless Workflow Execution Core First

This builds a deterministic run core under `appV2.4/appv24/workflows/` before exposing CLI/TUI controls.

Pros:

- Fully testable offline.
- Preserves red/yellow zone rules.
- Gives future CLI/TUI a small stable API.
- Supports dry-run, local-run, staged approval, live-read, live-draft, and live-production as modes of the same run contract.

Cons:

- Less flashy than live connector demos.
- Requires careful data contracts before product UX.

Decision: choose this path.

### C. Adopt Temporal Or LangGraph As The Runtime Now

This would delegate durability, replay, and human-in-loop to an existing workflow engine.

Pros:

- Proven concepts for durability, retries, and approval resume.
- Good long-term reference for cloud execution.

Cons:

- Adds a large dependency and deployment model before appv24 has stable workflow contracts.
- Risks turning appv24 into a wrapper around another runtime too early.
- Harder to keep tests offline and simple.

Decision: defer dependency adoption. Borrow the concepts: deterministic orchestration, activity-like brokers, checkpointer-like run store, and interrupt/resume approval envelopes.

## Execution Levels

### Level 0: Package Quality Gate

Input:

```text
workflow.yaml
connectors.yaml
permissions.yaml
dry_run_fixtures/*.yaml
rollback.md
```

Output:

```text
schema result
semantic result
compile result
policy result
```

Rules:

- No execution.
- No external calls.
- Raw secrets blocked.
- Mutating workflows require rollback and dry-run fixtures.

### Level 1: Run Preview

Input:

```text
saved package path or PackageDocuments
fixture name
optional dry-run broker
```

Output:

```text
WorkflowRunPreview
  workflow name
  fixture name
  status
  terminal step
  step trace
  brokered action previews
  approval previews
  ledger preview events
  issues
```

Rules:

- No writes except explicit report output when a caller requests it.
- No network.
- No credentials.
- Broker providers are dry-run only.
- Unknown brokered actions fail policy.

### Level 2: Persisted Run Preview

Input:

```text
saved package path
fixture name
run directory
```

Output:

```text
.appv24/workflow-runs/<run_id>/
  run.json
  package-fingerprint.json
  events.jsonl
  preview.json
```

Rules:

- Run records are append-only.
- Package fingerprint is based on package document contents.
- Preview cannot be resumed if the package hash changes.
- Raw secrets are never written to run records.

### Level 3: Local Artifact Execution

Input:

```text
WorkflowRunRequest(mode="local")
approved fixture
local artifact directory
```

Output:

```text
local artifact files
broker result events
simulated output references
```

Rules:

- Still no external APIs.
- Local providers can create files only under a run-owned artifact directory.
- Artifact paths are recorded in the ledger.
- Allowed outputs are still filtered.

### Level 4: Staged Approval

Input:

```text
run id
package fingerprint
approval envelope
```

Output:

```text
pending approval events
approved/rejected events
resume token
```

Rules:

- Mutating steps pause before broker execution.
- Approval payload includes step id, action, allowed outputs, idempotency fields, side effect class, credential refs, rollback summary, and package fingerprint.
- Approval is stale if package fingerprint changes.
- Denied approvals terminate the run safely.

### Level 5: Credential And Scope Preflight

Input:

```text
WorkflowRunRequest(mode="credential-preflight")
credential references
capability catalog
permissions.yaml scopes
```

Output:

```text
credential availability report
scope compatibility report
secret hygiene report
```

Rules:

- Workflow packages store credential references only.
- No raw secret values in package, ledger, preview, or approval payload.
- Wildcard scopes block live modes unless a future explicit policy exception exists.
- Missing credential refs block live modes.

### Level 6: IaC And Deployment Intent Export

Input:

```text
compiled IR
capability catalog
credential preflight report
approval policy
```

Output:

```text
WorkflowDeploymentIntent
  env requirements
  secret refs
  webhook ingress needs
  scheduler needs
  connector scopes
  approval policy
  rollback requirements
```

Rules:

- Export describes deployable intent.
- Export does not provision infrastructure.
- Export is allowed before live execution because it has no side effects.

### Level 7: Live Read

Input:

```text
WorkflowRunRequest(mode="live-read")
credential resolver
read-only connector allowlist
```

Output:

```text
read connector events
normalized read outputs
ledger entries
```

Rules:

- Only catalog-approved read-only actions run.
- Mutating actions remain simulated or blocked.
- Connector transports are injected so tests use fake transports.
- Live-read tests with real providers are manual-gated.

### Level 8: Live Draft

Input:

```text
WorkflowRunRequest(mode="live-draft")
credential resolver
draft-only connector allowlist
human approval events
idempotency store
```

Output:

```text
draft external artifacts
idempotency records
ledger entries
```

Rules:

- Only draft-safe mutating actions are allowed.
- Examples: Gmail draft, GitHub draft issue if represented by a draft-safe API, local ticket draft.
- Real send, publish, delete, provision, and broad update remain blocked.
- Approval, credential, idempotency, and rollback gates must pass before each draft action.

### Level 9: Live Production

Input:

```text
WorkflowRunRequest(mode="live-production")
production allowlist
credential resolver
approval ledger
idempotency store
rollback/compensation policy
```

Output:

```text
external side effects
immutable ledger
retry state
compensation state
```

Rules:

- This is the final stage, not the next build.
- Every mutating action requires explicit approval.
- Every retriable mutating action requires idempotency.
- Failed partial runs preserve ledger evidence and compensation instructions.
- Live production should remain opt-in per connector action.

## Core Data Contracts

### Run Mode

```python
class WorkflowRunMode(StrEnum):
    PREVIEW = "preview"
    LOCAL = "local"
    STAGED = "staged"
    CREDENTIAL_PREFLIGHT = "credential-preflight"
    LIVE_READ = "live-read"
    LIVE_DRAFT = "live-draft"
    LIVE_PRODUCTION = "live-production"
```

### Run Request

```python
@dataclass(frozen=True)
class WorkflowRunRequest:
    package_path: Path
    fixture_name: str | None
    mode: WorkflowRunMode
    run_root: Path | None = None
    approval_run_id: str | None = None
    connector_allowlist: tuple[str, ...] = ()
```

### Run Report

```python
@dataclass(frozen=True)
class WorkflowRunReport:
    run_id: str
    workflow_name: str
    mode: WorkflowRunMode
    status: str
    package_fingerprint: str
    terminal_step: str | None
    step_trace: tuple[StepTrace, ...]
    brokered_actions: tuple[WorkflowBrokeredActionPreview, ...]
    approvals: tuple[WorkflowApprovalPreview, ...]
    events: tuple[WorkflowRunEvent, ...]
    issues: tuple[str, ...]
```

### Run Event

```python
@dataclass(frozen=True)
class WorkflowRunEvent:
    event_id: str
    run_id: str
    event_type: str
    package_fingerprint: str
    step_id: str | None
    connector_action: str | None
    status: str
    details: Mapping[str, object]
```

## Hard Gates Before Any Live Mutation

All of these must pass before live-draft or live-production:

```text
package schema ok
semantic validation ok
compile ok
policy ok
capability catalog exact match
package fingerprint locked
no raw secrets
credential refs resolve
requested scopes match credential scopes
wildcard scopes absent
fixture coverage for every branch reaching mutation
approval envelope generated
approval granted for current package fingerprint
idempotency key material resolved
rollback.md maps to each mutating side effect class
connector action explicitly allowlisted for selected mode
dry-run or local run completed for same package fingerprint
```

## Failure Modes

### Invented Connector Action

Cause:

```text
LLM invents telegram.receive_webhook or another action not in the built-in capability catalog.
```

Handling:

```text
policy_unknown_catalog_action blocks the run before simulation or live mode
```

### Stale Approval

Cause:

```text
Package changed after approval preview was generated.
```

Handling:

```text
resume blocks because approval package_fingerprint != current package_fingerprint
```

### Duplicate External Mutation

Cause:

```text
Retry after network timeout or process crash.
```

Handling:

```text
idempotency store returns existing result for same idempotency key
conflicting payload for same key blocks the run
```

### Credential Leakage

Cause:

```text
Raw token appears in workflow package, preview, ledger, approval payload, or export.
```

Handling:

```text
validation blocks package
run store refuses event details with raw-secret patterns
tests assert redacted serialization
```

### Partial Failure

Cause:

```text
Step 1 mutates externally and step 2 fails.
```

Handling:

```text
ledger records completed side effects
run status becomes blocked or failed
compensation instructions are surfaced from rollback policy
automatic rollback remains disabled until separately designed
```

### Approval Timeout

Cause:

```text
Approval request is never granted or denied.
```

Handling:

```text
run remains pending until explicit expiry policy marks it expired
expired approval cannot resume
new package preview must be created
```

## Testing Strategy

Offline tests are required for all build phases.

```text
test_workflow_run_preview.py
  preview output shape
  fixture-specific traces
  missing fixture failure

test_workflow_run_store.py
  package fingerprint
  append-only JSONL events
  stale package blocks resume
  raw secret serialization refused

test_workflow_local_artifacts.py
  local providers write only under run artifacts directory
  path traversal blocked
  allowed outputs filtered

test_workflow_approval_gate.py
  pending approval event
  approval grant
  approval denial
  stale package rejection

test_workflow_credential_refs.py
  raw secret rejection
  missing credential ref blocks
  wildcard scopes block live modes

test_workflow_idempotency.py
  first mutation stores result
  retry returns prior result
  conflicting payload blocks

test_workflow_iac_export.py
  env requirements
  secret refs
  webhook ingress
  scheduler needs
  connector scopes
  approval policy

test_workflow_live_broker.py
  live-read allows read-only fake transport
  live-draft allows draft-only fake transport after approval
  live-production blocked by default
```

Real provider tests remain manual-gated and documented. They must never be required for normal CI.

## CLI And TUI Timing

Do not start by changing CLI or TUI.

First expose stable Python APIs:

```text
build_run_preview
persist_run_preview
run_local_workflow
create_approval_request
resume_approval
preflight_credentials
export_deployment_intent
run_live_read
run_live_draft
```

After those APIs have tests, yellow-zone wrappers can be thin:

```bash
appv24 workflow run-preview <package> --fixture happy_path.yaml
appv24 workflow run-local <package> --fixture happy_path.yaml
appv24 workflow export-intent <package>
```

TUI slash commands should come after CLI behavior and JSON report shape are stable.

## Rollout Phases

1. Land current dry-run run-preview and QA fixtures.
2. Add persisted run store and package fingerprint.
3. Add local artifact broker execution.
4. Add approval gate and resume semantics.
5. Add credential reference and scope preflight.
6. Add IaC/deployment intent export.
7. Add live-read fake-transport broker.
8. Add live-draft fake-transport broker.
9. Add manual-gated real-provider smoke tests.
10. Add live-production design review before any true send/publish/provision action.

## Direct Recommendation

Build run-preview and run-store next, not live connectors.

The first real proof should be:

```text
saved package + fixture
  -> deterministic preview report
  -> persisted run record
  -> immutable event ledger
  -> stale package approval blocked
```

That is the bridge from workflow design to workflow execution without risking external side effects.
