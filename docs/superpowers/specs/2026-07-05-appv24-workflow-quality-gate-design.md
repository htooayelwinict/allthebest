# appv24 Workflow Quality Gate Design

Date: 2026-07-05
Status: approved design
Scope: appV2.4 / appv24 workflow package layer only

## Summary

The last background TUI workflow test proved that appv24 can draft, repair, validate, save, compile, and policy-check a workflow package. It also exposed the next contract gap: a package can pass schema validation, semantic validation, compile, and policy checks while still failing dry-run simulation because action steps do not link to a terminal path.

This phase adds a **Workflow Quality Gate**:

```text
draft/package documents
  -> existing validation
  -> compile to IR
  -> static graph checks
  -> fixture simulation checks
  -> quality-gate result
```

The goal is to make `workflow-designer-lite` treat a workflow as ready only when the package has a reachable terminal path and each declared dry-run fixture can traverse to a terminal step. This is still passive validation. It must not call real connectors, external APIs, shell commands, MCP servers, cloud APIs, browsers, secrets, or package managers.

## Decision

Build a combined static and simulation quality gate inside `appV2.4/appv24/workflows/`.

The gate has two responsibilities:

- **Static graph readiness:** detect missing transitions, unreachable steps, unknown terminal paths, and cycles that have no visible terminal escape.
- **Fixture readiness:** run deterministic dry-run simulation for each fixture and require terminal completion.

This is stronger than schema validation and lighter than a real runner. It turns the exact TUI failure into a first-class repairable issue.

## Boundary

Allowed implementation areas:

```text
appV2.4/appv24/workflows/quality_gate.py
appV2.4/appv24/workflows/reports.py
appV2.4/appv24/workflows/repair.py
appV2.4/appv24/workflows/repair_directives.py
appV2.4/appv24/workflows/staging.py
appV2.4/appv24/workflows/prompt_fragments.py
appV2.4/appv24/workflows/prompt_examples.py
appV2.4/appv24/workflows/__init__.py
appV2.4/tests/test_workflow_quality_gate.py
appV2.4/tests/test_workflow_repair.py
appV2.4/tests/test_workflow_staging.py
appV2.4/tests/test_workflow_examples.py
docs/appv24/
```

Allowed only if needed for small compatibility fixes:

```text
appV2.4/appv24/workflows/compiler.py
appV2.4/appv24/workflows/simulator.py
appV2.4/appv24/workflows/ledger.py
appV2.4/appv24/workflows/validator.py
appV2.4/tests/test_workflow_ir.py
appV2.4/tests/test_workflow_simulator.py
appV2.4/tests/test_workflow_drafts.py
```

Avoid yellow-zone files unless the core gate is stable and a tiny integration is necessary:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/
appV2.4/appv24/profiles/
packages/appv24-cli/
```

Do not touch red-zone files:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Zone rule for this phase:

- Red-zone files are not allowed.
- Yellow-zone files are not part of the implementation plan.
- If a later TUI/CLI rendering change becomes useful, it must be a separate thin integration after the workflow-layer gate is complete and tested.
- The first implementation proof must pass through workflow modules and tests only.

## Approaches Considered

### A. Static Graph Checks Only

This adds reachability and transition checks after compile.

Pros:

- small and deterministic
- catches the missing `next` failure immediately
- stays fully inside workflow modules

Cons:

- does not prove fixture branches match the workflow
- can miss decision paths that only fail during simulation

Decision: include this, but not as the full phase.

### B. Simulation-Only Readiness

This relies on `simulate_workflow_fixture()` and fails any package whose fixture run does not terminate.

Pros:

- proves the user-visible scenarios work
- reuses existing simulator and broker

Cons:

- error messages can be too late and trace-oriented
- missing transitions become “stopped without terminal” instead of exact graph issues
- static unreachable steps can remain hidden if fixtures never visit them

Decision: include this, but not as the full phase.

### C. Combined Quality Gate

This adds static graph checks first, then simulation checks for each fixture.

Pros:

- catches structural problems with precise repair paths
- proves dry-run scenarios reach terminal
- gives the repair loop actionable issue codes
- keeps future runner readiness separate from real execution

Cons:

- more code than either check alone
- must be careful not to duplicate validator/compiler concerns

Decision: choose this approach.

## Architecture

Add `quality_gate.py` with plain dataclasses:

```python
@dataclass(frozen=True)
class WorkflowQualityIssue:
    code: str
    file: str
    path: tuple[str | int, ...]
    message: str
    severity: str = "error"
    repair_hint: str | None = None

@dataclass(frozen=True)
class WorkflowQualityResult:
    root: Path
    ok: bool
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    issues: tuple[WorkflowQualityIssue, ...]
    fixture_reports: tuple[WorkflowFixtureQualityReport, ...]
