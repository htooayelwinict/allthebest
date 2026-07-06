# appv24 Workflow Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a workflow-layer quality gate that rejects workflow packages which validate and compile but cannot reach a terminal step through static graph checks and declared dry-run fixtures.

**Architecture:** Implement `appV2.4/appv24/workflows/quality_gate.py` as a pure workflow module. It delegates to existing validation, compiler, policy, broker, and simulator code; it does not execute connectors and does not touch appv24 kernel, provider, compaction, session, TUI, or CLI control flow.

**Tech Stack:** Python dataclasses, existing appv24 workflow modules, pytest.

---

## Zone Rules

- Do not touch red-zone files under `appV2.4/appv24/agent/`, `appV2.4/appv24/ai/stream.py`, `appV2.4/appv24/ai/validation.py`, `appV2.4/appv24/compaction/`, `appV2.4/appv24/coding_agent/session_store.py`, or `appV2.4/appv24/app.py`.
- Do not change yellow-zone TUI/CLI/profile files in the first implementation cut.
- First proof must live in `appV2.4/appv24/workflows/`, workflow tests, and docs only.
- If a later UI report is wanted, create a separate plan after the workflow-layer API passes tests.

## File Structure

- Create `appV2.4/appv24/workflows/quality_gate.py`
  - Owns quality-gate dataclasses, graph checks, fixture simulation checks, and package/document entrypoints.
- Modify `appV2.4/appv24/workflows/__init__.py`
  - Exports stable quality-gate API.
- Modify `appV2.4/appv24/workflows/reports.py`
  - Uses the quality gate in enablement reports so reports cannot hide simulation dead ends.
- Modify `appV2.4/appv24/workflows/repair.py`
  - Builds repair prompts from quality-gate issues after basic validation passes.
- Modify `appV2.4/appv24/workflows/repair_directives.py`
  - Adds repair directives for `missing_transition`, `missing_branch_transition`, `terminal_unreachable`, and fixture simulation failures.
- Modify `appV2.4/appv24/workflows/staging.py`
  - Adds a package-stage prompt reminder for `next`, branches, and terminal fixture traversal.
- Create `appV2.4/tests/test_workflow_quality_gate.py`
  - Primary contract tests.
- Modify existing workflow tests only where necessary:
  - `appV2.4/tests/test_workflow_repair.py`
  - `appV2.4/tests/test_workflow_staging.py`
  - `appV2.4/tests/test_workflow_examples.py`

---

### Task 1: Static Quality Gate API

**Files:**
- Create: `appV2.4/appv24/workflows/quality_gate.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Test: `appV2.4/tests/test_workflow_quality_gate.py`

- [ ] **Step 1: Write failing tests for missing transitions**

Add tests that build a minimal valid package, remove `next` from an action step, compile it, and assert the quality gate fails with `missing_transition`.

```python
from dataclasses import replace

from appv24.workflows.quality_gate import check_workflow_quality_documents
from appv24.workflows.examples import golden_workflow_examples


def test_quality_gate_rejects_action_without_next() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    steps = [dict(step) for step in workflow["steps"]]
    steps[0].pop("next", None)
    workflow["steps"] = steps
    documents = replace(example.documents, workflow=workflow)

    result = check_workflow_quality_documents(documents, broker=example.broker)

    assert not result.ok
    assert [issue.code for issue in result.issues] == ["missing_transition"]
    assert result.issues[0].path == ("steps", steps[0]["id"], "next")
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py::test_quality_gate_rejects_action_without_next -q
```

Expected: fails because `appv24.workflows.quality_gate` does not exist.

- [ ] **Step 3: Implement dataclasses and document entrypoint**

Create `quality_gate.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents, compile_workflow_package
from appv24.workflows.ir import StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.validator import validate_workflow_documents

WorkflowQualitySeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class WorkflowQualityIssue:
    code: str
    file: str
    path: tuple[str | int, ...]
    message: str
    severity: WorkflowQualitySeverity = "error"
    repair_hint: str | None = None

    @property
    def location(self) -> str:
        suffix = ".".join(str(part) for part in self.path)
        return f"{self.file}:{suffix}" if suffix else self.file


