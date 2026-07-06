# appv24 Workflow Live Execution Six Phases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move workflow-designer-lite from valid local/sandbox packages to contract-first, approval-gated live workflow execution without touching kernel/red zones.

**Architecture:** Keep the workflow profile as a product layer over appv24: natural language produces a validated package, the package compiles to IR, the IR is checked against a strict connector contract catalog, and only then can a gated live runner call approved transports. The connector contract catalog becomes the single source of truth for model vocabulary, validation, preflight, transport readiness, approvals, idempotency, and deployment intent.

**Tech Stack:** Python dataclasses, JSON Schema 2020-12, existing appv24 workflow modules, pytest, HTTPX for bounded HTTP transports, existing TUI slash commands.

---

## Zone Contract

Do not touch red zones:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Avoid yellow zones unless a CLI/TUI command must expose already-built workflow behavior. Preferred scope:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_*.py
docs/superpowers/
```

## Current State Evidence

Existing workflow modules already provide validation, IR compilation, credential refs, approval ledger, live preflight, live broker, live runner, live transports, activation, run store, and deployment intent.

Existing live transport facts:

- `gmail.create_draft` is implemented as live-draft.
- `telegram.send_message` is implemented as live-production.
- `source.fetch_items` and `source.fetch_document` are implemented as live-read.
- `http.fetch` is not yet present.
- Contract metadata exists in `connector_packs.py`, but it is too small to be the full live execution contract.

External API facts used for this plan:

- Gmail draft creation uses `users/me/drafts` with a base64URL RFC 2822 MIME message and returns draft/message metadata.
- Telegram `sendMessage` requires `chat_id` and `text`, returns a message object on success, and is a real side effect.
- HTTP read connectors need bounded timeouts, redirect policy, response-size limits, and SSRF allowlist controls.

## Design Patterns

- **Typed contract catalog:** every executable action is declared once as a typed contract, not spread across prompt text, validator logic, preflight, and transports.
- **Default-deny registry:** unknown actions, unsupported modes, missing scopes, unsupported outputs, and missing idempotency fields fail before transport dispatch.
- **Gate pipeline:** validation -> compile -> policy -> bundle guard -> credential refs -> contract gate -> allowlist -> approval ledger -> idempotency -> transport.
- **Transport adapter:** each connector transport implements a narrow `invoke(BrokerRequest) -> BrokerResult` boundary.
- **Policy object:** target allowlists, HTTP safety, approval class, and retry/idempotency policy are explicit data, not prompt advice.
- **Audit-first execution:** every blocked, simulated, approved, and executed action writes run events and artifacts.
- **Trigger state machine:** manual, schedule, poll, and webhook activation state is persisted independently from the agent loop.

## Core Data Structures

Add `appV2.4/appv24/workflows/connector_contracts.py`.

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkflowLiveMode(StrEnum):
    LIVE_READ = "live-read"
    LIVE_DRAFT = "live-draft"
    LIVE_PRODUCTION = "live-production"


class SideEffectClass(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SEND = "send"
    PUBLISH = "publish"
    PROVISION = "provision"


class ApprovalClass(StrEnum):
    NONE = "none"
    DRAFT = "draft"
    EXTERNAL_SEND = "external_send"
    PRODUCTION_MUTATION = "production_mutation"
    INFRA_PROVISION = "infra_provision"


class RetryClass(StrEnum):
    NONE = "none"
    READ_SAFE = "read_safe"
    IDEMPOTENT_WRITE = "idempotent_write"


@dataclass(frozen=True)
class ConnectorSecretRef:
    env_name: str
    purpose: str
    required: bool = True


@dataclass(frozen=True)
class ConnectorTargetPolicy:
    allow_env: str | None = None
    allowed_schemes: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    allowed_path_prefixes: tuple[str, ...] = ()
    allowed_chat_ids_env: str | None = None
    max_response_bytes: int | None = None
    follow_redirects: bool = False


@dataclass(frozen=True)
class ConnectorContract:
    action_ref: str
    connector_id: str
    action_id: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    allowed_outputs: tuple[str, ...]
    required_scopes: tuple[str, ...]
    secrets: tuple[ConnectorSecretRef, ...]
    side_effect_class: SideEffectClass
    approval_class: ApprovalClass
    live_modes: tuple[WorkflowLiveMode, ...]
    idempotency_key_fields: tuple[str, ...]
    retry_class: RetryClass
    target_policy: ConnectorTargetPolicy
    prompt_summary: str
```

