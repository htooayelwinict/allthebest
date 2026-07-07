# appv24 Low-Risk Connector Pack Production Gate Design

Date: 2026-07-07
Status: Implemented locally; external Gmail/Telegram live calls require operator credentials
Scope: appV2.4 / appv24 workflow profile only

## Purpose

The next production gate is to prove that appv24 can execute a small set of real connector actions through strict contracts, live preflight, approval ledger checks, and sanitized run artifacts.

This is not the general automation platform yet. It is the narrow bridge from validated workflow packages to controlled external side effects.

## Current Baseline

The workflow layer already has the important primitives:

- `appV2.4/appv24/workflows/connector_contracts.py` defines action vocabulary, scopes, secrets, live modes, side-effect class, approval class, allowed outputs, target policy, idempotency fields, and retry class.
- `appV2.4/appv24/workflows/live_transports.py` builds env-gated direct transports for live-read, live-draft, and live-production modes.
- `appV2.4/appv24/workflows/live_preflight.py` checks package validation, compile, policy, bundle guard, credential refs, live gate, transport availability, target policy, and approval state before real execution.
- `appV2.4/appv24/workflows/live_runner.py` runs the same gate sequence and persists reports, events, traces, approvals, and issues.
- `appV2.4/appv24/workflows/approval_gate.py` binds approvals to package fingerprint, step/action/idempotency key, and payload hash.
- `appV2.4/appv24/workflows/run_store.py` writes append-only local run artifacts and rejects raw secret-looking values.
- `.env.example` now documents the operator-side env variables needed for local live tests.

The design should harden this existing path. It must not introduce a second workflow runtime.

## External API Facts

Gmail draft creation should use Gmail API `users.drafts.create`. The official Gmail reference says it creates a draft with the `DRAFT` label and accepts the `https://www.googleapis.com/auth/gmail.compose` OAuth scope, among others.

Telegram approved-target send should use Telegram Bot API `sendMessage`. The official Bot API says `sendMessage` sends text messages and requires `chat_id` and `text`; successful calls return a sent message object.

Sources:

- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create
- https://core.telegram.org/bots/api#sendmessage

## Non-Goals

- Do not add Gmail send.
- Do not add broad Telegram bot management.
- Do not add cloud/IaC deployment in this gate.
- Do not add MCP connector execution yet.
- Do not touch red-zone kernel files.
- Do not weaken validator, live gate, approval, idempotency, or secret redaction.
- Do not treat a real Telegram send to any arbitrary chat as acceptable.

## Zone Boundary

Green-zone work:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_*.py
docs/appv24/
docs/superpowers/specs/
.env.example
```

Yellow-zone work only if a command wrapper is missing:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
```

Red-zone work:

```text
none
```

The production gate must be achievable without changes to agent loop, provider streaming, compaction, session store, or app kernel files.

## Approaches Considered

### A. Direct Brokered Connector Pack

Appv24 owns the connector contracts and calls provider APIs through env-gated transports.

Pros:

- Fits the current workflow architecture.
- Keeps action vocabulary, policy, credentials, approval, and ledger in one place.
- Easy to fake in tests with injected clients.
- Strongest path for proving safety before expansion.

Cons:

- Each connector transport needs careful maintenance.
- OAuth and API quirks stay inside appv24.

Recommendation: use this approach for this gate.

### B. MCP Connector Backends

Appv24 treats Gmail, Telegram, and other systems as MCP backends.

Pros:

- Good long-term extension story.
- Can reuse external connector ecosystems.

Cons:

- Harder to prove side-effect safety until the appv24 contract boundary is mature.
- Permission mistakes can hide in external tools.
- Harder to bind payload approvals to exact provider calls.

Recommendation: defer. Add MCP later as an adapter type behind the same `ConnectorContract`.

### C. Human-Only Draft Export

Appv24 generates draft payload files and asks the user to manually paste/send.

Pros:

- Very safe.
- Useful fallback when credentials are unavailable.

Cons:

- Does not prove live connector execution.
- Avoids the production gate instead of passing it.

Recommendation: keep as fallback only.

## Connector Pack V1

The low-risk pack should include:

```text
read-only:
  http.fetch
  source.fetch_items
  source.fetch_document
  github.list_dependabot_alerts

draft-only:
  gmail.create_draft

approved-target production:
  telegram.send_message
```

