# appv24 Workflow Debugger

appv24 workflow debugger commands are read-only. They are available in the TUI and plain CLI, and inspect saved workflow packages and saved local run artifacts without running connectors or creating new workflow runs.

## Package Explanation

```text
/workflow-explain support-ticket-triage
```

The command reports:

- package name
- validation status
- compile status
- policy status
- workflow step ids
- connector actions
- approval ids
- blocking issues

## Run Explanation

```text
/workflow-run-explain support-ticket-triage run-09b476f5287d4bcb9f9f10f2eec326cf
```

The command reads `.workflow-runs/<run-id>/` and reports:

- run status
- terminal step
- provenance from `run.json`: mode, fixture, package path, approval mode, connector allowlist, and activation id when present
- trace step ids
- brokered action previews, including hashed idempotency keys when present
- approval status summaries, including pending approval request ids and pinned action metadata when present
- recorded approval decisions
- recorded issues

If no run id is passed, appv24 uses the only saved run. If several runs exist, the command asks for an explicit run id instead of guessing.
