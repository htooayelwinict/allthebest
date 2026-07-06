# appv24 Workflow Run-Live Path

appv24 workflow execution moves through staged modes:

1. `preview`
2. `local`
3. `staged`
4. `credential-preflight`
5. `live-read`
6. `live-draft`
7. `live-production`

Live production is disabled by default.

Before any live mutation, appv24 requires:

- package validation
- compile success
- policy success
- exact capability catalog match
- package fingerprint lock
- credential reference preflight
- explicit approval for current package fingerprint
- idempotency key material
- rollback coverage
- connector action allowlist
- prior dry-run or local run for the same package fingerprint

Normal CI uses fake transports only. Real provider checks are manual-gated.

`LiveWorkflowBroker` accepts only `live-read`, `live-draft`, and
`live-production` modes. Unknown broker modes fail at construction, and
`production_enabled=True` is valid only for `live-production`. Each live runner
also verifies the injected broker mode before transport invocation. Non-blocked
transport results must report the same live status as the broker mode, otherwise
the broker returns a blocked result with no outputs. Blocked transport results
also return no outputs even if the provider included fields that match
`outputs.allowed`.

Operators can inspect live-run readiness without creating a run directory,
calling the model, or invoking connector transports:

```text
/workflow-live-ready <mode> <package> <fixture> [approval-run-id] --allow <connector.action>[,<connector.action>]
```

The readiness report wraps the mode-specific preflight and adds safe setup
hints for credential-reference env vars, transport env vars, configured
transport action names, approval-run requirements, and the next run command.
It must not print raw API keys, OAuth tokens, bot tokens, credential reference
values, or provider responses.

Readiness also checks the default exported runtime bundle at
`<package>/.workflow-bundle`. A missing bundle is surfaced as an operator next
step:

```text
/workflow-export-bundle <package>
```

If a bundle exists but its manifest, checksums, or source package fingerprint
are stale, readiness is blocked until the bundle is re-exported. Live-run
commands also enforce the same stale-bundle block before creating run artifacts
or invoking broker transports. This keeps operator handoff artifacts aligned
with the package that live commands are about to inspect.

Operators can also render a safe `.env` setup template before readiness checks:

```text
/workflow-live-env-template <mode> <package> --allow <connector.action>[,<connector.action>]
```

This command loads package permissions and the requested live mode, then prints
only placeholder values for credential references, scopes, and built-in
transport gates. It does not read current env values, create run artifacts, call
the model, or invoke connector transports.

Operators can ask appv24 to classify a package into the live/local phases that
are actually available today:

```text
/workflow-live-plan <package> [fixture]
```

This command loads, validates, compiles, and policy-checks the package, then
prints live-read actions, local/simulated actions, approval-gated draft or
production actions, known transport gaps, an ordered phase sequence through the
fixture-selected path, and the next safe command sequence. It is read-only: it
does not inspect env values, create approval requests, call the model, create
run artifacts, or invoke connector transports. For mixed workflows, the plan is
intentionally conservative. It does not imply that a source + LLM + policy +
notification workflow can run as one live transaction; it shows the live-read
source phase, the local/simulated intelligence phase, and the later
approval-gated mutation phase separately.

Before live-draft or live-production, operators can create pending approval
requests with a local approval-prep run:

```text
/workflow-run-approval <package> [fixture]
```

This runs local mode with explicit approvals, persists pending approval request
records in `.workflow-runs/<run-id>/approvals.json`, and stops before the
brokered action. The resulting run id is the approval run id used by
`/workflow-approvals`, `/workflow-approve`, `/workflow-deny`, readiness checks,
and live-draft/live-production runs. It does not call the model, live
transports, or external providers.

## Implemented Live-Read V0 Boundary

`live-read` is the first live-capable execution mode. It can execute read-only
connector actions through injected broker transports after all preflight gates
pass.

Required gates:

- package validation passes
- IR compilation passes
- policy evaluation passes
- credential reference preflight passes
- the injected broker mode is exactly `live-read`
- connector action is listed in the request allowlist
- workflow step and connector action are read-only
- broker output matches declared `outputs.allowed`

