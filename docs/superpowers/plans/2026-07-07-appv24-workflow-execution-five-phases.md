# appv24 Workflow Execution Five Phases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Keep all implementation in appv24 workflow green-zone modules unless a command surface must expose already-built behavior.

**Goal:** Turn workflow-designer-lite into a safe workflow platform path: natural language package -> validated IR -> gated dry/local/live execution -> triggers -> deployment intent.

**Architecture:** Keep appv24 as a product/profile layer over the existing runtime. Workflow packages compile into strict IR, IR is checked against a connector contract catalog, execution moves through explicit gates, and live side effects require allowlists, approvals, credential references, idempotency, and audit artifacts.

**Tech Stack:** Python dataclasses, existing appv24 workflow modules, JSON/YAML package files, pytest, env-gated connector transports, local run stores.

---

## Zone Contract

Do not touch appv24 red-zone analogs:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Preferred implementation scope:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_*.py
docs/appv24/
docs/superpowers/
```

Yellow-zone command exposure is allowed only when behavior already exists in `workflows/`:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
```

## Current State

Already present:

```text
connector contracts
capability catalog
package validator
IR compiler
dry/local runner
approval ledger
credential refs
live preflight
live broker
live-read runner
live-draft transport
live-production transport
phase runner
activation records
schedule/poll decision helper
webhook ingress decision helper
deployment intent
bundle export
IaC export
```

Main missing joins:

```text
automatic trigger execution loop
webhook accept-to-run handoff
phase-runner continuation commands
deployment intent parity with live gates
repeatable integration tests covering end-to-end state transitions
```

## Core Design Patterns

```text
Typed Contract Catalog
  One source of truth for action vocabulary, scopes, secrets, modes, outputs, idempotency, and approval class.

Default-Deny Gate
  Unknown actions, missing allowlists, missing credentials, unsupported live modes, bad targets, and unapproved mutations fail before broker dispatch.

Explicit State Machine
  A run is preview/local/live-read/live-draft/live-production. No implicit escalation between modes.

Broker Adapter
  Runners call a broker boundary. Connectors live behind transports and never leak raw provider responses into allowed outputs.

Approval Ledger
  Mutating or external-send steps require approval tied to package fingerprint, step, action, and idempotency key.

Audit-First Execution
  Every preview, run, block, approval request, and connector action produces artifacts under the run or activation store.

Trigger Queue Boundary
  Schedule, poll, and webhook triggers create activation events first. Execution is a separate gated step.

Deployment Intent
  Export requirements for env, secrets, scopes, triggers, approvals, runtime mode, state, and audit without deploying infrastructure.
```

## Data Structures

Existing structures to preserve and extend only when needed:

```python
ConnectorContract(
    action_ref: str,
    connector_id: str,
    action_id: str,
    input_schema: dict[str, object],
    output_schema: dict[str, object],
    allowed_outputs: tuple[str, ...],
    required_scopes: tuple[str, ...],
    secrets: tuple[ConnectorSecretRef, ...],
    side_effect_class: SideEffectClass,
    approval_class: ApprovalClass,
    live_modes: tuple[WorkflowLiveMode, ...],
    idempotency_key_fields: tuple[str, ...],
    retry_class: RetryClass,
    target_policy: ConnectorTargetPolicy,
    prompt_summary: str,
    adapter: str,
    execution_mode: str,
    dry_run_supported: bool,
    design_only: bool,
)
```

```python
LiveGateDecision(
    ok: bool,
    checked_actions: tuple[str, ...],
    issues: tuple[LiveGateIssue, ...],
)

LiveGateIssue(
    code: str,
    step_id: str | None,
    action_ref: str | None,
    message: str,
    repair_hint: str | None,
)
```

```python
WorkflowPhaseRunReport(
    status: str,
    live_read_run_id: str,
    local_run_id: str,
    approval_run_id: str | None,
    phase_statuses: tuple[str, ...],
    next_commands: tuple[str, ...],
    approval_request_ids: tuple[str, ...],
    live_production_run_id: str,
    live_production_report: WorkflowRunReport | None,
    issues: tuple[str, ...],
)
```

```python
WorkflowActivationRecord(
    activation_id: str,
    workflow_name: str,
    package_path: Path,
    package_fingerprint: str,
    trigger_type: str,
    fixture_name: str,
    mode: WorkflowRunMode,
    approve_all: bool,
    trigger_config: Mapping[str, Any],
    cursor: str | None,
    max_concurrency: int,
    cooldown_seconds: int,
    last_started_at: str | None,
    last_finished_at: str | None,
    consecutive_failures: int,
    enabled: bool,
    last_run_id: str | None,
    last_status: str | None,
)
```

