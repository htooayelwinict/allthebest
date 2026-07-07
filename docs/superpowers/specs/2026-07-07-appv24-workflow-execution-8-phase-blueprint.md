# appv24 Workflow Execution 8-Phase Blueprint

## Purpose

This blueprint turns appv24 workflow-designer-lite from "valid workflow package generation" into a safe path toward actual workflow execution.

The design keeps appv24 as a profile/product layer. It must not change the runtime kernel. New execution behavior belongs in:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_*.py
docs/appv24/
docs/superpowers/
```

Yellow-zone command exposure is allowed only as a wrapper over proven workflow modules:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
```

Do not touch red-zone runtime files.

## Current State

Current appv24 already has the core pieces:

```text
natural-language workflow draft
validator and repair loop
quality gate
package save
IR compiler
policy checker
local runner
approval preparation
approval ledger
credential reference preflight
live preflight
live gate
live broker
live-read runner
live-draft runner
live-production runner
phase runner
activation store
manual activation
schedule/poll tick command
webhook replay protection command
bundle export
deployment intent
IaC intent export
```

Implemented live-capable transport families:

```text
source.fetch_items          live-read
source.fetch_document       live-read
http.fetch                  live-read
github.list_dependabot_alerts live-read
gmail.create_draft          live-draft
telegram.send_message       live-production
```

The gap is no longer basic graph execution. The gap is hardening and joining these pieces so a real external action can only happen after the package, contract, credentials, allowlist, target, approval, idempotency, and audit gates are all satisfied.

## External Grounding

MCP/context findings used for this blueprint:

```text
HTTPX:
  Use explicit client/request timeouts.
  Do not disable timeouts.
  Control redirect following explicitly.
  Bound response reads or stream large responses.
  Convert HTTPStatusError and RequestError into sanitized connector issues.

Gmail API:
  Draft creation is POST /gmail/v1/users/me/drafts.
  Request body contains message.raw as RFC 2822 MIME encoded as base64URL.
  Successful response includes draft id and message metadata.
  This belongs in live-draft, not live-production.

Telegram Bot API:
  sendMessage requires chat_id and text.
  Success returns a Message object with message_id.
  chat_id must be restricted by operator allowlist before production send.
  Optional paid/high-risk parameters should stay unsupported in v0.

Open Bridge advisory:
  Highest-risk gaps are approval state drift, idempotency races, payload validation bypass, and credential leakage across phase boundaries.
  TUI must never be the authorization layer.
```

## Recommended Architecture

Use a default-deny phase machine:

```text
draft package
  -> validate
  -> compile IR
  -> policy check
  -> bundle/export fingerprint check
  -> credential reference preflight
  -> connector contract gate
  -> target allowlist gate
  -> live-read
  -> local intelligence/resume
  -> approval request
  -> explicit approval
  -> live-draft
  -> explicit production approval if needed
  -> live-production
  -> audit report
```

No command should silently escalate from one mode to the next.

Execution modes:

```text
local
  fixture-backed, no external side effects

live-read
  external reads only, may pause at first non-read step

live-draft
  approved draft creation only, no send

live-production
  approved external send/mutation only, disabled by default
```

## Core Data Structures

Keep these as the main contract spine:

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

Add a persistent idempotency lease only when moving beyond single-process tests:

```python
@dataclass(frozen=True)
class IdempotencyLease:
    key: str
    payload_hash: str
    package_fingerprint: str
    step_id: str
    connector_action: str
    status: str  # claimed | committed | failed | expired
    run_id: str
    created_at: str
    expires_at: str
```

Add an approval binding snapshot for exact payload review:

```python
@dataclass(frozen=True)
class ApprovalBinding:
    approval_request_id: str
    package_fingerprint: str
    step_id: str
    connector_action: str
    idempotency_key: str
    payload_hash: str
    payload_preview_path: str
    status: str  # pending | granted | denied | stale
```