Live-read records the same run artifacts as local execution:

- `run.json`
- `events.jsonl`
- `trace.json`
- `approvals.json`
- `report.json`

When a live-read run completes at least one brokered read action and then
reaches a non-live-read step, the run status is `paused`, not `blocked`. This
is the intended handoff from the live-read prefix to later local, live-draft, or
live-production phases. Credential, validation, allowlist, catalog, policy, and
transport failures remain hard `blocked` states.

Paused live-read runs also persist `handoff.json` beside `report.json`. The
handoff records the completed read steps, the next step, the next phase, and
available output refs such as `fetch_news.items`. `/workflow-run-explain` reads
that artifact and recommends the next safe phase commands instead of telling
the operator to rerun the same live-read prefix.

The workflow runner can resume local execution from that handoff. The resume
path creates a new local run, starts at the handoff `next_step`, and injects
prior live-read outputs into the fixture under `handoff.outputs`. This is still
dry-run/local execution: it does not call external mutation transports. It is
the bridge needed before a multi-phase orchestrator can chain live-read,
local/model/policy, approval, and live-draft/live-production phases.

The first multi-phase orchestrator path chains `live-read` to local resume and
stops at the approval ledger. Its status is `approval-required` when the local
resume run creates a pending approval request. It does not auto-approve or run
live-draft/live-production actions. That keeps the first orchestrated path
side-effect safe while proving the phase handoff and approval-prep sequence.

Operators can invoke that first orchestrated phase chain directly:

```text
/workflow-run-live-read-to-approval <package> <fixture> --allow <connector.action>[,<connector.action>]
```

The command uses the live-read transport registry for the read prefix, resumes
locally from the handoff, and prints the live-read run id, local run id,
approval run id, phase statuses, issues, and next approval commands. It does
not approve requests or run live-production.

After an operator records an approval decision, the phase runner can continue
from that approved local ledger into the live-production phase. This
continuation does not bypass the live-production runner. It still requires the
same package fingerprint, local approval provenance, granted decision,
credential-reference preflight, action allowlist, production-enabled catalog
entry, idempotency material, `live-production` broker mode, and explicitly
enabled production broker before any transport is invoked.

Operators can check live-read readiness without creating a run or invoking any
connector transport:

```text
/workflow-live-read-preflight <package> <fixture> --allow <connector.action>[,<connector.action>]
```

The command checks package validation, compile, policy, fixture availability,
credential references, connector allowlist, catalog live-read eligibility, and
read-only behavior. CLI/TUI preflight also checks that the allowlisted action
has a configured env-gated live-read transport. It does not call the provider.
A failed preflight is a hard stop for later live-read execution. When a
configured transport is missing, the report includes the required environment
variable names, never their values.

Operators can run the same live-read gates and persist a run record:

```text
/workflow-run-live-read <package> <fixture> --allow <connector.action>[,<connector.action>]
```

Both live-read commands load credential references from environment variables
and never read or print raw secrets:

```text
APPV24_<CONNECTOR>_CREDENTIAL_REF=<opaque-reference>
APPV24_<CONNECTOR>_SCOPES=<scope>[,<scope>]
```

Credential preflight failures include safe operator hints with the required
environment variable names and scope names. They do not include raw credential
reference values, OAuth tokens, bot tokens, API keys, or provider responses.

By default this command uses an empty live transport registry. It can create a
`live-read` run directory and prove validation, credential, allowlist, catalog,
policy, and output gates, but it blocks at connector invocation unless live-read
transports are explicitly enabled.

The first live-read transport is `github.list_dependabot_alerts`. It remains
off by default and registers only when the operator provides:

```text
APPV24_WORKFLOW_LIVE_READ_ENABLED=true
APPV24_GITHUB_TOKEN=<token>
```

