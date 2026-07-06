# appv24 Workflow Approval List Command Design

## Intent

Make the approval step usable from CLI/TUI without requiring operators to open `.workflow-runs/<run-id>/approvals.json` manually. This improves the real live-draft path because `/workflow-run-live-draft` requires an approved prior local run, and `/workflow-approve` requires an approval request id.

## Boundary

This is a read-only operator convenience slice.

- Adds an approval-list helper and command.
- Does not grant or deny approvals.
- Does not invoke live transports.
- Does not change approval ledger semantics.
- Does not add live-production.
- Does not modify red-zone/kernel files.

## Command

```text
/workflow-approvals <package> <run-id>
```

Output:

```text
Workflow approvals: <run-id>
- <approval_request_id> <status> <step_id> <connector_action> approval=<approval_id> idempotency=<idempotency_key>
```

Status is derived from the append-only approval decision ledger:

- `pending` when there is no matching decision
- `approved` when the latest matching decision is approved
- `denied` when the latest matching decision is denied

The output includes idempotency keys because live-draft approval matching depends on them, but it does not include raw inputs, body text, tokens, or provider data.

## Components

Extend `appV2.4/appv24/workflows/approval_commands.py`:

- `WorkflowApprovalListItem`
- `WorkflowApprovalListResult`
- `list_workflow_approval_requests(package_path, run_id)`
- `format_workflow_approval_list(result)`

Wire CLI and TUI routing beside existing `/workflow-approve` and `/workflow-deny`.

## Testing

Tests cover:

- helper lists pending approval request ids
- helper reflects approved and denied decisions
- CLI command prints approval request id without invoking the model
- TUI command renders approval request id without invoking the model
- unknown run errors are rendered as command failures

## Zone Impact

Green-zone:

- `appV2.4/appv24/workflows/approval_commands.py`
- workflow tests
- docs/specs/plans

Yellow-zone:

- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- command tests

Red-zone:

- No changes.
