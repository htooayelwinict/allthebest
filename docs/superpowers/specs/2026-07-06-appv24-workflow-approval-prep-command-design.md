# appv24 Workflow Approval Prep Command Design

## Goal

Close the user-facing gap between local workflow validation and approved live
execution by adding a command that creates pending local approval requests.

Current CLI/TUI commands can list, approve, deny, and consume approval requests,
but the simple local run command uses the auto-approved path. Live-draft and
live-production need a prior local run with explicit pending approval requests.

## Zone Scope

Green-zone behavior reused:

- existing local runner with `approve_all=False`
- existing approval request persistence
- existing approval list/approve/deny commands

Yellow-zone wiring:

- CLI/TUI command parsing, help, and autocomplete entries

Red-zone files are out of scope.

## Command Shape

```text
/workflow-run-approval <package> [fixture]
```

Default fixture is `happy_path.yaml`.

## Behavior

The command:

- runs the package locally with `mode=local`
- uses `approve_all=False`
- creates `.workflow-runs/run-*`
- persists `approvals.json` with pending approval request records
- prints the run id and blocked approval issue
- does not call the model
- does not call live transports or external providers

The expected status is often `blocked` because the runner stops at the first
approval gate. That is correct: the point is to create the pending approval
request.

## Tests

Add tests proving:

- CLI command creates a pending approval request without invoking the model
- TUI command creates a pending approval request without invoking the model
- existing auto-approved local run behavior is unchanged

## Rollback

Remove the command wiring, tests, and docs. The underlying local runner and
approval ledger behavior remain unchanged.
