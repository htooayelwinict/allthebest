# appv24 Cleanup Stabilization Design

Date: 2026-07-04
Status: approved design
Scope: appV2.4 / appv24 cleanup before workflow enablement

## Summary

appv24 has proven the first workflow milestone: the `workflow-designer-lite` profile can draft workflow packages, validate them, repair invalid drafts, and save valid packages through an explicit app-owned command.

The next step is cleanup and stabilization before adding workflow enablement. This milestone fixes branch/product drift, documents repair bounds, resolves the workflow-designer tool contract, centralizes shared prompt fragments, and adds runtime profile-enforcement tests.

This is not the workflow runner, compiler, broker, simulator, connector, cloud, or IaC phase.

## Branch And Product Identity

appv24 should be treated as its own long-lived product line, similar in separation to appv23 and appv231.

The target branch strategy is:

```text
next/appv24 = long-lived appv24 integration branch
feature/* = isolated appv24 feature branches
fix/* = isolated appv24 bug-fix branches
```

This cleanup does not merge appv24 into main, appv23, or appv231. It prepares the current appv24 profile branch to land under the appv24 line when Lewis decides.

## Rules Boundary

`rules.md` is a hard reference for this work. appv24 should grow through profiles, workflow modules, docs, examples, and tests, not through kernel changes.

Allowed areas:

```text
appV2.4/appv24/workflows/
appV2.4/appv24/profiles/
appV2.4/appv24/tui/interactive_mode.py only if required for /workflow-save enforcement
appV2.4/tests/
docs/
README/package metadata only if drift exists
```

Forbidden unless Lewis explicitly approves a kernel change:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Also forbidden:

```text
appV2.3/appv23 changes
appV2.3.1/appv231 changes
Pi or Hermes changes
provider streaming changes
compaction changes
session-store changes
real connector execution
workflow runner implementation
cloud or IaC apply
npm, GHCR, or release publishing
```

## Repair Bounds Contract

The cleanup should remove ambiguity between fixed repair attempts and loop-engineering direction.

Repair behavior should be documented and tested as:

```text
default: finite bounded repair attempts
profile/test override: configurable larger or effectively unbounded attempt mode
always exits: validation success, user abort, timeout, provider error, or explicit configured stop
```

The default should remain cost-safe. Workflow-specific testing may configure a larger or unbounded-style loop to prove the repair pipeline can keep improving a draft until validation passes.

The docs must not claim a hard two-attempt limit if the runtime is configurable.

## Workflow-Designer Tool Contract

`workflow-designer-lite` remains passive from the model's perspective.

The model must not receive mutating coding tools such as:

```text
write
edit
bash
```

The app-owned save command is still allowed:

```text
/workflow-save relative/path
```

This is not a contradiction. The model cannot write files by tool call. The user may explicitly ask the app to persist the latest valid draft through deterministic application code.

`/workflow-save` must:

```text
require workflow-designer-lite profile
require a latest valid workflow draft
accept only relative paths under active --cwd
reject absolute paths
reject parent traversal
refuse overwrite
write package files deterministically
revalidate saved files from disk
never execute the workflow
```

## Shared Prompt Fragments

Workflow schema and rule reminders should be centralized in:

```text
appV2.4/appv24/workflows/prompt_fragments.py
```

The module should provide focused text helpers for:

```text
required package blocks
side-effect contract reminders
approval and permission reminders
secret-reference reminders
rollback safety reminders
save/passive-boundary reminders
```

`prompt_examples.py` should remain focused on few-shot examples and micro-examples. It should not become the general schema/rule fragment module.

Staging, repair, and tests should consume shared fragments instead of duplicating long rule text.

## Runtime Profile-Enforcement Tests

The cleanup must add deterministic offline tests for profile isolation.

Minimum test coverage:

```text
workflow-designer-lite disables mutating coding tools
coding profile keeps normal coding behavior
workflow staging does not run for non-workflow profiles
workflow validation does not run for non-workflow profiles
workflow repair does not run for non-workflow profiles
/workflow-save fails outside workflow-designer-lite
/workflow-save fails when no valid draft exists
/workflow-save fails for invalid drafts
/workflow-save rejects absolute paths
/workflow-save rejects parent traversal
/workflow-save refuses overwrite
/workflow-save writes a valid package under --cwd
/workflow-save revalidates the saved package from disk
```

These tests should prove appv24 behavior at the profile boundary without depending on live model calls.

## Data Flow

The stabilized authoring flow is:

```text
User workflow request
  -> workflow-designer-lite profile selected
  -> staged draft prompts use shared prompt fragments
  -> model returns labeled package blocks
  -> validator checks the package
  -> repair loop runs with configured bounds
  -> final valid draft is stored in app memory
  -> user may run /workflow-save relative/path
  -> app writes package files under --cwd
  -> app revalidates saved files
```

For non-workflow profiles:

```text
no workflow staging
no workflow validation
no workflow repair
no successful /workflow-save path
```

## Error Handling

Validation and repair failures remain product feedback, not kernel failures.

Expected behavior:

```text
invalid draft -> visible validation issues
repair exhausted -> visible failure, save blocked
abort -> repair stops and reports aborted state
timeout/provider error -> repair stops and reports failure reason
save rejected -> user-facing error, no partial overwrite
disk revalidation fails -> user-facing error and no execution
```

The workflow profile must never convert invalid model output into a runnable workflow.

## Testing And Verification

Focused verification:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_workflow_packages.py
```

Full verification before completion:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Before committing implementation, run a frozen-zone review:

```bash
git diff --name-only
```

The implementation should stop for Lewis approval if any forbidden red-zone path appears.

## Non-Goals

Do not build:

```text
workflow compiler
workflow IR
policy simulator
execution broker
real connector adapters
scheduler
cloud runner
IaC preview or apply
credential manager
workflow marketplace
```

Those belong in the next workflow enablement spec.
