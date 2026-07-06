# appv24 Workflow Live-Read Command Design

Date: 2026-07-06
Scope: appV2.4/appv24 only
Status: approved by standing instruction to proceed

## Goal

Expose the existing `live-read` runner through safe operator commands so users can prove validation, credential, allowlist, and broker gates from CLI or TUI without enabling external connector effects.

## Design

Add one command:

```text
/workflow-run-live-read <package> <fixture> --allow <connector.action>[,<connector.action>]
```

The command builds a `WorkflowRunRequest` with `mode=live-read`, resolves credential references from environment variables, and calls `run_live_read_workflow` with `LiveWorkflowBroker({}, mode="live-read")`.

The empty transport registry is intentional. In v0, the command can persist a `live-read` run artifact and prove the gates, then blocks at connector invocation with a clear "live transport is not configured" issue. Future connector work can inject a real transport registry behind this same runner without changing workflow package semantics.

## Safety

- No red-zone files are touched.
- No connector transport is registered by default.
- Raw credentials are never read or printed; only `APPV24_<CONNECTOR>_CREDENTIAL_REF` and `APPV24_<CONNECTOR>_SCOPES` are checked.
- Mutating workflow steps remain blocked by the existing live-read runner.
- The command must not call the model.

## Tests

Add CLI and TUI tests proving:

- `/workflow-run-live-read` does not call the model.
- with valid env credential refs and an allowlisted read action, it creates a blocked `live-read` run because no transport is configured.
- output includes the live-read label and blocked transport issue.

## Zone Impact

Green-zone docs/tests plus narrow yellow-zone CLI/TUI command wiring:

- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- `appV2.4/tests/test_cli.py`
- `appV2.4/tests/test_tui.py`
- `docs/appv24/workflow-run-live-path.md`

Red-zone files remain untouched.