@dataclass(frozen=True)
class WorkflowFixtureQualityReport:
    fixture_name: str
    status: str
    terminal_step: str | None
    trace_steps: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowQualityResult:
    root: Path
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    issues: tuple[WorkflowQualityIssue, ...] = ()
    fixture_reports: tuple[WorkflowFixtureQualityReport, ...] = ()

    @property
    def ok(self) -> bool:
        return self.validation_ok and self.compile_ok and self.policy_ok and not self.issues
```

Add:

```python
def check_workflow_quality_package(
    path: str | Path,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult:
    documents = load_package_documents(path)
    return check_workflow_quality_documents(documents, broker=broker)


def check_workflow_quality_documents(
    documents: PackageDocuments,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult:
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=False,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(
                    code=issue.code,
                    file=issue.file,
                    path=issue.path,
                    message=issue.message,
                    repair_hint=issue.repair_hint,
                )
                for issue in validation.issues
            ),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=True,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(issue.code, issue.file, issue.path, issue.message)
                for issue in compiled.issues
            ),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=True,
            compile_ok=True,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(issue.code, "workflow.yaml", (issue.step_id or "",), issue.message)
                for issue in policy.issues
            ),
        )

    graph_issues = _check_graph(compiled.ir)
    return WorkflowQualityResult(
        root=documents.root,
        validation_ok=True,
        compile_ok=True,
        policy_ok=True,
        issues=tuple(graph_issues),
    )
```

- [ ] **Step 4: Implement minimal `_check_graph` for missing transitions**

```python
def _check_graph(ir: WorkflowIR) -> list[WorkflowQualityIssue]:
    issues: list[WorkflowQualityIssue] = []
    for step in ir.steps.values():
        if step.kind is StepKind.TERMINAL:
            continue
        if step.kind is StepKind.DECISION:
            continue
        if step.next_step is None:
            issues.append(
                WorkflowQualityIssue(
                    code="missing_transition",
                    file="workflow.yaml",
                    path=("steps", step.step_id, "next"),
                    message=f"non-terminal step {step.step_id} has no next transition",
                    repair_hint=f"Add next to workflow.yaml step {step.step_id}.",
                )
            )
    return issues
```

- [ ] **Step 5: Export the new API**

Add to `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.quality_gate import (
    WorkflowFixtureQualityReport,
    WorkflowQualityIssue,
    WorkflowQualityResult,
    check_workflow_quality_documents,
    check_workflow_quality_package,
)
```

- [ ] **Step 6: Run focused test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: the missing transition test passes.

- [ ] **Step 7: Commit**

```bash
git add appV2.4/appv24/workflows/quality_gate.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_quality_gate.py
git commit -m "feat(appv24): add workflow quality gate API"
```

---

### Task 2: Reachability And Terminal Graph Checks

**Files:**
- Modify: `appV2.4/appv24/workflows/quality_gate.py`
- Test: `appV2.4/tests/test_workflow_quality_gate.py`

- [ ] **Step 1: Add failing tests for graph readiness**

Add tests for:

```python
def test_quality_gate_rejects_decision_without_branches() -> None: ...
def test_quality_gate_rejects_unreachable_non_terminal_step() -> None: ...
def test_quality_gate_rejects_entrypoint_without_reachable_terminal() -> None: ...
def test_quality_gate_rejects_cycle_without_terminal_escape() -> None: ...
```

Each test should mutate a golden example document and assert issue codes:

```python
assert "missing_branch_transition" in {issue.code for issue in result.issues}
assert "unreachable_step" in {issue.code for issue in result.issues}
assert "terminal_unreachable" in {issue.code for issue in result.issues}
assert "cycle_without_terminal_escape" in {issue.code for issue in result.issues}
```

- [ ] **Step 2: Run tests to verify failure**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: new tests fail with missing issue codes.

- [ ] **Step 3: Implement graph traversal helpers**

Add helpers:

```python
def _reachable_steps(ir: WorkflowIR) -> set[str]:
    reachable: set[str] = set()
    stack = [ir.entrypoint]
    while stack:
        step_id = stack.pop()
        if step_id in reachable:
            continue
        step = ir.steps.get(step_id)
        if step is None:
            continue
        reachable.add(step_id)
        stack.extend(target for target in step.outbound if target not in reachable)
    return reachable