This pack intentionally mixes already-proven read-only actions with the first two controlled side-effect classes.

## Contract Requirements

Every live action must have a `ConnectorContract` with:

```text
action_ref
connector_id
action_id
input_schema
output_schema
allowed_outputs
required_scopes
secrets
side_effect_class
approval_class
live_modes
idempotency_key_fields
retry_class
target_policy
prompt_summary
adapter
execution_mode
```

Hard rules:

- Unknown action refs fail validation or compile before live preflight.
- Connector IDs require matching `APPV24_<CONNECTOR>_CREDENTIAL_REF`.
- Connector scopes require exact `APPV24_<CONNECTOR>_SCOPES`.
- Wildcard scopes are rejected.
- Secrets come only from env or credential providers, never workflow YAML.
- Outputs are filtered to `allowed_outputs`.
- Mutating actions require idempotency keys.
- Approval decisions are valid only for the same package fingerprint and payload hash.
- Approval decisions become stale if payload, package, step, action, or idempotency key changes.

## Gmail Draft Gate

Action:

```text
gmail.create_draft
```

Mode:

```text
live-draft
```

Required env:

```text
APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true
APPV24_GMAIL_CREDENTIAL_REF=<opaque-ref>
APPV24_GMAIL_SCOPES=gmail.compose
APPV24_GMAIL_ACCESS_TOKEN=<token>
```

Alternative token flow:

```text
APPV24_GMAIL_REFRESH_TOKEN=<refresh-token>
APPV24_GMAIL_CLIENT_ID=<client-id>
APPV24_GMAIL_CLIENT_SECRET=<client-secret>
APPV24_GOOGLE_OAUTH_TOKEN_URL=https://oauth2.googleapis.com/token
```

Execution sequence:

```text
load package
validate package
compile to IR
evaluate policy
inspect bundle guard
resolve fixture
preflight credential refs
preflight live-draft transport
resolve input refs
compute idempotency key
compute payload hash
create or load approval request
require approved approval_run_id
call Gmail drafts.create
persist draft_id/thread_id only
persist sanitized report, events, trace, approvals
```

Expected output:

```text
draft_id
thread_id
artifact_path
```

Blocked cases:

- live-draft gate disabled
- missing credential ref
- missing `gmail.compose`
- wildcard scope
- no usable Gmail token
- missing approval
- stale approval hash
- malformed recipient, subject, or body
- Gmail API error
- raw token appears in any artifact

## Telegram Approved-Target Gate

Action:

```text
telegram.send_message
```

Mode:

```text
live-production
```

Required env:

```text
APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true
APPV24_TELEGRAM_CREDENTIAL_REF=<opaque-ref>
APPV24_TELEGRAM_SCOPES=telegram:send
APPV24_TELEGRAM_BOT_TOKEN=<bot-token>
APPV24_TELEGRAM_ALLOWED_CHAT_IDS=<comma-separated-chat-ids>
```

Execution sequence:

```text
load package
validate package
compile to IR
evaluate policy
inspect bundle guard
resolve fixture
preflight credential refs
preflight live-production transport
resolve input refs
verify chat_id is in APPV24_TELEGRAM_ALLOWED_CHAT_IDS
compute idempotency key
compute payload hash
create or load approval request
require approved approval_run_id
call Telegram sendMessage
persist message_id/chat_id only
persist sanitized report, events, trace, approvals
```

Expected output:

```text
message_id
chat_id
artifact_path
```

Blocked cases:

- live-production gate disabled
- missing credential ref
- missing `telegram:send`
- wildcard scope
- missing bot token
- missing allowed chat IDs
- chat_id outside allowlist
- missing approval
- stale approval hash
- empty text
- message body above Telegram limit
- Telegram API error
- raw token appears in any artifact

## Approval Ledger Design

Approval requests are generated before mutating live execution. The approval preview should include:

```text
run_id
approval_request_id
package_fingerprint
step_id
connector_action
approval_id
idempotency_key
payload_hash
payload_preview_path
allowed_outputs
status=pending
```

Approval decisions should include:

```text
approval_request_id
approved
status
package_fingerprint
decided_by
idempotency_key
payload_hash
payload_preview_path
```

The runner must not trust an approval by `step_id` alone. It must match:

```text
package_fingerprint
step_id
connector_action
idempotency_key
payload_hash
approved=true
status=approved
```

