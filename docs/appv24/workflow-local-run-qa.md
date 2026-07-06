# appv24 Workflow Local Run QA

Date: 2026-07-05
Scope: appV2.4 workflow local-run execution only

## Boundary

These scenarios verify that a saved workflow package can execute through the local runner and produce auditable local artifacts.

Local run is not live automation. It must not call Gmail, Telegram, ticketing, GitHub, cloud, shell, MCP, webhook, or browser services. Connector actions are routed through the local broker and write files only under the run artifact directory.

## TUI And Plain CLI Commands

Planned explicit form:

```text
/workflow-run <saved-package> <fixture> --mode local --approve-all
```

Convenience local-only form:

```text
/workflow-run-local <saved-package> [fixture]
```

Connector sandbox form:

```text
/workflow-run-sandbox <saved-package> [fixture]
```

All forms run `WorkflowRunMode.LOCAL` only in TUI and plain CLI. Live
production remains blocked by the runner.

## Scenario 1: Gmail Draft Reply

Golden package:

```text
appV2.4/appv24/workflows/examples.py
  gmail_draft_reply
```

Expected happy-path local run:

```text
status: passed
terminal_step: done
brokered actions:
  gmail.create_draft -> simulated
artifacts:
  local-draft-create_gmail_draft.json
```

Expected rejection-path local run:

```text
status: passed
terminal_step: done
brokered actions: none
artifacts: none
```

## Scenario 2: Authorized Telegram Support Ticket

Golden package:

```text
appV2.4/appv24/workflows/examples.py
  telegram_support_ticket
```

Expected happy-path local run:

```text
status: passed
terminal_step: done
brokered actions:
  ticket.create_draft -> simulated
  telegram.send_message -> simulated
artifacts:
  local-ticket-create_ticket_draft.json
  local-telegram-notify_admin.json
```

Expected rejection-path local run:

```text
status: passed
terminal_step: rejected
brokered actions: none
artifacts: none
```

## Run Record

A successful local run writes:

```text
.workflow-runs/run-<id>/
  run.json
  report.json
  trace.json
  approvals.json
  approval_decisions.jsonl
  events.jsonl
  artifacts/
```

`events.jsonl` and `approval_decisions.jsonl` are append-only. `report.json`,
`trace.json`, and `approvals.json` are JSON snapshots of the final local run
result.

`approvals.json` contains both approval summaries and approval request records.
Pending approval requests include the run id, package fingerprint, step id,
connector action, allowed outputs, idempotency key fields, and hashed
idempotency key. This gives later approval resume/live execution work a concrete
artifact to verify against.

Approval decisions are appended to `approval_decisions.jsonl` by looking up a
persisted approval request id from `approvals.json`. Unknown request ids are
rejected, and decisions are written only after the decision package fingerprint
matches the request package fingerprint.

Operator approval commands append these decisions without running the model or
executing connectors:

```text
/workflow-approve <package> <run-id> <approval-request-id>
/workflow-deny <package> <run-id> <approval-request-id>
```

Both commands compute the current package fingerprint from disk before writing
the decision, so package drift is rejected by the same stale-fingerprint guard
used by the approval ledger.

After a local run has produced and approved a pending approval request, operators
can run a no-side-effect live-draft preflight:

```text
/workflow-live-draft-preflight <package> <fixture> <approval-run-id> --allow <connector.action>[,<connector.action>]
```

This checks readiness only. It does not create another run directory and does
not invoke connector transports.

Brokered actions with declared `idempotency.key_fields` include a stable hashed
`idempotency_key` in the report and broker action events. The key is audit
material for later live execution; it does not expose raw field values.

## Connector Enforcement

The model can draft connector intent, but the local runner only executes connector actions after these gates:

```text
package loads
schema and semantic validation pass
IR compile passes
policy passes
fixture exists
approval policy is satisfied
pending approval requests are persisted before blocked approval waits
idempotency key material is recorded for brokered mutating/draft actions
local broker provider exists
outputs are filtered to allowed fields
provider output names match at least one declared allowed field
```

Unknown catalog actions are blocked during policy evaluation. Local provider output mismatches and live-production runs are blocked before any external side effects.

## Connector Sandbox Fixtures

Saved sandbox fixtures can force connector-level behavior without real API calls:

```text
connector_timeout.yaml -> blocked timeout at the selected connector step
bad_llm_output.yaml    -> blocked output allowlist mismatch
missing_field.yaml     -> blocked missing provider field
```

The first repeatable sandbox fixtures are in:

```text
examples/appv24/workflows/financial-catalyst-alerts/dry_run_fixtures/
```

## Offline Verification

Focused tests:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider \
  appV2.4/tests/test_workflow_runner.py \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_run_local_runs_saved_package \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_run_accepts_planned_local_command_shape
```

Full verification:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```