```

Public API:

```python
def check_workflow_quality_documents(
    documents: PackageDocuments,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult: ...

def check_workflow_quality_package(
    path: str | Path,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult: ...
```

The quality gate delegates existing work instead of replacing it:

```text
validate_workflow_documents
  -> compile_workflow_documents
  -> evaluate_policy
  -> static graph checks
  -> simulate every dry-run fixture
```

## Static Graph Checks

Static checks operate on compiled `WorkflowIR`.

Blocking issue codes:

```text
missing_transition
  non-terminal, non-decision step has no next step

missing_branch_transition
  decision step has no branches

unreachable_step
  step is not reachable from entrypoint

terminal_unreachable
  no terminal step is reachable from entrypoint

cycle_without_terminal_escape
  cycle is reachable and has no outbound path to terminal
```

Initial policy should be strict but practical:

- every action/manual step needs `next` unless it is terminal
- every decision step needs at least one branch
- all non-terminal paths should have a possible path to terminal
- unreachable non-terminal steps fail because they usually indicate model drift
- unreachable terminal steps fail only when no reachable terminal exists

This belongs in `quality_gate.py`, not in the JSON schema. Schema should continue checking document shape. Compiler should continue checking references and IR normalization. Quality gate checks readiness.

## Fixture Simulation Checks

For each fixture in `PackageDocuments.dry_run_fixtures`, the gate runs:

```python
simulate_workflow_fixture(ir, fixture_name, fixture, broker=broker)
```

Blocking issue codes:

```text
fixture_no_terminal
  fixture simulation ended without a terminal step

fixture_branch_unmatched
  decision step had multiple branches and fixture did not specify a valid decision

fixture_transition_budget_exceeded
  simulation exceeded max transition budget

fixture_broker_blocked
  broker blocked a connector action
```

The gate should preserve the simulator ledger in a compact fixture report:

```python
@dataclass(frozen=True)
class WorkflowFixtureQualityReport:
    fixture_name: str
    status: str
    terminal_step: str | None
    trace_steps: tuple[str, ...]
    issues: tuple[str, ...]
```

The simulator can remain simple. If clearer issue codes need small additions to `simulator.py`, keep them local and covered by tests.

## Repair Integration

Quality gate failures should become repairable feedback for `workflow-designer-lite`.

Repair prompt additions:

- Treat quality gate issues like compiler errors.
- For `missing_transition`, add the smallest correct `next:` transition.
- Preserve step ids, connector ids, approvals, fixtures, rollback, and business intent.
- Do not redesign the workflow.
- If fixture simulation fails because a decision branch is not specified, add the smallest fixture `decision:` mapping.
- If terminal is unreachable, connect the existing path to the existing terminal step instead of inventing a new workflow.

Example directive:

```text
- missing_transition workflow.yaml:steps.receive_message:
  Add next: load_allowlist to step receive_message, or the smallest next transition that preserves the existing step order and intended flow.
```

The repair loop should continue to validate and repair until the combined validation plus quality gate passes, subject to the existing repeated-failure and timeout protections.

## Staging Prompt Integration

The package-stage prompt should include one small reminder:

```text
Every non-terminal action step must include next, every decision step must include branches, and every dry-run fixture must be able to traverse from entrypoint to terminal.
```

This is prompt help only. The quality gate is the source of truth.

## Save And Report Semantics

The current `/workflow-save` behavior can remain initially unchanged while the workflow-layer quality gate is built and tested.

After the gate is stable, the live validation path should report:

```text
Workflow draft validation: passed
Workflow quality gate: passed
```

or:

```text
Workflow draft validation: failed
- missing_transition workflow.yaml:steps.receive_message: non-terminal step has no next transition
```

If a small TUI/CLI integration is added, it should only call workflow-layer APIs and render existing result strings. It must not change agent loop, session, provider, compaction, or tool execution behavior.

## Testing Strategy

Add focused tests before implementation behavior is broadened.

Quality gate tests:

```text
valid golden workflow passes quality gate
non-terminal action without next fails missing_transition
decision without branches fails missing_branch_transition
orphan action step fails unreachable_step
entrypoint path with no terminal fails terminal_unreachable
simple cycle with no terminal escape fails cycle_without_terminal_escape
happy fixture reaches terminal and reports trace
rejection fixture reaches terminal and reports trace
fixture with missing decision fails fixture_branch_unmatched or fixture_no_terminal
broker-blocked action fails fixture_broker_blocked
```

Repair tests:

```text
repair prompt includes missing_transition directive
repair prompt scopes fixture decision failures to dry_run_fixtures path
repair loop can converge from missing next transitions to quality-gate pass
repeated quality-gate failure still trips repeated-failure guard
```

Prompt/staging tests:

```text
package-stage prompt requires next transitions and terminal fixture traversal
quality gate rules are present only for workflow-designer-lite staging
```

Regression tests:

```text
existing valid examples still pass validation
existing side-effect contract tests still pass
TUI save remains path-safe and explicit
non-workflow profiles do not run workflow quality gate
```

Verification:

```text
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_staging.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_examples.py appV2.4/tests/test_workflow_simulator.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

## Rollout Order

1. Add quality-gate dataclasses and static graph checks.
2. Add fixture simulation checks and compact fixture reports.
3. Wire quality gate into enablement reports.
4. Add repair directives and prompt fragments for gate failures.
5. Add staging reminder for transitions and terminal fixture traversal.
6. Only then consider tiny live-validation rendering integration.

## Non-Goals

Do not build real workflow execution.

Do not add connector credentials, OAuth, token handling, background workers, schedulers, webhooks, cloud runners, Terraform/Pulumi apply, Telegram/Gmail/GitHub sends, shell execution, browser automation, or MCP execution.

Do not move workflow logic into red-zone kernel files.

Do not make workflow quality checks global for all profiles.

Do not require generated workflows to cover every possible runtime input. This phase only requires structural terminal reachability plus declared dry-run fixture completion.

## Risk Review

Risk: stricter validation could make weaker budget models repair more often.

Mitigation: make gate failures precise and repair-scoped, with examples for missing `next`, decision fixture mapping, and terminal reachability.

Risk: quality gate duplicates compiler validation.

Mitigation: compiler checks references and IR normalization; quality gate checks readiness and terminal traversal.

Risk: TUI/CLI churn.

Mitigation: build and test the workflow-layer API first. Any UI integration must be a thin report call only.

Risk: overfitting to Telegram or support workflows.

Mitigation: issue codes and tests must use generic workflow shapes as primary fixtures. Domain examples can be secondary.
