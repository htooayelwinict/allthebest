# appv24 Workflow Side-Effect Contract Design

Date: 2026-07-04
Status: proposed design
Scope: appV2.4 / appv24 workflow package layer only

## Summary

appv24 should tighten `workflow-designer-lite` packages so mutating workflow designs are safe enough for a future broker without building a runner now.

The current workflow package validator already checks schema shape, connector references, permissions, approvals, raw secrets, dry-run fixtures, and manual rollback notes. The missing contract is broker-ready metadata for side effects. A workflow can currently be valid while leaving future execution policy ambiguous.

This design adds a side-effect contract layer for mutating connector actions and mutating workflow steps. The contract remains passive: it validates package intent and safety metadata only. It does not execute workflows, call APIs, send messages, provision resources, schedule jobs, request secrets, or mutate external systems.

## Boundary

This work stays inside the workflow package layer.

Allowed implementation areas:

```text
appV2.4/appv24/workflows/schema.py
appV2.4/appv24/workflows/semantic.py
appV2.4/appv24/workflows/staging.py
appV2.4/appv24/workflows/repair.py
appV2.4/tests/
docs/
```

Avoid unless clearly needed:

```text
appV2.4/appv24/profiles/builtins.py
```

Do not touch:

```text
appV2.4/appv24/agent/agent.py
appV2.4/appv24/agent/agent_loop.py
appV2.4/appv24/agent/types.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/compressor.py
appV2.4/appv24/compaction/timing.py
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

TUI internals should not change in this phase unless an existing validation display string needs a small update. The current validation pass/fail display is enough.

## Contract Shape

Mutating connector actions should declare broker-ready metadata in `connectors.yaml`.

Example:

```yaml
- id: create_issue
  side_effect: true
  side_effect_class: create
  execution_mode: brokered
  approval_required: true
  dry_run_supported: true
  idempotency:
    key_fields:
      - repository
      - ghsa_id
  outputs:
    allowed:
      - issue_number
      - issue_url
```

Field meaning:

- `side_effect_class`: mutation category, one of `create`, `update`, `delete`, `send`, `publish`, or `provision`.
- `execution_mode`: future execution policy hint. v1 allows `brokered`, `manual`, and `design_only`.
- `approval_required`: whether a future broker must require explicit approval before this action.
- `dry_run_supported`: whether the action can be represented by dry-run fixtures before real execution.
- `idempotency.key_fields`: stable input fields that a future broker can use to prevent duplicate side effects.
- `outputs.allowed`: allowlist of output fields a future broker may store or return.

Mutating workflow steps still require the existing step-level contract:

```yaml
side_effect_class: create
approval: approve_create_issue
```

They may also include future-broker hints:

```yaml
idempotency_key: "${receive_alert.repository.full_name}:${receive_alert.alert.security_advisory.ghsa_id}"
rollback_strategy: manual
```

These fields are validated as package metadata only. They are not executed.

## Validation Policy

The validator should apply stricter contract rules only when an action is mutating.

### Mutating Action Metadata

If `connectors.yaml` has an action with `side_effect: true`, that action must include:

```text
side_effect_class
execution_mode
approval_required
dry_run_supported
```

`side_effect_class` must match the existing mutation classes:

```text
create
update
delete
send
publish
provision
```

`execution_mode` must be:

```text
brokered
manual
design_only
```

### Brokered Mutations

If a mutating action uses `execution_mode: brokered`:

- `approval_required` must be `true`.
- `dry_run_supported` must be `true`.
- every workflow step using the action must have `approval`.
- the approval id must exist in `permissions.yaml`.
- `approvals[].required_for` must include the exact workflow step id.
- the workflow must include required dry-run fixtures and manual rollback notes.

### Design-Only Mutations

If a mutating action uses `execution_mode: design_only`:

- workflow steps must not call that action.
- permissions may mention that action only as design-only or future-target intent.
- approval is not required.

This supports packages that describe future Telegram, email, Slack, cloud, or third-party delivery targets without letting a generated package pretend those actions are executable.

### Idempotency

If `side_effect_class` is one of:

```text
create
send
publish
provision
```

then the connector action must define:

```yaml
idempotency:
  key_fields:
    - some_input_field
```

The validator should treat missing idempotency as a blocking issue in v1. Avoid warnings for this phase; warnings make the contract ambiguous.

### Output Allowlist

Brokered mutating actions must define:

```yaml
outputs:
  allowed:
    - field_name
```

The allowlist must be a non-empty list of strings. Wildcards such as `*` are not allowed.

### Rollback

Existing rollback safety remains in force:

- mutating brokered workflows require `rollback.md`.
- rollback notes must be manual and non-executable.
- rollback notes must not include shell, API, cloud CLI, Terraform, kubectl, token, authorization header, destructive HTTP method, or broad destructive-scope examples.
- ordinary lowercase prose such as "delete permission" remains allowed.

## Prompt And Repair Behavior

The profile/staging prompts should teach the model the contract, but the validator remains the source of truth.

Package-stage prompt additions:

- mutating connector actions must include side-effect contract metadata.
- design-only connectors must use `execution_mode: design_only`.
- design-only actions must not be called by workflow steps.
- brokered mutating actions require approval, dry-run support, idempotency, and output allowlists.
- Telegram draft-only means no workflow step calls `telegram.send_message`.

Repair prompt additions:

- if a mutating action is missing broker fields, return a complete corrected package.
- if an action is design-only, remove executable workflow step references to it.
- if a brokered mutation lacks idempotency or output allowlist, add them.
- preserve the user's intended workflow.

The existing TUI and CLI validation/repair flow can remain unchanged except for the new issue messages produced by the validator and repair prompt.

## Testing Strategy

Add focused contract tests instead of broad TUI rewrites.

Schema tests:

- mutating action accepts broker metadata.
- invalid `execution_mode` is rejected.
- `outputs.allowed` must be a list of strings.
- `idempotency.key_fields` must be a list of strings.

Semantic tests:

- brokered mutating action without approval metadata fails.
- brokered mutating step without approval fails.
- brokered mutating action without dry-run support fails.
- brokered `create`, `send`, `publish`, or `provision` action without idempotency fails.
- design-only mutating action referenced by a workflow step fails.
- design-only mutating action in permissions is allowed only when marked design-only.
- brokered mutating action without `outputs.allowed` fails.
- valid GitHub issue creation package passes.
- valid Telegram draft-only package passes without executable send step.

Regression tests:

- convert the Mimo-style Dependabot workflow shape into a fixture and validate it against the stricter contract.
- lowercase rollback prose such as "delete permission" remains allowed.
- uppercase HTTP `DELETE`, curl, Terraform, kubectl, token, authorization header, and destructive scope examples remain blocked.

Verification before completion:

```text
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_staging.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

## Non-Goals

Do not build a workflow runner.

Do not call connector APIs.

Do not implement approval UI beyond existing package validation feedback.

Do not add broker state, scheduler state, execution logs, cloud credentials, or runtime secret access.

Do not create `broker.yaml` in this phase. Reserve a dedicated broker manifest for a later runner design if the package contract proves stable.

Do not add workflow-specific logic to agent loop, provider streaming, compaction, session store, or app kernel files.

## Success Criteria

The phase is successful when:

- brokered mutating actions cannot validate without approval, dry-run, idempotency, and output allowlist metadata.
- design-only mutating actions can be documented without becoming executable workflow steps.
- existing workflow draft validation and repair still work.
- appv24 remains passive: no workflow execution or external mutation is introduced.
- no red-zone kernel files are modified.