## Data Flow

```text
workflow.yaml/connectors.yaml/permissions.yaml
  -> loader
  -> validator
  -> compiler
  -> policy
  -> bundle_guard
  -> credential_refs
  -> live_gate
  -> approval_gate
  -> live_broker
  -> live_transport
  -> run_store
```

The model is not trusted at execution time. It can draft the package, but execution is controlled by deterministic validators, policies, preflight checks, and broker contracts.

## Command Shape

Existing command style should stay:

```text
/workflow-live-ready live-draft <package> <fixture> --allow gmail.create_draft
/workflow-approval-prep <package> <fixture>
/workflow-approval-approve <run-id> <approval-request-id>
/workflow-run-live-draft <package> <fixture> --allow gmail.create_draft --approval-run-id <run-id>

/workflow-live-ready live-production <package> <fixture> --allow telegram.send_message
/workflow-approval-prep <package> <fixture>
/workflow-approval-approve <run-id> <approval-request-id>
/workflow-run-live-production <package> <fixture> --allow telegram.send_message --approval-run-id <run-id>
```

If a command already exists, harden it. Do not create duplicate command families.

## QA Matrix

Unit tests:

- contract catalog contains the V1 actions
- contract schemas expose only allowed input/output fields
- credential refs require exact env names and scopes
- wildcard scopes are rejected
- Gmail transport creates draft with sanitized outputs
- Gmail refresh-token flow does not leak token material
- Telegram transport blocks non-allowlisted chat IDs
- Telegram transport rejects empty text
- Telegram transport filters response outputs
- approval index rejects stale package fingerprints
- approval index rejects stale payload hashes
- idempotency key changes when approved payload changes
- run store rejects secret-looking values

Integration tests:

- live-ready blocks when env gate is disabled
- live-ready blocks when credential ref is missing
- live-ready blocks when scope is missing
- approval prep writes pending approval request
- live run blocks without approval
- live run blocks with stale approval
- live run succeeds with approved Gmail draft using fake HTTP client
- live run succeeds with approved Telegram send using fake HTTP client and allowlisted chat ID
- live run writes `run.json`, `events.jsonl`, `approvals.json`, `trace.json`, and `report.json`
- artifacts contain no provider secrets

Manual live tests:

- Gmail draft-only test against a real mailbox, no send
- Telegram send test only to an approved admin/test chat ID
- TUI human-style run for both paths
- Failure-path TUI run for missing approval and non-allowlisted Telegram target

## Production Gate Definition

This gate is passed only when all of these are true:

```text
validation blocks unknown live actions
preflight blocks missing env gates
preflight blocks missing credential refs
preflight blocks missing scopes
preflight blocks wildcard scopes
preflight blocks unconfigured live transports
approval prep produces payload-bound approval requests
live run blocks without approval
live run blocks stale approval
Gmail can create a draft and never send
Telegram can send only to allowlisted target IDs
run artifacts are sanitized
TUI command path works human-style
full appV2.4 pytest suite passes
no red-zone files changed
yellow-zone changes, if any, are thin wrappers with tests
```

## Rollback

Rollback must be simple:

- Disable all live behavior by setting live-mode gates to false or empty.
- Remove connector allowlist flags from live run commands.
- Revert only `appV2.4/appv24/workflows/`, workflow tests, docs, `.env.example`, and optional CLI/TUI wrappers.
- No data migration is required because run artifacts are local append-only files.

## Recommendation

Proceed with the direct brokered connector pack. Treat Gmail draft and Telegram approved-target send as the first production-grade side-effect proof. Keep every safety gate deterministic. Do not involve the LLM in final execution decisions.

## Implementation Notes

Implemented in the appv24 workflow layer without red-zone changes:

- Telegram production transport registration now requires `APPV24_TELEGRAM_ALLOWED_CHAT_IDS`.
- Direct `telegram.send_message` transport blocks when the allowed-chat allowlist is missing.
- Direct `telegram.send_message` transport blocks text longer than 4096 characters before HTTP dispatch.
- Live gate and live-production preflight now default-deny missing Telegram target allowlists.
- Operator hints, docs, and `.env.example` include the Telegram approved-target allowlist requirement.
- `.env.example` is explicitly unignored so it can be versioned without permitting real `.env` files.

Verification:

```text
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
1647 passed in 52.32s
```