```python
WorkflowDeploymentIntent(
    workflow_name: str,
    package_fingerprint: str,
    runtime_modes: tuple[str, ...],
    env_requirements: tuple[str, ...],
    secret_refs: tuple[str, ...],
    webhook_triggers: tuple[str, ...],
    scheduler_triggers: tuple[str, ...],
    connector_scopes: dict[str, tuple[str, ...]],
    connector_targets: dict[str, tuple[str, ...]],
    approval_ids: tuple[str, ...],
    approval_policy: tuple[str, ...],
    idempotency_required_for: tuple[str, ...],
    state_requirements: tuple[str, ...],
    audit_requirements: tuple[str, ...],
    rollback_required: bool,
)
```

Add only if needed:

```python
@dataclass(frozen=True)
class TriggerRunDecision:
    activation_id: str
    trigger_type: str
    status: str
    reason: str
    should_run: bool
    run_id: str | None = None
    run_status: str | None = None


@dataclass(frozen=True)
class WebhookRunDecision:
    activation_id: str
    status: str
    reason: str
    replay_key: str | None
    run_id: str | None = None
    run_status: str | None = None
```

## Phase 1: Contract Spine

**Purpose:** Make the connector contract catalog the only executable vocabulary.

**Files:**

```text
appV2.4/appv24/workflows/connector_contracts.py
appV2.4/appv24/workflows/connector_packs.py
appV2.4/appv24/workflows/catalog_builtins.py
appV2.4/appv24/workflows/live_gate.py
appV2.4/tests/test_workflow_connector_contracts.py
appV2.4/tests/test_workflow_live_gate.py
```

**Build:**

```text
1. Assert every package action must resolve to ConnectorContract.
2. Assert allowed outputs are contract-derived.
3. Assert live mode eligibility comes from contract.live_modes.
4. Assert mutating actions have approval class and idempotency fields.
5. Assert prompt/catalog fragments derive from contract prompt_summary, not duplicated prose.
```

**Done when:**

```text
unknown action blocks
invented connector vocabulary blocks
mutating action without idempotency blocks
catalog/prompt/action metadata snapshots pass
```

## Phase 2: Execution State And Gates

**Purpose:** Make every execution attempt prove its state before a broker can run.

**Files:**

```text
appV2.4/appv24/workflows/live_gate.py
appV2.4/appv24/workflows/live_preflight.py
appV2.4/appv24/workflows/live_runner.py
appV2.4/appv24/workflows/live_broker.py
appV2.4/appv24/workflows/approval_ledger.py
appV2.4/appv24/workflows/idempotency.py
appV2.4/tests/test_workflow_live_preflight.py
appV2.4/tests/test_workflow_live_runner.py
appV2.4/tests/test_workflow_approval_ledger.py
```

**Build:**

```text
1. Gate order is fixed: validate -> compile -> policy -> credential refs -> contract -> allowlist -> target policy -> approval -> idempotency -> transport.
2. live-read can pause at the first non-read step but cannot mutate.
3. live-draft can create drafts only after approval/idempotency gates.
4. live-production can perform external sends/mutations only after approval/idempotency gates and explicit production enablement.
5. Broker results are output-filtered against contract.allowed_outputs.
```

**Done when:**

```text
live-read cannot send
live-draft cannot production-send
live-production cannot run without granted approval
missing credential refs show env names only
provider secrets and raw responses are never printed
```

## Phase 3: Phase Runner

**Purpose:** Join safe phases into an operator-friendly workflow without auto-approving or hiding gates.

**Files:**

```text
appV2.4/appv24/workflows/phase_runner.py
appV2.4/appv24/workflows/reports.py
appV2.4/appv24/workflows/explain.py
appV2.4/tests/test_workflow_phase_runner.py
appV2.4/tests/test_workflow_run_explain.py
```

**Build:**

```text
1. live-read prefix runs and pauses with handoff outputs.
2. local resume consumes handoff outputs and reaches terminal or approval-required.
3. approval-required report lists request ids and exact next commands.
4. approved continuation runs only live-draft or live-production through the existing live runner.
5. phase report includes run ids, phase statuses, issues, next commands, and approval request ids.
```

**Done when:**