Add a connector invocation envelope so every transport gets the same safe shape:

```python
@dataclass(frozen=True)
class ConnectorInvocation:
    action_ref: str
    mode: str
    input: Mapping[str, object]
    idempotency_key: str | None
    approval_binding: ApprovalBinding | None
    allowed_outputs: tuple[str, ...]
    target_policy: ConnectorTargetPolicy
    run_id: str
    step_id: str
```

## Phase 1: Live Execution Contract

Goal: make execution modes impossible to confuse.

Design:

```text
WorkflowRunMode is the state boundary.
LiveWorkflowBroker mode must match runner mode.
Transport result status must match broker mode.
Unknown modes block at construction or preflight.
Production broker remains disabled unless explicit production flag is true.
```

Gate order:

```text
validate package
compile IR
policy check
bundle fingerprint check
credential reference preflight
contract lookup
mode eligibility
operator allowlist
target policy
approval and idempotency when needed
transport configured
broker invoke
output filter
audit persist
```

Files:

```text
appV2.4/appv24/workflows/execution.py
appV2.4/appv24/workflows/live_broker.py
appV2.4/appv24/workflows/live_gate.py
appV2.4/appv24/workflows/live_runner.py
```

Done when:

```text
live-read cannot mutate
live-draft cannot send
live-production cannot run without production enablement
broker cannot return outputs for blocked results
transport status mismatch blocks
```

## Phase 2: Credential Policy

Goal: credentials are operator-provided capabilities, not workflow-package data.

Design:

```text
Workflow packages declare connector permissions and scopes.
Environment provides opaque credential references and explicit scopes.
Preflight checks required connector refs and exact scopes.
Wildcard scopes are blocked.
Raw credential values are never printed or persisted.
Live transports read provider tokens from process env only at transport construction.
Run artifacts store credential ref names and scope names only.
```

Credential env shape:

```text
APPV24_<CONNECTOR>_CREDENTIAL_REF=<opaque-reference>
APPV24_<CONNECTOR>_SCOPES=<scope>[,<scope>]
```

Transport env gates:

```text
APPV24_WORKFLOW_LIVE_READ_ENABLED=true
APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true
APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true
```

Files:

```text
appV2.4/appv24/workflows/credential_refs.py
appV2.4/appv24/workflows/live_env_template.py
appV2.4/appv24/workflows/live_readiness.py
appV2.4/appv24/workflows/live_transports.py
```

Done when:

```text
missing ref blocks before transport
missing scope blocks before transport
wildcard scope blocks before transport
readiness and env-template show variable names only
run artifacts contain no token/API key/bot token values
```

## Phase 3: Connector Hardening

Goal: every connector action is deterministic, schema-bounded, output-filtered, and target-limited.

Design:

```text
ConnectorContract is the only executable vocabulary.
Every action has input_schema and output_schema.
Every live transport normalizes provider responses into allowed outputs.
Provider raw payloads are never persisted by default.
HTTP connectors enforce allowed scheme, allowed hosts, redirect policy, timeout, and max bytes.
Telegram production send enforces allowed chat ids.
Gmail draft only creates drafts and returns draft/message ids.
Unsupported optional provider parameters are dropped or rejected.
```

Connector classes:

```text
read-only:
  source.fetch_items
  source.fetch_document
  http.fetch
  github.list_dependabot_alerts

draft:
  gmail.create_draft
  notification.create_draft
  telegram.create_message_draft

production:
  telegram.send_message
```

Files:

```text
appV2.4/appv24/workflows/connector_contracts.py
appV2.4/appv24/workflows/connector_registry.py
appV2.4/appv24/workflows/live_transports.py
appV2.4/appv24/workflows/http_safety.py
```

Done when:

```text
invented connector action blocks
unknown output field is filtered
off-base source URL blocks
http.fetch to non-allowlisted host blocks
telegram.send_message to non-allowlisted chat blocks
gmail.create_draft cannot send
```