def _terminal_steps(ir: WorkflowIR) -> set[str]:
    return {step.step_id for step in ir.steps.values() if step.kind is StepKind.TERMINAL}


def _can_reach_terminal(ir: WorkflowIR, start: str, terminals: set[str]) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        step_id = stack.pop()
        if step_id in terminals:
            return True
        if step_id in seen:
            continue
        seen.add(step_id)
        step = ir.steps.get(step_id)
        if step is not None:
            stack.extend(step.outbound)
    return False
```

- [ ] **Step 4: Extend `_check_graph`**

Add:

```python
reachable = _reachable_steps(ir)
terminals = _terminal_steps(ir)
if not any(_can_reach_terminal(ir, step_id, terminals) for step_id in reachable):
    issues.append(WorkflowQualityIssue("terminal_unreachable", "workflow.yaml", ("entrypoint",), "no terminal step is reachable from entrypoint", "error", "Connect the entrypoint path to an existing terminal step."))

for step in ir.steps.values():
    if step.step_id not in reachable and step.kind is not StepKind.TERMINAL:
        issues.append(WorkflowQualityIssue("unreachable_step", "workflow.yaml", ("steps", step.step_id), f"step {step.step_id} is not reachable from entrypoint", "error", f"Connect or remove workflow.yaml step {step.step_id}."))
    if step.kind is StepKind.DECISION and not step.branches:
        issues.append(WorkflowQualityIssue("missing_branch_transition", "workflow.yaml", ("steps", step.step_id, "branches"), f"decision step {step.step_id} has no branches", "error", f"Add branches to workflow.yaml decision step {step.step_id}."))
```

Implement cycle detection with a small DFS over reachable nodes. Report `cycle_without_terminal_escape` when every node in a detected cycle fails `_can_reach_terminal`.

- [ ] **Step 5: Run focused tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: graph tests pass.

- [ ] **Step 6: Commit**

```bash
git add appV2.4/appv24/workflows/quality_gate.py appV2.4/tests/test_workflow_quality_gate.py
git commit -m "feat(appv24): add workflow graph readiness checks"
```

---

### Task 3: Fixture Simulation Quality Checks

**Files:**
- Modify: `appV2.4/appv24/workflows/quality_gate.py`
- Test: `appV2.4/tests/test_workflow_quality_gate.py`

- [ ] **Step 1: Add failing tests for fixture readiness**

Add:

```python
def test_quality_gate_passes_valid_golden_example_fixtures() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = check_workflow_quality_documents(example.documents, broker=example.broker)
    assert result.ok
    assert {report.fixture_name for report in result.fixture_reports} == {"happy_path.yaml", "rejection_path.yaml"}
    assert all(report.terminal_step for report in result.fixture_reports)


def test_quality_gate_reports_fixture_without_terminal() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    steps = [dict(step) for step in workflow["steps"]]
    steps[0].pop("next", None)
    workflow["steps"] = steps
    documents = replace(example.documents, workflow=workflow)
    result = check_workflow_quality_documents(documents, broker=example.broker)
    assert "missing_transition" in {issue.code for issue in result.issues}
```

Add a fixture decision mismatch test by removing a required `decision` value from a fixture and expecting `fixture_no_terminal` or `fixture_branch_unmatched`.

- [ ] **Step 2: Run tests to verify failure**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: fixture report tests fail until simulation is wired in.

- [ ] **Step 3: Add fixture simulation to quality gate**

After graph checks pass, run every fixture:

```python
from appv24.workflows.simulator import simulate_workflow_fixture