```text
source -> llm -> policy -> telegram draft stops at approval
approved telegram production continuation runs through live-production broker
phase reports are enough for TUI follow-up without inspecting files manually
```

## Phase 4: Triggers And Activation

**Purpose:** Let workflows run from manual, schedule, poll, and webhook triggers without inline unsafe side effects.

**Files:**

```text
appV2.4/appv24/workflows/activation.py
appV2.4/appv24/workflows/scheduler.py
appV2.4/appv24/workflows/webhook_ingress.py
appV2.4/appv24/workflows/trigger_state.py
appV2.4/tests/test_workflow_activation.py
appV2.4/tests/test_workflow_triggers.py
appV2.4/tests/test_workflow_webhook_ingress.py
```

**Build:**

```text
1. Activation records pin package fingerprint, fixture, trigger type, mode, cursor, concurrency, cooldown, and enabled state.
2. Schedule/poll tick evaluates due/skipped/disabled before any run is created.
3. Due schedule/poll activation marks started, runs local mode, marks finished, and appends history.
4. Webhook ingress validates trigger type, enabled state, shared secret, replay key, and payload hash.
5. Webhook accepted event can hand off to activation run; duplicate replay keys never run twice.
```

**Done when:**

```text
disabled activation cannot run
changed package fingerprint blocks
cooldown prevents duplicate schedule/poll runs
webhook secret mismatch blocks
duplicate webhook replay blocks
accepted webhook can produce one local audited run
```

## Phase 5: Deployment Intent And IaC

**Purpose:** Export what a valid workflow needs to run elsewhere, without deploying real infrastructure yet.

**Files:**

```text
appV2.4/appv24/workflows/deployment_intent.py
appV2.4/appv24/workflows/bundle_export.py
appV2.4/appv24/workflows/iac_export.py
appV2.4/appv24/workflows/live_env_template.py
appV2.4/tests/test_workflow_deployment_intent.py
appV2.4/tests/test_workflow_bundle_export.py
appV2.4/tests/test_workflow_iac_export.py
```

**Build:**

```text
1. Deployment intent derives env vars, secret refs, scopes, triggers, approvals, idempotency, state, and audit needs from the same contracts and IR used by live gates.
2. Bundle export writes checksummed manifest files and refuses stale package fingerprints.
3. IaC export emits declarative intent only: env requirements, secrets required, webhook ingress needs, connector scopes, approval policy, runtime mode.
4. No cloud mutation, no provider provisioning, no secret values.
5. Readiness checks compare package fingerprint and bundle fingerprint before live commands.
```

**Done when:**

```text
bundle includes deployment intent
stale bundle blocks readiness
export contains no raw secrets
IaC output is deterministic for the same package fingerprint
```

## Implementation Order

```text
1. Phase 4 trigger-run join first.
   Reason: contracts/gates/runners mostly exist; trigger execution is the clearest missing bridge.

2. Phase 3 next-command ergonomics second.
   Reason: operators need exact follow-up commands after approval-required reports.

3. Phase 5 deployment parity third.
   Reason: bundle/export must reflect the same contracts and gate outcomes.

4. Phase 1/2 regression hardening fourth.
   Reason: existing code already has the spine; add contract snapshots and missing negative tests.

5. Yellow-zone CLI/TUI exposure last.
   Reason: command surfaces should only wrap proven workflow behavior.
```

## Verification

Targeted first:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_connector_contracts.py \
  appV2.4/tests/test_workflow_live_gate.py \
  appV2.4/tests/test_workflow_phase_runner.py \
  appV2.4/tests/test_workflow_activation.py \
  appV2.4/tests/test_workflow_triggers.py \
  appV2.4/tests/test_workflow_webhook_ingress.py \
  appV2.4/tests/test_workflow_bundle_export.py \
  appV2.4/tests/test_workflow_iac_export.py
```

Broader before gitops:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Manual-gated only:

```text
TUI background workflow-designer-lite generation test
TUI local run/preview/export-bundle test
live-read/live-draft/live-production tests only with explicit env gates and test credentials
```

## Non-Goals

```text
No red-zone runtime changes.
No package split.
No cloud deployment.
No live connector execution without explicit env gates.
No raw secret printing.
No automatic production mutation after model generation.
No cross-profile custom-node authoring in this plan.
```

## Self-Review

```text
No unresolved markers.
No red-zone implementation scope.
No new framework dependency.
No live side effects by default.
Five phases only.
The first executable work item is green-zone trigger-run join.
```