## Phase 4: Approval Gate

Goal: approval is bound to exactly what will run.

Design:

```text
Local approval-prep creates pending approval requests.
Each request includes package fingerprint, step id, connector action, idempotency key, payload hash, side-effect class, and allowed output summary.
Operator approves or denies a request id.
Live-draft/live-production checks approval against the same package fingerprint, step, action, and idempotency key.
If package content or payload hash changes, approval is stale.
```

Approval commands remain operator UX only:

```text
/workflow-run-approval <package> [fixture]
/workflow-approvals <package> <approval-run-id>
/workflow-approve <package> <approval-run-id> <approval-request-id>
/workflow-deny <package> <approval-run-id> <approval-request-id>
```

Files:

```text
appV2.4/appv24/workflows/approval_gate.py
appV2.4/appv24/workflows/approval_ledger.py
appV2.4/appv24/workflows/approval_commands.py
appV2.4/appv24/workflows/live_gate.py
```

Done when:

```text
mutating action without approval blocks
approval for old package fingerprint blocks
approval for different idempotency key blocks
denied approval blocks
payload preview is inspectable before approval
TUI cannot bypass approval checks
```

## Phase 5: Idempotency Ledger

Goal: duplicate sends/drafts/mutations cannot happen accidentally.

Design:

```text
Idempotency key derives from package fingerprint, step id, connector action, configured key fields, and resolved input values.
Before a live mutating transport runs, runner claims the idempotency key.
If an existing committed key has the same payload hash, reuse or block according to action retry class.
If an existing key has a different payload hash, hard block.
For webhooks/schedules, replay keys and activation cooldowns run before idempotency claim.
```

Near-term:

```text
single-process JSONL/file ledger is acceptable for local appv24
atomic create/rename or lock file is required before background daemon work
```

Future:

```text
CAS/lease-backed store for cloud execution
lease TTL for crashed workers
hash-chained audit entries for tamper evidence
```

Files:

```text
appV2.4/appv24/workflows/idempotency.py
appV2.4/appv24/workflows/run_store.py
appV2.4/appv24/workflows/webhook_ingress.py
appV2.4/appv24/workflows/activation.py
```

Done when:

```text
same key same payload cannot double-send
same key different payload blocks
duplicate webhook replay blocks before run
schedule cooldown blocks duplicate trigger runs
run report records hashed idempotency key, not raw payload
```

## Phase 6: Phase Runner

Goal: join phases without hiding gates.

Design:

```text
live-read prefix runs first and may pause.
Handoff records completed read steps, output refs, next step, and next phase.
Local resume consumes handoff outputs and reaches terminal or approval-required.
Approval-required report returns request ids and next commands.
Approved continuation calls live-draft or live-production through existing live runners.
The phase runner never directly invokes provider APIs.
```

Canonical flow:

```text
/workflow-live-plan <package> [fixture]
/workflow-live-ready live-read <package> <fixture> --allow <read-actions>
/workflow-run-live-read-to-approval <package> <fixture> --allow <read-actions>
/workflow-approvals <package> <approval-run-id>
/workflow-approve <package> <approval-run-id> <request-id>
/workflow-live-ready live-draft|live-production <package> <fixture> <approval-run-id> --allow <actions>
/workflow-run-live-draft|live-production <package> <fixture> <approval-run-id> --allow <actions>
```

Files:

```text
appV2.4/appv24/workflows/phase_runner.py
appV2.4/appv24/workflows/live_plan.py
appV2.4/appv24/workflows/reports.py
appV2.4/appv24/workflows/explain.py
```

Done when:

```text
read -> local -> approval-required is one guided path
approved continuation reuses live runner gates
phase report contains every run id
phase report contains exact next commands
phase runner does not auto-approve
```

## Phase 7: TUI UX

Goal: make live workflow operation understandable without making the TUI a policy engine.

Design:

```text
TUI commands are thin wrappers over workflows modules.
TUI renders readiness, plan, approval, run, status, and explain reports.
TUI never decides authorization.
TUI never stores secrets.
TUI shows command sequence and blocked reasons in plain operator language.
```

Required operator surfaces:

```text
/workflow-connectors <package>
/workflow-live-env-template <mode> <package> --allow <actions>
/workflow-live-plan <package> [fixture]
/workflow-live-ready <mode> <package> <fixture> [approval-run-id] --allow <actions>
/workflow-run-live-read-to-approval <package> <fixture> --allow <read-actions>
/workflow-approvals <package> <approval-run-id>
/workflow-approve <package> <approval-run-id> <approval-request-id>
/workflow-deny <package> <approval-run-id> <approval-request-id>
/workflow-run-live-draft <package> <fixture> <approval-run-id> --allow <actions>
/workflow-run-live-production <package> <fixture> <approval-run-id> --allow <actions>
/workflow-run-explain <package> [run-id]
```

Files:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
```

Done when:

```text
same behavior works in plain CLI and TUI
TUI blocked reports show next safe command
TUI approval flow shows request ids and payload summaries
TUI live commands do not print secrets
TUI tests cover command parsing and report rendering
```

## Phase 8: Tests And QA Fixtures

Goal: prove workflow execution safety through repeatable tests before real provider use.

Test layers:

```text
unit:
  contracts, schemas, idempotency, approval binding, URL safety, credential refs

integration:
  package -> validate -> compile -> policy -> preflight -> local/live runner with fake transports

phase:
  live-read pause -> local resume -> approval -> live-draft/live-production continuation

activation:
  manual, schedule/poll tick, webhook replay, changed package fingerprint, cooldown

TUI:
  old-style interactive bg runs for generated workflow packages and command flows

manual-gated:
  real source/http/github/gmail/telegram only with explicit test credentials and env gates
```

Golden scenarios:

```text
read-only source workflow passes live-read
source -> llm -> telegram approval stops before send
approved telegram send passes with allowed chat id
telegram send blocks for disallowed chat id
gmail draft creates only a draft
duplicate webhook replay blocks
duplicate idempotency key blocks second mutation
changed package fingerprint invalidates activation and approval
stale bundle blocks live readiness
unknown connector action blocks
```

Verification commands:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_*.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Manual-gated QA:

```text
Run old-style TUI with qwen/qwen3-coder-next or mimo.
Generate a workflow package from a normal user prompt.
Save it.
Run local.
Create activation.
Run activation.
Run live-plan/readiness.
Only run live provider commands when test credentials and allowlists are explicitly configured.
```

## Implementation Sequence

Recommended order:

```text
1. Finish contract spine regression tests.
2. Make approval binding payload previews exact and stale-safe.
3. Replace in-memory-only idempotency assumptions with persistent local ledger semantics.
4. Harden connector input/output schemas and target policies.
5. Complete phase-runner next-command UX and continuation paths.
6. Wire activation trigger decisions to phase-runner-safe modes.
7. Add TUI/CLI wrappers only after workflow module behavior is tested.
8. Add manual-gated live provider fixtures and docs.
```

Reasoning:

```text
Approval and idempotency are the dangerous parts.
Connector hardening comes before adding more transports.
Phase runner should join proven gates, not invent new policy.
TUI is last because it must remain a display/control surface only.
```

## Non-Goals

```text
No red-zone runtime changes.
No package split.
No live provider call by default.
No cloud deployment yet.
No secret storage in workflow packages.
No model-generated custom code nodes in this blueprint.
No automatic production mutation after workflow generation.
No TUI-only authorization logic.
```

## Self-Review

```text
No unresolved markers or placeholder sections.
No red-zone implementation scope.
No conflict with the five-phase implementation plan; this blueprint expands it into eight design concerns.
No requirement for live side effects by default.
No third-party API raw secret printing.
No domain-specific financial workflow assumption.
```