Add `appV2.4/appv24/workflows/live_gate.py`.

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveGateIssue:
    code: str
    step_id: str | None
    action_ref: str | None
    message: str
    repair_hint: str | None = None


@dataclass(frozen=True)
class LiveGateDecision:
    ok: bool
    checked_actions: tuple[str, ...]
    issues: tuple[LiveGateIssue, ...]
```

Add `appV2.4/appv24/workflows/http_safety.py`.

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafeHttpRequest:
    url: str
    params: dict[str, str]
    headers: dict[str, str]
    timeout_seconds: float
    follow_redirects: bool
    max_response_bytes: int
```

Add trigger data only when Phase 5 starts:

```python
@dataclass(frozen=True)
class WorkflowTriggerState:
    activation_id: str
    trigger_type: str
    package_fingerprint: str
    cursor: str | None
    last_run_id: str | None
    last_started_at: str | None
    last_finished_at: str | None
    consecutive_failures: int
    enabled: bool
```

## Phase 1: Branch Hygiene

**Files:**

- Modify: none for new behavior.
- Commit existing intended hygiene only:
  - `appV2.4/appv24/workflows/run_store.py`
  - `appV2.4/appv24/workflows/semantic.py`
  - `appV2.4/tests/test_workflow_run_store.py`
  - `docs/superpowers/specs/2026-07-06-appv24-cross-profile-code-node-authoring-idea.md`

- [ ] **Step 1: Verify current diff scope**

Run:

```bash
git status --short --branch
git diff -- appV2.4/appv24/workflows/run_store.py appV2.4/appv24/workflows/semantic.py appV2.4/tests/test_workflow_run_store.py
git diff -- docs/superpowers/specs/2026-07-06-appv24-cross-profile-code-node-authoring-idea.md
```

Expected:

```text
Only workflow green-zone files, tests, and docs are modified.
No appv24 red-zone or yellow-zone files appear.
```

- [ ] **Step 2: Run targeted regression tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_run_store.py appV2.4/tests/test_workflow_packages.py -q
```

Expected:

```text
All selected tests pass.
```

- [ ] **Step 3: Commit hygiene**

Run:

```bash
git add appV2.4/appv24/workflows/run_store.py appV2.4/appv24/workflows/semantic.py appV2.4/tests/test_workflow_run_store.py docs/superpowers/specs/2026-07-06-appv24-cross-profile-code-node-authoring-idea.md
git commit -m "fix(appv24): tighten workflow secret false positives"
```

Expected:

```text
One small commit, no red/yellow zone files.
```

## Phase 2: Live-Safe Connector Contracts

**Files:**

- Create: `appV2.4/appv24/workflows/connector_contracts.py`
- Create: `appV2.4/appv24/workflows/live_gate.py`
- Modify: `appV2.4/appv24/workflows/connector_packs.py`
- Modify: `appV2.4/appv24/workflows/connector_registry.py`
- Modify: `appV2.4/appv24/workflows/prompt_fragments.py`
- Modify: `appV2.4/appv24/workflows/semantic.py`
- Test: `appV2.4/tests/test_workflow_connector_contracts.py`
- Test: `appV2.4/tests/test_workflow_live_gate.py`

- [ ] **Step 1: Add connector contract tests first**

Create tests that assert:

```python
def test_builtin_contract_catalog_is_default_deny():
    catalog = builtin_connector_contract_catalog()
    assert "gmail.create_draft" in catalog
    assert "telegram.send_message" in catalog
    assert "http.fetch" in catalog
    assert "slack.send_message" not in catalog


