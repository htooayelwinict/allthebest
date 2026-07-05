"""Small workflow prompt examples for schema shape only."""

from __future__ import annotations

from appv24.workflows.validator import WorkflowDraftValidationResult


PACKAGE_STAGE_MICRO_EXAMPLES = """
Micro examples for exact shape only:

Design contract shape:
```yaml
design:
  intent: Describe the passive workflow goal.
  trigger: Describe the trigger in prose.
  inputs:
    - Input needed by the workflow.
  outputs:
    - Output produced by the workflow.
  assumptions:
    - Assumption reviewers should verify.
  side_effects:
    - External mutation only after explicit approval.
  approval_gates:
    - approve_create_ticket
  failure_modes:
    - External API unavailable.
  rollback:
    - Manual rollback prose only.
```

Mutating step and action shape:
```yaml
steps:
  - id: create_ticket
    uses: ticketing.create_ticket
    side_effect_class: create
    approval: approve_create_ticket
    next: done
  - id: done
    type: terminal
```

```yaml
actions:
  - id: create_ticket
    side_effect: true
    side_effect_class: create
    execution_mode: brokered
    approval_required: true
    dry_run_supported: true
    idempotency:
      key_fields: [ticket_subject, customer_id]
    outputs:
      allowed: [ticket_id, ticket_url]
```

Approval and secret reference shape:
```yaml
approvals:
  - id: approve_create_ticket
    mode: explicit
    required_for: [create_ticket]
inputs:
  api_token: "secretRef: API_TOKEN_REF"
```

Decision branch and fixture shape:
```yaml
steps:
  - id: decide_path
    type: decision
    branches:
      approved: create_ticket
      rejected: done
```

```yaml
decision:
  decide_path: approved
```
""".strip()


DESIGN_CONTRACT_MICRO_EXAMPLE = """
Design contract shape:
```yaml
design:
  intent: Describe the passive workflow goal.
  trigger: Describe the trigger in prose.
  inputs:
    - Input needed by the workflow.
  outputs:
    - Output produced by the workflow.
  assumptions:
    - Assumption reviewers should verify.
  side_effects:
    - External mutation only after explicit approval.
  approval_gates:
    - approve_create_ticket
  failure_modes:
    - External API unavailable.
  rollback:
    - Manual rollback prose only.
```
""".strip()


BROKERED_ACTION_MICRO_EXAMPLE = """
Mutating brokered action shape:
```yaml
actions:
  - id: create_ticket
    side_effect: true
    side_effect_class: create
    execution_mode: brokered
    approval_required: true
    dry_run_supported: true
    idempotency:
      key_fields: [ticket_subject, customer_id]
    outputs:
      allowed: [ticket_id, ticket_url]
```
""".strip()


APPROVAL_WIRING_MICRO_EXAMPLE = """
Approval wiring shape:
```yaml
steps:
  - id: create_ticket
    uses: ticketing.create_ticket
    side_effect_class: create
    approval: approve_create_ticket
    next: done
  - id: done
    type: terminal
approvals:
  - id: approve_create_ticket
    mode: explicit
    required_for: [create_ticket]
```
""".strip()


SECRET_REFERENCE_MICRO_EXAMPLE = """
Secret reference shape:
```yaml
inputs:
  api_token: "secretRef: API_TOKEN_REF"
```
""".strip()


ROLLBACK_MICRO_EXAMPLE = """
Rollback prose shape:
```markdown
# Rollback

Manual rollback prose only. Review the created record, annotate the audit trail,
and close or correct the record according to operator policy.
```
""".strip()


DECISION_BRANCH_MICRO_EXAMPLE = """
Decision branch and fixture shape:
```yaml
steps:
  - id: decide_path
    type: decision
    branches:
      approved: create_ticket
      rejected: done
```

```yaml
decision:
  decide_path: approved
```
""".strip()


RUNTIME_TRANSITION_MICRO_EXAMPLE = """
Runtime transition shape:
```yaml
steps:
  - id: create_ticket
    uses: ticketing.create_ticket
    side_effect_class: create
    approval: approve_create_ticket
    next: done
  - id: done
    type: terminal
```
""".strip()


def render_repair_micro_examples(result: WorkflowDraftValidationResult) -> str:
    """Return small examples for the current failure classes."""

    if result.ok:
        return ""

    examples: list[str] = []
    seen: set[str] = set()

    def add(key: str, text: str) -> None:
        if key not in seen:
            seen.add(key)
            examples.append(text)

    for issue in result.validation.issues:
        code = str(getattr(issue, "code", ""))
        file = str(getattr(issue, "file", ""))
        path = getattr(issue, "path", ())
        path_text = ".".join(str(part) for part in path) if isinstance(path, tuple) else ""

        if code == "missing_design_contract" or (file == "workflow.yaml" and path_text.startswith("design")):
            add("design", DESIGN_CONTRACT_MICRO_EXAMPLE)
        if code in {"missing_side_effect_contract", "invalid_side_effect_contract"}:
            add("brokered-action", BROKERED_ACTION_MICRO_EXAMPLE)
        if code in {"missing_approval", "approval_missing_required_for", "unknown_approval"}:
            add("approval", APPROVAL_WIRING_MICRO_EXAMPLE)
        if code in {"missing_side_effect_class", "missing_side_effect_contract", "invalid_side_effect_contract"}:
            add("approval", APPROVAL_WIRING_MICRO_EXAMPLE)
        if code == "raw_secret":
            add("secret", SECRET_REFERENCE_MICRO_EXAMPLE)
        if code in {"unsafe_rollback", "missing_rollback"}:
            add("rollback", ROLLBACK_MICRO_EXAMPLE)
        if code in {
            "missing_decision_branches",
            "unsupported_decision_syntax",
            "missing_fixture_decision",
            "unknown_fixture_decision_branch",
        }:
            add("decision", DECISION_BRANCH_MICRO_EXAMPLE)
        if code == "missing_step_transition":
            add("runtime-transition", RUNTIME_TRANSITION_MICRO_EXAMPLE)

    if not examples:
        return ""

    return "\n\n".join(("Targeted micro examples:", *examples))