def _check_fixtures(
    ir: WorkflowIR,
    documents: PackageDocuments,
    broker: LocalWorkflowBroker | None,
) -> tuple[list[WorkflowQualityIssue], list[WorkflowFixtureQualityReport]]:
    issues: list[WorkflowQualityIssue] = []
    reports: list[WorkflowFixtureQualityReport] = []
    for fixture_name, fixture in sorted(documents.dry_run_fixtures.items()):
        ledger = simulate_workflow_fixture(ir, fixture_name, fixture, broker=broker)
        reports.append(
            WorkflowFixtureQualityReport(
                fixture_name=fixture_name,
                status=ledger.status,
                terminal_step=ledger.terminal_step,
                trace_steps=tuple(step.step_id for step in ledger.trace),
                issues=ledger.issues,
            )
        )
        if ledger.terminal_step is None:
            issues.append(
                WorkflowQualityIssue(
                    code="fixture_no_terminal",
                    file=f"dry_run_fixtures/{fixture_name}",
                    path=(),
                    message=f"fixture {fixture_name} did not reach a terminal step",
                    repair_hint="Fix workflow transitions or fixture decisions so this fixture reaches terminal.",
                )
            )
    return issues, reports
```

In `check_workflow_quality_documents`, only run `_check_fixtures` if graph issues are empty.

- [ ] **Step 4: Run focused tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: quality gate tests pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.4/appv24/workflows/quality_gate.py appV2.4/tests/test_workflow_quality_gate.py
git commit -m "feat(appv24): require fixture terminal traversal"
```

---

### Task 4: Enablement Report Integration

**Files:**
- Modify: `appV2.4/appv24/workflows/reports.py`
- Test: `appV2.4/tests/test_workflow_examples.py`

- [ ] **Step 1: Add failing report test**

Add a test proving `build_enablement_report()` blocks when quality gate fails:

```python
def test_enablement_report_blocks_quality_gate_failure() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    steps = [dict(step) for step in workflow["steps"]]
    steps[0].pop("next", None)
    documents = replace(example.documents, workflow={**workflow, "steps": steps})
    fixture = require_fixture(documents.dry_run_fixtures, "happy_path.yaml")

    report = build_enablement_report(documents, "happy_path.yaml", fixture, broker=example.broker)

    assert report.simulation_status == "blocked"
    assert any("missing transition" in issue.lower() for issue in report.issues)
```

- [ ] **Step 2: Run failing test**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_examples.py::test_enablement_report_blocks_quality_gate_failure -q
```

Expected: fails because reports do not call quality gate.

- [ ] **Step 3: Update reports to use quality gate**

In `reports.py`, import and call:

```python
from appv24.workflows.quality_gate import check_workflow_quality_documents
```

Before direct fixture simulation:

```python
quality = check_workflow_quality_documents(documents, broker=broker)
if not quality.ok:
    return WorkflowEnablementReport(
        workflow_name=workflow_name,
        validation_ok=quality.validation_ok,
        compile_ok=quality.compile_ok,
        policy_ok=quality.policy_ok,
        simulation_status="blocked",
        terminal_step=None,
        simulated_side_effects=(),
        issues=tuple(issue.message for issue in quality.issues),
    )
```

- [ ] **Step 4: Run report tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_examples.py appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.4/appv24/workflows/reports.py appV2.4/tests/test_workflow_examples.py
git commit -m "feat(appv24): gate workflow enablement reports"
```

---

### Task 5: Repair Prompt Integration

**Files:**
- Modify: `appV2.4/appv24/workflows/repair.py`
- Modify: `appV2.4/appv24/workflows/repair_directives.py`
- Test: `appV2.4/tests/test_workflow_repair.py`

- [ ] **Step 1: Add failing repair tests**

Add:

```python
def test_repair_prompt_includes_missing_transition_directive() -> None:
    result = validate_workflow_draft_text(DRAFT_WITH_MISSING_NEXT_BUT_VALID_SCHEMA)
    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)
    assert "missing_transition" in prompt
    assert "Add next" in prompt
```

Add a convergence test where the first assistant draft lacks `next`, and the repair turn appends a corrected full package with transitions.

- [ ] **Step 2: Run failing repair tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair.py -q
```

Expected: new tests fail because draft validation does not include quality-gate issues.

- [ ] **Step 3: Extend validation result handling carefully**

Do not replace `WorkflowDraftValidationResult`. Add a helper in `repair.py`:

```python
def quality_gate_issues_for_valid_draft(result: WorkflowDraftValidationResult) -> tuple[object, ...]:
    if not result.ok or result.documents is None:
        return ()
    quality = check_workflow_quality_documents(result.documents)
    return quality.issues