The workflow package still controls what the transport is allowed to return via
`connectors.yaml` `outputs.allowed`, and the broker filters outputs to that
allowlist before reports are persisted. The GitHub token is used only in the
transport request header. It is not written to `run.json`, `events.jsonl`,
`trace.json`, `approvals.json`, `report.json`, or terminal output.

`github.list_dependabot_alerts` resolves the repository from, in order:

```text
steps[].with.repository
steps[].with.repo
dry_run_fixtures/<fixture>.yaml input.repository
dry_run_fixtures/<fixture>.yaml input.repo
```

The value must be `owner/repo`. Optional filters can be supplied in
`steps[].with`: `state`, `severity`, `ecosystem`, `package`, `manifest`,
`scope`, `sort`, `direction`, and `per_page`. `steps[].with` values can be
literal strings/numbers or `{"inputRef": "<fixture-input-name>"}` references.
`per_page` is clamped to 100.

The generic source live-read transports are `source.fetch_items` and
`source.fetch_document`. They remain off by default and register only when the
operator provides:

```text
APPV24_WORKFLOW_LIVE_READ_ENABLED=true
APPV24_SOURCE_BASE_URL=<https-source-base-url>
```

The workflow package still requires normal credential-reference preflight, for
example:

```text
APPV24_SOURCE_CREDENTIAL_REF=<opaque-reference>
APPV24_SOURCE_SCOPES=source:read
```

`APPV24_SOURCE_BASE_URL` is the operator-controlled HTTP(S) boundary for source
reads. A workflow can provide a relative `path`, `endpoint`, `url`, or
`source_url`, or an absolute URL that stays under that base URL. Off-base
absolute URLs and `..` path traversal are blocked before any HTTP request.

`source.fetch_items` returns only slim item records plus `source_count`.
`source.fetch_document` returns only slim document fields. Provider raw payloads,
unknown fields, and configured base URL values are not written to reports unless
they are explicitly part of the slim public source output.

`run.json` is the provenance manifest. It records the workflow name,
fingerprint, mode, fixture, package path, approval stance, connector allowlist,
and activation id when the run came from a managed activation.
For live-draft and live-production runs, explanation output also renders the
prior `approval_run_id` so the live action can be audited back to the local run
that produced the approval request.

Brokered action reports and events record hashed `idempotency_key` values when
an action declares `idempotency.key_fields`. This is required audit material
before any future live mutation path.

Local runs that stop for human approval persist approval request records in
`approvals.json`. Each request pins the package fingerprint, action, approval
id, allowed outputs, idempotency fields, and hashed idempotency key so stale or
ambiguous approval resumes can be rejected later.

Approval grants and denials are recorded append-only in
`approval_decisions.jsonl` by resolving the persisted approval request id from
`approvals.json`. Unknown request ids are rejected, and a decision whose package
fingerprint does not match the request is rejected before it is persisted.

Operators can write those approval decisions from CLI or TUI without invoking
the model or executing connectors:

```text
/workflow-run-approval <package> [fixture]
/workflow-approvals <package> <run-id>
/workflow-approve <package> <run-id> <approval-request-id>
/workflow-deny <package> <run-id> <approval-request-id>
```

`/workflow-approvals` lists the approval request ids, current decision status,
step id, connector action, approval id, and hashed idempotency key for a prior
local run. It is read-only and does not call the model or any connector.

The command target `<run-id>` is the prior local run that produced the pending
approval request. appv24 reads that run's `run.json` manifest and rejects it
unless `mode` is `local`. Later `live-draft` requests pass that value as
`approval_run_id`.

Live-read does not:

- execute mutating connector actions
- grant approvals
- send messages
- create tickets or issues
- provision infrastructure
- call live-production transports
- bypass credential, catalog, policy, or output checks

## Implemented Live-Draft V0 Boundary

`live-draft` is the first approval-gated mutation-capable mode. It can execute
cataloged draft-only connector actions through injected broker transports after
all live-read gates plus approval-ledger gates pass.

Required additional gates:

