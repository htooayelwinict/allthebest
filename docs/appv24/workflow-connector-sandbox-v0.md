# appv24 Workflow Connector Sandbox v0

Date: 2026-07-06
Scope: appV2.4 workflow local execution only

## Boundary

Connector sandbox v0 is fixture-backed simulation. It never calls external APIs, cloud services, MCP tools, webhooks, browser automation, shell commands, Telegram, Gmail, or ticketing systems.

The sandbox wraps local artifact providers and lets a dry-run fixture force connector behavior by workflow step id or connector action.

## Fixture Shape

```yaml
connector_sandbox:
  failures:
    fetch_news:
      status: timeout
      message: news connector timed out after 30s
  outputs:
    extract_catalysts:
      unexpected: not allowed by declared output contract
  missing_fields:
    fetch_news:
      - items
```

Lookup order is:

```text
step id
connector action
```

For example, `fetch_news` is checked before `source.fetch_items`.

## Supported Scenarios

The first saved example scenarios live under:

```text
examples/appv24/workflows/financial-catalyst-alerts/dry_run_fixtures/
examples/appv24/workflows/public-news-digest/dry_run_fixtures/
examples/appv24/workflows/telegram-support-ticket/dry_run_fixtures/
```

Included fixtures:

```text
happy_path.yaml
rejection_path.yaml
connector_timeout.yaml
bad_llm_output.yaml
missing_field.yaml
```

The golden Telegram support-ticket workflow also includes:

```text
unauthorized_user.yaml
ticket_draft_timeout.yaml
```

The persisted Telegram support-ticket example includes:

```text
happy_path.yaml
rejection_path.yaml
unauthorized_user.yaml
ticket_draft_timeout.yaml
```

The golden Gmail draft-reply workflow also includes:

```text
missing_recipient.yaml
```

The public news digest example includes:

```text
happy_path.yaml
rejection_path.yaml
source_timeout.yaml
bad_source_output.yaml
```

Expected behavior:

```text
happy_path.yaml        -> passed
rejection_path.yaml    -> blocked by policy before notification draft
connector_timeout.yaml -> blocked at source.fetch_items
bad_llm_output.yaml    -> blocked by output allowlist mismatch
missing_field.yaml     -> blocked before the next step
unauthorized_user.yaml -> terminal rejected with no brokered actions
ticket_draft_timeout.yaml -> blocked before admin notification
missing_recipient.yaml -> blocked before local Gmail draft artifact
source_timeout.yaml -> blocked at source.fetch_document
bad_source_output.yaml -> blocked by output allowlist mismatch
```

## TUI Command

```text
/workflow-run-sandbox <package> [fixture]
```

This command runs `WorkflowRunMode.LOCAL` with fixture-backed connector simulation and labels the output as a sandbox run. It does not enable live execution.

## Verification

Focused command:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_runner.py \
  appV2.4/tests/test_workflow_local_artifacts.py \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_run_sandbox_runs_named_fixture
```