```

Then make `compact_workflow_validation_issues`, `compact_workflow_failed_edit_scope`, and directive rendering include quality issues only when normal validation passes.

- [ ] **Step 4: Add repair directives**

In `repair_directives.py`, add cases:

```python
if code == "missing_transition":
    return WorkflowRepairDirective(
        code=code,
        location=location,
        instruction="Add the smallest correct next transition for this non-terminal step.",
        rules=(
            "Preserve existing step ids and order.",
            "Prefer connecting to the next existing step in the intended flow.",
            "Do not redesign unrelated connector actions, permissions, fixtures, or rollback notes.",
        ),
    )
```

Add similar directive cases for `missing_branch_transition`, `terminal_unreachable`, and `fixture_no_terminal`.

- [ ] **Step 5: Run repair tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add appV2.4/appv24/workflows/repair.py appV2.4/appv24/workflows/repair_directives.py appV2.4/tests/test_workflow_repair.py
git commit -m "feat(appv24): repair workflow quality gate failures"
```

---

### Task 6: Staging Prompt Reminder

**Files:**
- Modify: `appV2.4/appv24/workflows/staging.py`
- Test: `appV2.4/tests/test_workflow_staging.py`

- [ ] **Step 1: Add failing staging test**

```python
def test_package_stage_prompt_mentions_terminal_traversal_contract() -> None:
    prompt = build_workflow_stage_prompt("design workflow", stage_id="package", stage_index=3, total_stages=3)
    assert "Every non-terminal action step must include next" in prompt
    assert "dry-run fixture" in prompt
    assert "terminal" in prompt
```

- [ ] **Step 2: Run failing test**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_staging.py::test_package_stage_prompt_mentions_terminal_traversal_contract -q
```

Expected: fails until prompt reminder exists.

- [ ] **Step 3: Add minimal prompt reminder**

In the package stage body, add:

```python
"Every non-terminal action step must include next, every decision step must include branches, and every dry-run fixture must traverse from entrypoint to a terminal step.",
```

- [ ] **Step 4: Run staging tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_staging.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.4/appv24/workflows/staging.py appV2.4/tests/test_workflow_staging.py
git commit -m "docs(appv24): prompt workflow terminal traversal"
```

---

### Task 7: Full Verification And Docs

**Files:**
- Modify: `docs/appv24/workflow-enable-v0.md`
- Test: workflow and full appV2.4 suites

- [ ] **Step 1: Update docs**

Add the quality gate to the documented pipeline:

```text
PackageDocuments
  -> validate_workflow_documents
  -> compile_workflow_documents
  -> evaluate_policy
  -> check_workflow_quality_documents
  -> LocalWorkflowBroker
  -> simulate_workflow_fixture
  -> WorkflowEnablementReport
```

Add a note:

```text
The quality gate requires static terminal reachability and terminal completion for declared dry-run fixtures. It does not execute real connectors.
```

- [ ] **Step 2: Run focused workflow tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_quality_gate.py \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_repair.py \
  appV2.4/tests/test_workflow_staging.py \
  appV2.4/tests/test_workflow_simulator.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run all workflow tests**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests -q
```

Expected: full appV2.4 suite passes.

- [ ] **Step 4: Check zone impact**

Run:

```bash
git diff --name-only HEAD~6..HEAD
```

Expected: only workflow modules, workflow tests, and docs are listed. No red-zone files and no yellow-zone TUI/CLI files.

- [ ] **Step 5: Commit docs**

```bash
git add docs/appv24/workflow-enable-v0.md
git commit -m "docs(appv24): document workflow quality gate"
```

---

## Final Review Checklist

- [ ] `test_workflow_quality_gate.py` proves the exact Mimo TUI failure: missing `next` transitions are rejected before a workflow is considered ready.
- [ ] Golden examples still pass quality gate.
- [ ] Enablement reports block quality-gate failures.
- [ ] Repair prompts include precise quality-gate directives.
- [ ] No red-zone files changed.
- [ ] No yellow-zone files changed in the first implementation cut.
- [ ] Full appV2.4 tests pass.