- request mode is exactly `live-draft`
- the injected broker mode is exactly `live-draft`
- `approval_run_id` points to a prior local run in the same run store
- the prior run's `run.json` manifest has `mode: local`
- the prior run has an append-only approved decision for the same package fingerprint
- the approved decision matches the current step id, connector action, and hashed idempotency key
- the connector action is listed in the request allowlist
- the built-in connector catalog marks the action as `live_draft_eligible`
- the action declares idempotency key fields

Live-draft currently supports draft/create actions such as `gmail.create_draft`
through injected transports in tests. It still does not send messages, publish
content, create final tickets/issues, provision infrastructure, or call
`live-production`.

Denied approvals, stale package fingerprints, unknown approval runs, missing
idempotency material, and non-draft-eligible mutations block before broker
transport invocation.

Operators can check live-draft readiness without creating a run or invoking any
connector transport:

```text
/workflow-live-draft-preflight <package> <fixture> <approval-run-id> --allow <connector.action>[,<connector.action>]
```

The command loads credential references from environment variables. It never
reads or prints raw secrets:

```text
APPV24_<CONNECTOR>_CREDENTIAL_REF=<opaque-reference>
APPV24_<CONNECTOR>_SCOPES=<scope>[,<scope>]
```

Credential preflight failures include safe operator hints with the required
environment variable names and scope names. They do not include raw credential
reference values, OAuth tokens, client secrets, access tokens, or provider
responses.

Examples:

```text
APPV24_GMAIL_CREDENTIAL_REF=gmail-prod
APPV24_GMAIL_SCOPES=gmail.compose
```

The preflight checks package validation, compile, policy, fixture availability,
credential references, approval ledger decisions, connector allowlist,
catalog live-draft eligibility, idempotency material, and configured env-gated
live-draft transport presence. It does not call the provider or create a draft.
A failed preflight is a hard stop for later live-draft execution. When a
configured transport is missing, the report includes the required environment
variable names, never their values.

Operators can also run the same approved live-draft gates and persist a run
record:

```text
/workflow-run-live-draft <package> <fixture> <approval-run-id> --allow <connector.action>[,<connector.action>]
```

By default this command uses an empty live transport registry. It can create a
`live-draft` run directory and prove validation, credential, approval,
allowlist, idempotency, and catalog gates, but it blocks at connector invocation
unless live-draft transports are explicitly enabled.

The first live-draft transport is `gmail.create_draft`. It remains off by
default and registers only when the operator enables live-draft plus one Gmail
token mode.

Static access-token mode:

```text
APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true
APPV24_GMAIL_ACCESS_TOKEN=<oauth-access-token>
```

Refresh-token mode:

```text
APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true
APPV24_GMAIL_REFRESH_TOKEN=<oauth-refresh-token>
APPV24_GMAIL_CLIENT_ID=<oauth-client-id>
APPV24_GMAIL_CLIENT_SECRET=<oauth-client-secret>
```

Optional:

```text
APPV24_GMAIL_API_BASE_URL=<base-url>
APPV24_GOOGLE_OAUTH_TOKEN_URL=https://oauth2.googleapis.com/token
```

If both token modes are present, the static access token wins and no OAuth
refresh call is made. If refresh-token mode is used, the transport exchanges the
refresh token for an access token during live-draft run invocation. Preflight
only checks that the env-gated transport can be configured; it does not call the
OAuth token endpoint.

`gmail.create_draft` creates an unsent Gmail draft through Gmail
`drafts.create`. It does not call `drafts.send` and does not send messages. The
transport builds an RFC 2822 MIME message, base64URL encodes it into
`message.raw`, and posts to `/gmail/v1/users/me/drafts`.

The transport resolves draft fields from `steps[].with` first, then the selected
fixture's `input` map. `steps[].with` values can be literal strings or
`{"inputRef": "<fixture-input-name>"}` references.

Required:

```text
to or recipient
subject
body
```

Optional:

```text
from
cc
bcc
thread_id
```

