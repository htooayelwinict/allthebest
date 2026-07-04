# appv24 Workflow Package Validator

The appv24 workflow validator checks workflow-as-code packages before appv24 has any runner or cloud executor.

The validator is offline only. It does not run workflows, call APIs, send messages, provision cloud resources, or read secrets.

## Package Layout

```text
workflow.yaml
connectors.yaml
permissions.yaml
dry_run_fixtures/
  happy_path.yaml
  rejection_path.yaml
rollback.md
```

## Validation Layers

1. Required package files are present.
2. YAML files load through `yaml.safe_load`.
3. `workflow.yaml`, `connectors.yaml`, and `permissions.yaml` match appv24 JSON Schemas.
4. Workflow step IDs are unique.
5. Entrypoint and transitions point to existing steps.
6. `connector.action` references point to declared connectors and actions.
7. Permissions allow every connector action used by workflow steps.
8. Mutating steps require `side_effect_class` and an explicit approval gate.
9. Brokered mutating connector actions require explicit side-effect contract metadata.
10. Design-only mutating connector actions may be described, but workflow steps must not call them.
11. Inline secret-looking values are rejected. Use `secretRef` instead.
12. Mutating workflows require `dry_run_fixtures/happy_path.yaml`, `dry_run_fixtures/rejection_path.yaml`, and `rollback.md`.
13. `rollback.md` must be manual and non-executable. API, CLI, shell, token, destructive HTTP method, and broad destructive-scope examples are rejected.

## Python API

```python
from appv24.workflows import validate_workflow_package

result = validate_workflow_package("examples/appv24/workflows/dependency-security-review")
if not result.ok:
    for message in result.messages():
        print(message)
```

## Adapter Targets

Connector adapters may reference MCP, OpenAPI, AsyncAPI, webhook, direct API, IaC, manual, or platform export targets. These are design-time targets in v1. They are not executed by the validator.

## Side-Effect Contract

Mutating connector actions must declare enough metadata for a future broker to enforce the action without guessing. The validator checks this contract but does not execute it.

Brokered mutating action example:

```yaml
- id: create_issue
  operationId: issues_create
  side_effect: true
  side_effect_class: create
  execution_mode: brokered
  approval_required: true
  dry_run_supported: true
  idempotency:
    key_fields:
      - repo
      - alert_id
  outputs:
    allowed:
      - issue_number
      - issue_url
  required_scopes:
    - issues:write
```

Rules:

- `execution_mode` must be `brokered`, `manual`, or `design_only`.
- brokered mutating actions require `approval_required: true` and `dry_run_supported: true`.
- brokered `create`, `send`, `publish`, and `provision` actions require `idempotency.key_fields`.
- brokered mutating actions require `outputs.allowed` with explicit field names. `*` is rejected.
- workflow steps that call brokered mutating actions still require a valid approval gate.

Design-only mutating actions are allowed for future targets such as Telegram, email, Slack, or cloud notifications. They must not be called by workflow steps.

```yaml
- id: send_message
  side_effect: true
  side_effect_class: send
  execution_mode: design_only
  approval_required: false
  dry_run_supported: false
```

If `permissions.yaml` mentions a design-only action, that permission entry must be marked:

```yaml
design_only: true
```

## Live Draft Validation

When appv24 runs with `--profile workflow-designer-lite`, assistant responses that contain labeled workflow package blocks are validated after the assistant turn finishes.

The draft validator looks for fenced blocks labeled:

```text
workflow.yaml
connectors.yaml
permissions.yaml
dry_run_fixtures/happy_path.yaml
dry_run_fixtures/rejection_path.yaml
rollback.md
```

Validation is in-memory and passive. It does not write files, run workflows, call APIs, send messages, provision resources, or read secrets.

In interactive mode or the plain input loop, save the last valid draft explicitly:

```text
/workflow-save relative/path
```

The save path must be relative to the active `--cwd`. Existing files are not overwritten.

## Live Draft Staging

When appv24 runs with `--profile workflow-designer-lite`, user workflow requests are compiled through visible staged turns before validation:

```text
intent -> skeleton -> package -> validation -> repair if needed
```

The staging loop is passive. It does not execute workflows, call connector APIs, read secrets, run shell commands, provision infrastructure, send messages, or write package files.

The first two stages produce review notes only. The package stage must return the labeled workflow package blocks that the draft validator already understands.

Staging uses normal `CodingApp.run_turn()` calls. This means stage prompts and assistant responses are visible in the session transcript. appv24 does not create hidden internal workflow turns in this version.

## Live Draft Repair

When `appv24` runs with `--profile workflow-designer-lite`, the CLI and TUI validate the latest assistant workflow draft after each assistant turn.

If the draft fails validation, appv24 sends a compact validator feedback prompt back through the same `CodingApp.run_turn()` path. The repair loop is bounded to two attempts. The repair prompt includes validator issue codes, issue locations, issue messages, required draft section names, and schema reminders for common mistakes.

The repair loop is passive. It does not execute workflows, call connector APIs, read secrets, run shell commands, provision infrastructure, or write package files. It only asks the model to return a complete corrected draft. The existing `/workflow-save relative/path` command still refuses to save unless the final draft validates.

If both repair attempts fail, appv24 keeps the failed validation report visible and leaves `/workflow-save` blocked for that draft.