def test_gmail_create_draft_contract_is_draft_only():
    contract = builtin_connector_contract_catalog()["gmail.create_draft"]
    assert contract.approval_class is ApprovalClass.DRAFT
    assert WorkflowLiveMode.LIVE_DRAFT in contract.live_modes
    assert WorkflowLiveMode.LIVE_PRODUCTION not in contract.live_modes
    assert contract.idempotency_key_fields == ("to", "subject")


def test_telegram_send_message_contract_requires_target_policy():
    contract = builtin_connector_contract_catalog()["telegram.send_message"]
    assert contract.approval_class is ApprovalClass.EXTERNAL_SEND
    assert contract.target_policy.allowed_chat_ids_env == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert WorkflowLiveMode.LIVE_PRODUCTION in contract.live_modes
```

- [ ] **Step 2: Implement `connector_contracts.py`**

Define the dataclasses listed in **Core Data Structures** and a function:

```python
def builtin_connector_contract_catalog() -> dict[str, ConnectorContract]:
    contracts = (
        ConnectorContract(
            action_ref="source.fetch_items",
            connector_id="source",
            action_id="fetch_items",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object", "required": ["items"], "properties": {"items": {"type": "array"}}},
            allowed_outputs=("items", "source_count", "metadata"),
            required_scopes=("source:read",),
            secrets=(ConnectorSecretRef("APPV24_SOURCE_BASE_URL", "operator source base URL"),),
            side_effect_class=SideEffectClass.READ,
            approval_class=ApprovalClass.NONE,
            live_modes=(WorkflowLiveMode.LIVE_READ,),
            idempotency_key_fields=(),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(allowed_schemes=("http", "https"), follow_redirects=False),
            prompt_summary="source.fetch_items(input: source_url/path/query) -> items, source_count",
        ),
        ConnectorContract(
            action_ref="http.fetch",
            connector_id="http",
            action_id="fetch",
            input_schema={"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"status_code": {"type": "integer"}, "url": {"type": "string"}, "text": {"type": "string"}, "json": {}}},
            allowed_outputs=("status_code", "url", "text", "json", "artifact_path"),
            required_scopes=("http:read",),
            secrets=(),
            side_effect_class=SideEffectClass.READ,
            approval_class=ApprovalClass.NONE,
            live_modes=(WorkflowLiveMode.LIVE_READ,),
            idempotency_key_fields=(),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(
                allow_env="APPV24_HTTP_FETCH_ALLOWED_HOSTS",
                allowed_schemes=("http", "https"),
                max_response_bytes=65536,
                follow_redirects=False,
            ),
            prompt_summary="http.fetch(input: url, query) -> status_code, url, text/json",
        ),
        ConnectorContract(
            action_ref="gmail.create_draft",
            connector_id="gmail",
            action_id="create_draft",
            input_schema={"type": "object", "required": ["to", "subject", "body"]},
            output_schema={"type": "object", "required": ["draft_id"], "properties": {"draft_id": {"type": "string"}, "thread_id": {"type": "string"}}},
            allowed_outputs=("draft_id", "thread_id", "artifact_path"),
            required_scopes=("gmail.compose",),
            secrets=(ConnectorSecretRef("APPV24_GMAIL_ACCESS_TOKEN", "Gmail OAuth access token", required=False),),
            side_effect_class=SideEffectClass.CREATE,
            approval_class=ApprovalClass.DRAFT,
            live_modes=(WorkflowLiveMode.LIVE_DRAFT,),
            idempotency_key_fields=("to", "subject"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            target_policy=ConnectorTargetPolicy(),
            prompt_summary="gmail.create_draft(input: to, subject, body) -> draft_id, thread_id",
        ),
        ConnectorContract(
            action_ref="telegram.send_message",
            connector_id="telegram",
            action_id="send_message",
            input_schema={"type": "object", "required": ["chat_id", "text"]},
            output_schema={"type": "object", "required": ["message_id"], "properties": {"message_id": {"type": "string"}, "chat_id": {"type": "string"}}},
            allowed_outputs=("message_id", "chat_id", "artifact_path"),
            required_scopes=("telegram:send",),
            secrets=(ConnectorSecretRef("APPV24_TELEGRAM_BOT_TOKEN", "Telegram bot token"),),
            side_effect_class=SideEffectClass.SEND,
            approval_class=ApprovalClass.EXTERNAL_SEND,
            live_modes=(WorkflowLiveMode.LIVE_PRODUCTION,),
            idempotency_key_fields=("chat_id", "text"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            target_policy=ConnectorTargetPolicy(allowed_chat_ids_env="APPV24_TELEGRAM_ALLOWED_CHAT_IDS"),
            prompt_summary="telegram.send_message(input: chat_id, text) -> message_id, chat_id",
        ),
        ConnectorContract(
            action_ref="llm.extract_structured",
            connector_id="llm",
            action_id="extract_structured",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object", "additionalProperties": True},
            allowed_outputs=("fields", "confidence", "issues", "artifact_path"),
            required_scopes=(),
            secrets=(),
            side_effect_class=SideEffectClass.READ,
            approval_class=ApprovalClass.NONE,
            live_modes=(),
            idempotency_key_fields=(),
            retry_class=RetryClass.NONE,
            target_policy=ConnectorTargetPolicy(),
            prompt_summary="llm.extract_structured(input: text/items/schema) -> fields, confidence, issues",
        ),
        ConnectorContract(
            action_ref="policy.check",
            connector_id="policy",
            action_id="check",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object", "properties": {"allowed": {"type": "boolean"}, "reason": {"type": "string"}}},
            allowed_outputs=("allowed", "reason", "matched_rule", "artifact_path"),
            required_scopes=(),
            secrets=(),
            side_effect_class=SideEffectClass.READ,
            approval_class=ApprovalClass.NONE,
            live_modes=(),
            idempotency_key_fields=(),
            retry_class=RetryClass.NONE,
            target_policy=ConnectorTargetPolicy(),
            prompt_summary="policy.check(input: fields/rules) -> allowed, reason, matched_rule",
        ),
    )
    return {contract.action_ref: contract for contract in contracts}
```

Required contract decisions:

```text
source.fetch_items:
  mode: live-read
  side effect: read
  scopes: source:read
  approval: none
  idempotency: none

http.fetch:
  mode: live-read
  method: GET only
  side effect: read
  scopes: http:read
  approval: none
  target policy: allowed schemes http/https, allowed hosts from env, no redirects by default, max bytes

gmail.create_draft:
  mode: live-draft
  side effect: create
  scopes: gmail.compose
  approval: draft
  idempotency: to, subject

telegram.send_message:
  mode: live-production
  side effect: send
  scopes: telegram:send
  approval: external_send
  idempotency: chat_id, text
  target policy: allowed chat IDs from env
```

- [ ] **Step 3: Make existing metadata consume contracts**

Keep `ConnectorActionMetadata` compatibility, but derive builtins from `ConnectorContract` so old callers still work.

Expected invariant:

```text
connector_packs.py remains a compatibility adapter.
connector_contracts.py becomes source of truth.
```

- [ ] **Step 4: Tighten semantic validation**

Update semantic validation so:

```text
Unknown action refs fail.
Declared connector actions must match builtin contract metadata.
Side-effect class must match contract.
Required scopes must include contract scopes.
Allowed outputs must be subset of contract outputs.
Live-production action must have approval and idempotency fields when contract requires them.
```

Failure examples:

```text
unknown connector action: telegram.receive_webhook
action gmail.create_draft must use side_effect_class create
action telegram.send_message requires approval class external_send
action http.fetch only supports live-read GET semantics
```

- [ ] **Step 5: Slim prompt vocabulary**

Update workflow prompt fragments to expose compact action summaries:

```text
source.fetch_items(input: source_url/path/query) -> items, source_count
http.fetch(input: url, query) -> status_code, url, text/json
llm.extract_structured(input: text/items/schema) -> fields, confidence, issues
policy.check(input: fields/rules) -> allowed, reason, matched_rule
gmail.create_draft(input: to, subject, body) -> draft_id, thread_id; draft approval required
telegram.send_message(input: chat_id, text) -> message_id, chat_id; production approval required
```

Do not dump full JSON schemas into the model prompt.

- [ ] **Step 6: Verify Phase 2**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_contracts.py appV2.4/tests/test_workflow_connector_registry.py appV2.4/tests/test_workflow_packages.py -q
```

Expected:

```text
All selected tests pass.
Unknown live actions fail before runner/preflight.
```

## Phase 3: Runner Gate First, Then Low-Risk Connector Pack

This phase intentionally hardens the gate before adding new connector behavior.

**Files:**

- Modify: `appV2.4/appv24/workflows/live_preflight.py`
- Modify: `appV2.4/appv24/workflows/live_runner.py`
- Modify: `appV2.4/appv24/workflows/live_broker.py`
- Modify: `appV2.4/appv24/workflows/live_transports.py`
- Create: `appV2.4/appv24/workflows/http_safety.py`
- Test: `appV2.4/tests/test_workflow_live_gate.py`
- Test: `appV2.4/tests/test_workflow_live_transports.py`

- [ ] **Step 1: Write live gate tests**

Assert the gate blocks:

```text
uncataloged action
mode/action mismatch
mutating action in live-read
missing connector allowlist
missing credential ref
missing approval for mutating action
missing idempotency key fields
missing configured transport
telegram target not allowlisted
http.fetch host not allowlisted
```

- [ ] **Step 2: Add live gate helper**

Implement:

```python
def evaluate_live_gate(
    ir: WorkflowIR,
    fixture: Mapping[str, object],
    *,
    mode: WorkflowRunMode,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    available_transports: Collection[str] | None,
    env: Mapping[str, str],
) -> LiveGateDecision:
    checked_actions: list[str] = []
    issues: list[LiveGateIssue] = []
    current = ir.entrypoint
    transitions = 0
    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append(LiveGateIssue("transition_budget", current, None, "transition budget exceeded"))
            break
        step = ir.steps.get(current)
        if step is None:
            issues.append(LiveGateIssue("unknown_step", current, None, f"unknown step: {current}"))
            break
        if step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            current = select_branch_from_fixture(step, fixture)
            continue
        action_ref = step.uses.dotted if step.uses is not None else None
        checked_actions.append(action_ref or "<missing>")
        issue = gate_step_against_contract(
            step,
            action_ref=action_ref,
            mode=mode,
            package_fingerprint=package_fingerprint,
            connector_allowlist=connector_allowlist,
            approval_index=approval_index,
            available_transports=available_transports,
            env=env,
            fixture=fixture,
        )
        if issue is not None:
            issues.append(issue)
            break
        current = step.next_step
    return LiveGateDecision(ok=not issues, checked_actions=tuple(checked_actions), issues=tuple(issues))
```

This function replaces duplicated mode-specific gate logic in `live_preflight.py` and becomes reusable by `live_runner.py`.

- [ ] **Step 3: Add `http.fetch` transport**

Add env:

```text
APPV24_HTTP_FETCH_ALLOWED_HOSTS=example.com,api.example.com
APPV24_HTTP_FETCH_MAX_RESPONSE_BYTES=65536
APPV24_HTTP_FETCH_TIMEOUT_SECONDS=15
APPV24_HTTP_FETCH_FOLLOW_REDIRECTS=false
```

Transport rules:

```text
GET only.
http/https only.
Host must match allowlist.
No redirects by default.
Timeout required.
Response body capped.
Return JSON when content-type is JSON, otherwise text.
Never return raw headers wholesale.
Never include cookies or authorization in artifacts.
```

- [ ] **Step 4: Harden Telegram target policy**

Add env:

```text
APPV24_TELEGRAM_ALLOWED_CHAT_IDS=12345,67890
```

Gate and transport must block `telegram.send_message` if `chat_id` is not in the configured allowlist.

- [ ] **Step 5: Verify Phase 3**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_gate.py appV2.4/tests/test_workflow_live_transports.py appV2.4/tests/test_workflow_live_preflight.py appV2.4/tests/test_workflow_live_runner.py -q
```

Expected:

```text
Live gate blocks unsafe connector actions before transport invocation.
Fake transports prove no network dispatch occurs on blocked gates.
```

## Phase 4: Live Runner Execution Contract

**Files:**

- Modify: `appV2.4/appv24/workflows/live_runner.py`
- Modify: `appV2.4/appv24/workflows/reports.py`
- Modify: `appV2.4/appv24/workflows/explain.py`
- Modify: `appV2.4/appv24/workflows/run_store.py`
- Test: `appV2.4/tests/test_workflow_live_runner.py`
- Test: `appV2.4/tests/test_workflow_explain.py`

- [ ] **Step 1: Normalize live runner event sequence**

Every live run should emit this ordered event contract:

```text
run_created
package_validation_passed
compile_passed
policy_passed
bundle_guard_passed
credential_preflight_passed
live_gate_passed
broker_preflight_passed
step_started
step_completed or step_blocked
run_completed
```

Blocked runs emit the same sequence until the failed gate, then persist the report.

- [ ] **Step 2: Require idempotency reservation before mutating transport**

For draft/production actions:

```text
Build idempotency key.
Check approval ledger decision matches key.
Write idempotency reservation event.
Invoke transport.
Write transport result event.
Persist sanitized artifact.
```

- [ ] **Step 3: Add run explain completeness**

`/workflow-run-explain` must show:

```text
mode
package fingerprint
fixture
activation id when present
gate that blocked or passed
approval request and status
idempotency key prefix
connector action
artifact paths
safe next commands
```

- [ ] **Step 4: Verify Phase 4**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_runner.py appV2.4/tests/test_workflow_explain.py appV2.4/tests/test_workflow_run_store.py -q
```

Expected:

```text
Live-read can execute only read actions.
Live-draft executes only after approval ledger is satisfied.
Live-production remains disabled unless explicitly enabled and approved.
```

## Phase 5: Triggers

Build triggers in this order: manual, schedule, poll, webhook. Webhook is last because ingress is externally reachable and needs replay protection.

**Files:**

- Modify: `appV2.4/appv24/workflows/activation.py`
- Create: `appV2.4/appv24/workflows/trigger_state.py`
- Create: `appV2.4/appv24/workflows/scheduler.py`
- Create: `appV2.4/appv24/workflows/webhook_ingress.py`
- Test: `appV2.4/tests/test_workflow_activation.py`
- Test: `appV2.4/tests/test_workflow_triggers.py`

- [ ] **Step 1: Extend activation state without enabling live modes by default**

Add stored fields:

```text
trigger_config
cursor
max_concurrency
cooldown_seconds
last_started_at
last_finished_at
consecutive_failures
```

Manual activation remains local/sandbox by default. Live activation requires explicit mode and existing live gates.

- [ ] **Step 2: Add schedule/poll state machine**

State transitions:

```text
idle -> due -> running -> succeeded -> idle
idle -> due -> running -> failed -> idle
running -> skipped when max_concurrency reached
disabled -> no-op
```

Poll rules:

```text
Store cursor.
Pass cursor to source/http read step.
Only advance cursor after successful run.
Never run mutating follow-up steps without normal approval/idempotency gates.
```

- [ ] **Step 3: Add webhook ingress design-only/local handler**

Webhook v0 is local-only:

```text
Validate activation id.
Validate package fingerprint.
Validate shared secret header or signed token.
Compute replay key.
Reject duplicate replay key.
Write webhook received event.
Start local/live-read run only through existing gates.
```

No public server or cloud ingress in this phase.

- [ ] **Step 4: Verify Phase 5**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_activation.py appV2.4/tests/test_workflow_triggers.py -q
```

Expected:

```text
Manual activation still works.
Schedule/poll does not double-run.
Webhook replay is blocked.
Live modes still require all live gates.
```

## Phase 6: Deployment Intent

Deployment intent is export-only. Do not auto-deploy infra yet.

**Files:**

- Modify: `appV2.4/appv24/workflows/iac_export.py`
- Create: `appV2.4/appv24/workflows/deployment_intent.py`
- Test: `appV2.4/tests/test_workflow_iac_export.py`
- Test: `appV2.4/tests/test_workflow_deployment_intent.py`

- [ ] **Step 1: Extend deployment intent data**

Add:

```python
@dataclass(frozen=True)
class WorkflowDeploymentIntent:
    workflow_name: str
    package_fingerprint: str
    runtime_modes: tuple[str, ...]
    env_requirements: tuple[str, ...]
    secret_refs: tuple[str, ...]
    webhook_triggers: tuple[str, ...]
    scheduler_triggers: tuple[str, ...]
    connector_scopes: dict[str, tuple[str, ...]]
    connector_targets: dict[str, tuple[str, ...]]
    approval_ids: tuple[str, ...]
    approval_policy: tuple[str, ...]
    idempotency_required_for: tuple[str, ...]
    state_requirements: tuple[str, ...]
    audit_requirements: tuple[str, ...]
    rollback_required: bool
```

- [ ] **Step 2: Export connector target requirements from contracts**

Examples:

```text
http.fetch:
  APPV24_HTTP_FETCH_ALLOWED_HOSTS
  APPV24_HTTP_FETCH_MAX_RESPONSE_BYTES

telegram.send_message:
  APPV24_TELEGRAM_ALLOWED_CHAT_IDS
  APPV24_TELEGRAM_BOT_TOKEN

gmail.create_draft:
  APPV24_GMAIL_ACCESS_TOKEN or refresh-token tuple
```

- [ ] **Step 3: Add text and JSON output**

Text output is for operators. JSON output is for future IaC compiler.

Required JSON shape:

```json
{
  "version": "appv24.deployment_intent/v1",
  "workflow_name": "name",
  "package_fingerprint": "sha256...",
  "runtime_modes": ["live-read", "live-draft"],
  "env_requirements": [],
  "secret_refs": [],
  "connector_scopes": {},
  "approval_policy": [],
  "state_requirements": [],
  "audit_requirements": []
}
```

- [ ] **Step 4: Verify Phase 6**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_iac_export.py appV2.4/tests/test_workflow_deployment_intent.py -q
```

Expected:

```text
Valid packages export deployable intent.
Invalid packages cannot export intent.
Intent includes env, secrets, scopes, approvals, idempotency, trigger, audit, and rollback requirements.
No infra is provisioned.
```

## End-to-End Verification

After all phases:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_*.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests -q
git diff --name-only | rg 'appV2.4/appv24/(agent|ai/stream.py|ai/types.py|ai/validation.py|compaction|coding_agent/session_store.py|app.py)' && exit 1 || true
```

Manual TUI verification:

```text
1. Run workflow-designer-lite with qwen/qwen3-coder-next.
2. Ask for a workflow using source.fetch_items, http.fetch, llm.extract_structured, policy.check, gmail.create_draft.
3. Save package.
4. Run local happy and rejection fixtures.
5. Run live-read readiness and preflight.
6. Run approval prep.
7. Verify live-draft is blocked until approval.
8. Verify explain shows the exact safe next command.
9. Do not execute live-production unless Lewis explicitly requests it.
```

## Commit Strategy

Use one commit per phase:

```text
fix(appv24): tighten workflow secret false positives
feat(appv24): add workflow connector contracts
feat(appv24): harden workflow live gate
feat(appv24): add safe workflow http fetch
feat(appv24): normalize live runner audit events
feat(appv24): add workflow trigger state
feat(appv24): extend workflow deployment intent
```

Do not release GHCR or npm from this plan.