The workflow package still controls what the transport is allowed to return via
`connectors.yaml` `outputs.allowed`, and the broker filters outputs to that
allowlist before reports are persisted. Gmail access tokens, refresh tokens,
client secrets, and raw OAuth responses are used only inside the transport. They
are not written to `run.json`, `events.jsonl`, `trace.json`, `approvals.json`,
`report.json`, or terminal output. The transport returns only `draft_id` and
`thread_id` when Gmail returns them.

## Implemented Live-Production V0 Boundary

`live-production` remains disabled by default. V0 enables one exact production
connector action only:

```text
telegram.send_message
```

No other mutating connector is production enabled in this release path.

Required additional gates:

- request mode is exactly `live-production`
- the injected broker mode is exactly `live-production`
- `approval_run_id` points to a prior local run in the same run store
- the prior run's `run.json` manifest has `mode: local`
- the prior run has an append-only approved decision for the same package fingerprint
- the approved decision matches the current step id, connector action, and hashed idempotency key
- the connector action is listed in the request allowlist
- the built-in connector catalog marks the action as `live_production_enabled`
- the action declares idempotency key fields
- the live-production broker is explicitly constructed with production enabled
- the env-gated live-production transport registry contains the action

Operators can check production readiness without creating a run or invoking any
connector transport:

```text
/workflow-live-production-preflight <package> <fixture> <approval-run-id> --allow <connector.action>[,<connector.action>]
```

If the env-gated transport is missing, the preflight issue includes the safe
operator hint:

```text
required env: APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, APPV24_TELEGRAM_BOT_TOKEN
```

The hint names required variables only. It must not print raw bot tokens,
credential references, OAuth tokens, or provider responses.

Operators can run the same approved production gates and persist a run record:

```text
/workflow-run-live-production <package> <fixture> <approval-run-id> --allow <connector.action>[,<connector.action>]
```

CLI and TUI both route this command through the approved phase-runner
continuation. The command keeps the existing action-level live-production run
output, while the internal phase report records which approved local ledger run
authorized the production phase.

The first production transport is `telegram.send_message`. It remains off by
default and registers only when the operator enables live-production and
provides a Telegram bot token:

```text
APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true
APPV24_TELEGRAM_BOT_TOKEN=<bot-token>
```

Optional:

```text
APPV24_TELEGRAM_API_BASE_URL=https://api.telegram.org
```

Credential references are still loaded separately and never print raw secrets:

```text
APPV24_TELEGRAM_CREDENTIAL_REF=telegram-prod
APPV24_TELEGRAM_SCOPES=telegram:send
```

Credential preflight failures name the missing credential-reference variables
and required scopes only. They must not print Telegram bot tokens, credential
reference values, or provider responses.

`telegram.send_message` uses the Telegram Bot API `sendMessage` method. It
resolves fields from `steps[].with` first, then the selected fixture's `input`
map. `steps[].with` values can be literal strings/booleans or
`{"inputRef": "<fixture-input-name>"}` references.

Required:

```text
chat_id, admin_chat_id, or recipient
text, message_text, or body
```

Optional:

```text
parse_mode
disable_notification
protect_content
```

The workflow package still controls what the transport is allowed to return via
`connectors.yaml` `outputs.allowed`, and the broker filters outputs to that
allowlist before reports are persisted. Telegram bot tokens are used only in
the transport request URL. They are not written to `run.json`, `events.jsonl`,
`trace.json`, `approvals.json`, `report.json`, or terminal output. The
transport returns only `message_id` and `chat_id` when Telegram returns them.

## Intelligence Node Boundary

Workflow intelligence nodes remain inside the same execution boundary.

- `source.*` actions are read-only and must be allowlisted before live-read.
- `llm.*` actions are typed transforms, not autonomous agent loops.
- `llm.*` steps must use exactly one compact `output_contract` or inline `output_schema`.
- `policy.check` is deterministic and must declare `with.checks`.
- `notification.create_draft` is a local/brokered draft action and still requires policy, approval, idempotency, and output checks before any live mutation path.

Financial catalyst alerts are an example package built on generic source, LLM, policy, and notification nodes. They must stay informational and must not generate trading advice or execute trades.
