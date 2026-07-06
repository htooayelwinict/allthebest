# appv24 Workflow Repair Optimizer

Workflow repair turns validation failures into scoped model feedback. The validator still consumes a complete workflow draft, so the repair prompt asks the model to return the full package as a minimal patched copy, not a diff.

## Issue Fingerprints

Repair fingerprints use stable validation coordinates:

```text
<code>:<file>:<location>
```

They intentionally ignore prose wording, so repeated failures are detected even if the validator message changes slightly.

## Repeated Failure Detection

When the same fingerprint set appears again, the repair prompt adds a loop progress warning:

- do not redesign the package
- revert unrelated drift
- fix only the failed paths

This keeps the infinite-style repair loop from becoming a broad rewrite loop.

## Scoped Prompts

Repair prompts include:

- validation issues
- failed edit scope
- repair directives
- targeted micro examples only when relevant
- required labeled fenced sections
- schema reminders

The full prompt fallback remains available when scope cannot be derived, but the normal path is scoped and bounded.

## TUI Report

```text
/workflow-repair-report
```

This shows the latest repair attempts, issue fingerprints, and repeated-pattern markers for the current TUI session.

