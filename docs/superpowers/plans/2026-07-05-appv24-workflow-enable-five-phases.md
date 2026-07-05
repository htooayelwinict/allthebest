# appv24 Workflow Enablement Five Phases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move appv24 from validated natural-language workflow package generation to safe, broker-ready, approval-gated, draft-only connector enablement without changing the red-zone runtime kernel.

**Architecture:** Keep the appv24 workflow system headless and passive until the final pilot. The main path is: repair challenge corpus -> enablement report v1 -> capability catalog -> approval/execution contract ledger -> one draft-only connector pilot. Workflow logic stays in `appV2.4/appv24/workflows/`; CLI/TUI changes are limited to thin reporting commands after workflow APIs pass tests.

**Tech Stack:** Python dataclasses, existing appv24 workflow modules, JSON Schema via `jsonschema`, pytest, deterministic dry-run broker, YAML workflow package fixtures.

---

## Sources And Design Inputs

- `rules.md`: appv24 must grow through profiles/apps/plugins/workflow modules, not red-zone runtime changes.
- Current modules: `validator.py`, `schema.py`, `semantic.py`, `compiler.py`, `ir.py`, `broker.py`, `simulator.py`, `quality_gate.py`, `repair.py`, `repair_directives.py`, `reports.py`, `examples.py`.
- Current tests: `test_workflow_repair.py`, `test_workflow_quality_gate.py`, `test_workflow_broker.py`, `test_workflow_examples.py`, `test_workflow_simulator.py`, `test_cli.py`, `test_tui.py`.
- Context7 Temporal docs: workflow definitions should be deterministic; external side effects belong behind activities/broker-like boundaries; idempotency keys prevent duplicate external effects during retries.
- Context7 LangGraph docs: persisted state and human-in-loop resume are useful patterns for approval gates, but appv24 should not import LangGraph for this phase.
- Context7 n8n docs: workflow automation needs explicit concepts for workflow, node/action, trigger, credential, and execution. appv24 should borrow the vocabulary, not the drag-and-drop runtime model.
- Open Bridge review: strengthen coupling between repair challenge corpus and capability catalog; keep workflow modules headless; ledger must not pollute persistent execution state on dry-run failure.

## Zone Rules

- Never touch red-zone files:
  - `appV2.4/appv24/agent/`
  - `appV2.4/appv24/ai/stream.py`
  - `appV2.4/appv24/ai/validation.py`
  - `appV2.4/appv24/compaction/`
  - `appV2.4/appv24/coding_agent/session_store.py`
  - `appV2.4/appv24/app.py`
- Avoid yellow-zone files until workflow APIs pass:
  - `appV2.4/appv24/cli.py`
  - `appV2.4/appv24/tui/`
  - `appV2.4/appv24/profiles/`
  - `packages/appv24-cli/`
- Allowed green workflow areas:
  - `appV2.4/appv24/workflows/`
  - `appV2.4/tests/test_workflow*.py`
  - `docs/appv24/`
  - `docs/superpowers/specs/`
  - `docs/superpowers/plans/`
- Any yellow-zone change must be a thin wrapper over an already-tested workflow API and must include a focused CLI/TUI test.
- No phase may call real Telegram, Gmail, GitHub, cloud APIs, MCP tools, shell commands, browsers, package managers, or secrets except the explicitly approved Phase 5 draft-only connector pilot, and that pilot must default to dry-run.

## Branch And Commit Strategy

- Phase 0 branch: current `feature/appv24-workflow-quality-gate`.
- Phase 1 branch: `feature/appv24-repair-challenge-corpus`.
- Phase 2 branch: `feature/appv24-enable-report-v1`.
- Phase 3 branch: `feature/appv24-capability-catalog`.
- Phase 4 branch: `feature/appv24-approval-ledger`.
- Phase 5 branch: `feature/appv24-draft-connector-pilot`.
- Each phase must be independently testable and revertible.
- Commit only relevant files per phase.
- Do not release, publish npm, or build GHCR unless Lewis explicitly asks.

## File Structure

### Phase 0: Current Branch Closeout

- Modify existing changed files only if final verification exposes a defect:
  - `appV2.4/appv24/cli.py`
  - `appV2.4/appv24/tui/interactive_mode.py`
  - `appV2.4/appv24/workflows/live_validation.py`
  - `appV2.4/appv24/workflows/prompt_fragments.py`
  - `appV2.4/appv24/workflows/repair_directives.py`
  - `appV2.4/tests/test_cli.py`
  - `appV2.4/tests/test_tui.py`
  - `appV2.4/tests/test_workflow_repair.py`
  - `pyproject.toml`
- Output: committed quality-gate repair/TUI-save enforcement branch.

### Phase 1: Repair Challenge Corpus

- Create `appV2.4/appv24/workflows/challenge_corpus.py`
  - Owns named broken package cases and expected repaired properties.
- Create `appV2.4/tests/test_workflow_challenge_corpus.py`
  - Verifies all challenge cases fail for the intended reason and can be repaired by existing prompt/directive plumbing.
- Modify `appV2.4/appv24/workflows/examples.py`
  - Adds reusable base package builders only if the new corpus would otherwise duplicate large package dictionaries.
- Modify `appV2.4/appv24/workflows/repair_directives.py`
  - Adds missing directives only for corpus failures that are not already actionable.

### Phase 2: Workflow Enablement v1

- Modify `appV2.4/appv24/workflows/reports.py`
  - Upgrades enablement reports from single-fixture summary to package-level report.
- Create `appV2.4/appv24/workflows/enablement.py`
  - Owns `build_workflow_enablement_report()` and capability-plan construction.
- Create `appV2.4/tests/test_workflow_enablement.py`
  - Tests package-level validation, compile, policy, quality gate, broker dry-run, report serialization, and blocked states.
- Modify `appV2.4/appv24/workflows/__init__.py`
  - Exports stable v1 report types only after tests pass.

### Phase 3: Capability Catalog

- Create `appV2.4/appv24/workflows/catalog.py`
  - Owns built-in capability catalog dataclasses, loader, lookup, and validation helpers.
- Create `appV2.4/appv24/workflows/catalog_builtins.py`
  - Contains built-in draft-capable manifests for `telegram.send_message_draft`, `gmail.create_draft`, `github.create_issue_draft`, and local `ticket.create_draft`.
- Modify `appV2.4/appv24/workflows/schema.py`
  - Adds schema fields for catalog references if needed.
- Modify `appV2.4/appv24/workflows/semantic.py`
  - Rejects connector actions not present in the active catalog unless they are marked `custom: true` and `execution_mode: design_only`.
- Create `appV2.4/tests/test_workflow_catalog.py`
  - Tests lookup, rejection, side-effect metadata compatibility, scope rules, and design-only custom actions.

### Phase 4: Approval And Execution Contract Ledger

- Create `appV2.4/appv24/workflows/approval_ledger.py`
  - Owns immutable in-memory approval plan and append-only JSONL serialization helpers.
- Modify `appV2.4/appv24/workflows/ledger.py`
  - Adds execution-contract fields to `StepTrace` only if the simulator needs them.
- Modify `appV2.4/appv24/workflows/simulator.py`
  - Emits approval requirements and idempotency facts into the ledger during dry-run.
- Modify `appV2.4/appv24/workflows/reports.py` or `enablement.py`
  - Includes approval plan and ledger summary in enablement reports.
- Create `appV2.4/tests/test_workflow_approval_ledger.py`
  - Tests immutability, idempotency keys, approval requirements, failure isolation, and JSONL round-trip.

### Phase 5: Draft-Only Connector Pilot

- Create `appV2.4/appv24/workflows/connectors/`
  - New package for pilot connector provider code.
- Create `appV2.4/appv24/workflows/connectors/__init__.py`
  - Exports no provider by default.
- Create `appV2.4/appv24/workflows/connectors/draft_gmail.py`
  - Draft-only provider shape for `gmail.create_draft`.
- Create `appV2.4/appv24/workflows/connectors/draft_telegram.py`
  - Draft-only provider shape for `telegram.send_message_draft`.
- Create `appV2.4/tests/test_workflow_draft_connectors.py`
  - Tests dry-run default, approval requirement, no-send enforcement, no-network fake transport, output allowlist, and raw secret rejection.
- Optional yellow-zone files after workflow tests pass:
  - `appV2.4/appv24/cli.py`
  - `appV2.4/appv24/tui/interactive_mode.py`
  - Add only a display/save command for enablement reports, not real execution.

---

## Phase 0: Close Current Quality-Gate Branch

### Task 0.1: Verify Current Branch

**Files:**
- Read: current modified files from `git status --short`
- Test: existing focused suite

- [ ] **Step 1: Confirm changed files**

Run:

```bash
git status --short
```

Expected changed files:

```text
 M appV2.4/appv24/cli.py
 M appV2.4/appv24/tui/interactive_mode.py
 M appV2.4/appv24/workflows/live_validation.py
 M appV2.4/appv24/workflows/prompt_fragments.py
 M appV2.4/appv24/workflows/repair_directives.py
 M appV2.4/tests/test_cli.py
 M appV2.4/tests/test_tui.py
 M appV2.4/tests/test_workflow_repair.py
 M pyproject.toml
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_repair.py \
  appV2.4/tests/test_workflow_quality_gate.py \
  appV2.4/tests/test_workflow_simulator.py \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_save_writes_last_valid_draft \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_save_rejects_quality_gate_failure \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_save_writes_last_valid_draft \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_save_rejects_quality_gate_failure -q
```

Expected:

```text
47 passed
```

- [ ] **Step 3: Run all appv24 tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests -q
```

Expected: all tests pass.

- [ ] **Step 4: Review zone impact**

Run:

```bash
git diff --name-only
```

Expected: no files under:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

- [ ] **Step 5: Commit current branch**

Run:

```bash
git add \
  appV2.4/appv24/cli.py \
  appV2.4/appv24/tui/interactive_mode.py \
  appV2.4/appv24/workflows/live_validation.py \
  appV2.4/appv24/workflows/prompt_fragments.py \
  appV2.4/appv24/workflows/repair_directives.py \
  appV2.4/tests/test_cli.py \
  appV2.4/tests/test_tui.py \
  appV2.4/tests/test_workflow_repair.py \
  pyproject.toml
git commit -m "feat(appv24): enforce workflow quality gate on save"
```

Expected: one commit containing only current intended quality-gate save enforcement work.

---

## Phase 1: Repair Challenge Corpus

### Task 1.1: Add Corpus Case Types

**Files:**
- Create: `appV2.4/appv24/workflows/challenge_corpus.py`
- Create: `appV2.4/tests/test_workflow_challenge_corpus.py`

- [ ] **Step 1: Write failing test for corpus inventory**

Create `appV2.4/tests/test_workflow_challenge_corpus.py`:

```python
from __future__ import annotations

from appv24.workflows.challenge_corpus import workflow_repair_challenges


def test_workflow_repair_challenge_inventory_has_required_cases() -> None:
    names = set(workflow_repair_challenges())

    assert {
        "missing_next",
        "missing_branches",
        "missing_fixture_decision",
        "wrong_fixture_decision_key",
        "target_step_fixture_decision",
        "unreachable_terminal",
        "bad_approval_metadata",
        "design_only_action_used",
    } <= names
```

- [ ] **Step 2: Run failing test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_challenge_corpus.py::test_workflow_repair_challenge_inventory_has_required_cases -q
```

Expected: fails because `challenge_corpus.py` does not exist.

- [ ] **Step 3: Add corpus dataclass and inventory**

Create `appV2.4/appv24/workflows/challenge_corpus.py`:

```python
"""Broken workflow package cases used to regression-test repair behavior."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Callable

from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class WorkflowRepairChallenge:
    name: str
    documents: PackageDocuments
    expected_issue_codes: tuple[str, ...]
    expected_repaired_paths: tuple[str, ...]


ChallengeBuilder = Callable[[], WorkflowRepairChallenge]


def workflow_repair_challenges() -> dict[str, WorkflowRepairChallenge]:
    return {name: builder() for name, builder in _BUILDERS.items()}


def _base() -> PackageDocuments:
    return golden_workflow_examples()["telegram_support_ticket"].documents


def _missing_next() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    workflow["steps"][1].pop("next", None)
    return WorkflowRepairChallenge(
        name="missing_next",
        documents=replace(base, workflow=workflow),
        expected_issue_codes=("missing_transition",),
        expected_repaired_paths=("workflow.yaml:steps.create_ticket_draft.next",),
    )


def _missing_branches() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    workflow["steps"][0].pop("branches", None)
    return WorkflowRepairChallenge(
        name="missing_branches",
        documents=replace(base, workflow=workflow),
        expected_issue_codes=("missing_branch_transition",),
        expected_repaired_paths=("workflow.yaml:steps.check_authorized_customer.branches",),
    )


def _missing_fixture_decision() -> WorkflowRepairChallenge:
    base = _base()
    fixtures = deepcopy(base.dry_run_fixtures)
    fixtures["happy_path.yaml"].pop("decision", None)
    return WorkflowRepairChallenge(
        name="missing_fixture_decision",
        documents=replace(base, dry_run_fixtures=fixtures),
        expected_issue_codes=("fixture_branch_unmatched",),
        expected_repaired_paths=("dry_run_fixtures/happy_path.yaml:decision.check_authorized_customer",),
    )


def _wrong_fixture_decision_key() -> WorkflowRepairChallenge:
    base = _base()
    fixtures = deepcopy(base.dry_run_fixtures)
    fixtures["happy_path.yaml"] = {"decision": {"check_path": "authorized"}}
    fixtures["rejection_path.yaml"] = {"decision": {"check_path": "rejected"}}
    return WorkflowRepairChallenge(
        name="wrong_fixture_decision_key",
        documents=replace(base, dry_run_fixtures=fixtures),
        expected_issue_codes=("fixture_branch_unmatched",),
        expected_repaired_paths=("dry_run_fixtures/happy_path.yaml:decision.check_authorized_customer",),
    )


def _target_step_fixture_decision() -> WorkflowRepairChallenge:
    base = _base()
    fixtures = deepcopy(base.dry_run_fixtures)
    fixtures["happy_path.yaml"] = {"decision": {"check_authorized_customer": "create_ticket_draft"}}
    fixtures["rejection_path.yaml"] = {"decision": {"check_authorized_customer": "rejected"}}
    return WorkflowRepairChallenge(
        name="target_step_fixture_decision",
        documents=replace(base, dry_run_fixtures=fixtures),
        expected_issue_codes=("fixture_branch_unmatched",),
        expected_repaired_paths=("dry_run_fixtures/happy_path.yaml:decision.check_authorized_customer",),
    )


def _unreachable_terminal() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    workflow["steps"][1]["next"] = "notify_admin"
    workflow["steps"][2]["next"] = "create_ticket_draft"
    return WorkflowRepairChallenge(
        name="unreachable_terminal",
        documents=replace(base, workflow=workflow),
        expected_issue_codes=("terminal_unreachable", "cycle_without_terminal_escape"),
        expected_repaired_paths=("workflow.yaml:steps.notify_admin.next",),
    )


def _bad_approval_metadata() -> WorkflowRepairChallenge:
    base = _base()
    permissions = deepcopy(base.permissions)
    permissions["approvals"][0]["required_for"] = ["create_ticket_draft"]
    return WorkflowRepairChallenge(
        name="bad_approval_metadata",
        documents=replace(base, permissions=permissions),
        expected_issue_codes=("missing_approval",),
        expected_repaired_paths=("permissions.yaml:approvals.0.required_for",),
    )


def _design_only_action_used() -> WorkflowRepairChallenge:
    base = _base()
    connectors = deepcopy(base.connectors)
    connectors["connectors"][0]["actions"][0]["execution_mode"] = "design_only"
    return WorkflowRepairChallenge(
        name="design_only_action_used",
        documents=replace(base, connectors=connectors),
        expected_issue_codes=("design_only_action_used", "invalid_side_effect_contract"),
        expected_repaired_paths=("workflow.yaml:steps.create_ticket_draft",),
    )


_BUILDERS: dict[str, ChallengeBuilder] = {
    "missing_next": _missing_next,
    "missing_branches": _missing_branches,
    "missing_fixture_decision": _missing_fixture_decision,
    "wrong_fixture_decision_key": _wrong_fixture_decision_key,
    "target_step_fixture_decision": _target_step_fixture_decision,
    "unreachable_terminal": _unreachable_terminal,
    "bad_approval_metadata": _bad_approval_metadata,
    "design_only_action_used": _design_only_action_used,
}
```

- [ ] **Step 4: Run inventory test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_challenge_corpus.py::test_workflow_repair_challenge_inventory_has_required_cases -q
```

Expected: pass.

### Task 1.2: Verify Challenge Failures Are Precise

**Files:**
- Modify: `appV2.4/tests/test_workflow_challenge_corpus.py`

- [ ] **Step 1: Add quality-gate failure test**

Append:

```python
from appv24.workflows.quality_gate import check_workflow_quality_documents


def test_workflow_repair_challenges_fail_with_expected_issue_codes() -> None:
    for name, challenge in workflow_repair_challenges().items():
        result = check_workflow_quality_documents(challenge.documents)
        codes = {issue.code for issue in result.issues}

        assert not result.ok, name
        assert set(challenge.expected_issue_codes) & codes, (name, codes)
```

- [ ] **Step 2: Run failure test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_challenge_corpus.py::test_workflow_repair_challenges_fail_with_expected_issue_codes -q
```

Expected: pass or expose a corpus case that fails at the wrong layer. If a case fails at the wrong layer, adjust only the case data so it isolates the intended defect.

### Task 1.3: Add Corpus-Aware Repair Directive Tests

**Files:**
- Modify: `appV2.4/tests/test_workflow_challenge_corpus.py`
- Modify only if tests fail: `appV2.4/appv24/workflows/repair_directives.py`

- [ ] **Step 1: Add directive coverage test**

Append:

```python
from appv24.workflows.repair import build_workflow_repair_prompt
from appv24.workflows.validator import validate_workflow_draft_text
import yaml


def _draft_text(challenge_name: str) -> str:
    documents = workflow_repair_challenges()[challenge_name].documents
    fixture_blocks = "\n\n".join(
        f"```yaml dry_run_fixtures/{fixture_name}\n{yaml.safe_dump(fixture, sort_keys=False)}```"
        for fixture_name, fixture in sorted(documents.dry_run_fixtures.items())
    )
    return "\n\n".join(
        block
        for block in (
            f"```yaml workflow.yaml\n{yaml.safe_dump(documents.workflow, sort_keys=False)}```",
            f"```yaml connectors.yaml\n{yaml.safe_dump(documents.connectors, sort_keys=False)}```",
            f"```yaml permissions.yaml\n{yaml.safe_dump(documents.permissions, sort_keys=False)}```",
            fixture_blocks,
            f"```markdown rollback.md\n{documents.rollback_text or ''}```",
        )
        if block.strip()
    )


def test_workflow_repair_challenge_prompts_include_actionable_directives() -> None:
    for name, challenge in workflow_repair_challenges().items():
        result = validate_workflow_draft_text(_draft_text(name))
        prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=12)

        for code in challenge.expected_issue_codes:
            assert code in prompt, (name, code)
        for repaired_path in challenge.expected_repaired_paths:
            assert repaired_path.split(":", 1)[0] in prompt, (name, repaired_path)
```

- [ ] **Step 2: Run directive coverage test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_challenge_corpus.py::test_workflow_repair_challenge_prompts_include_actionable_directives -q
```

Expected: pass. If it fails, add targeted directive text in `repair_directives.py`; do not broaden the prompt globally.

### Task 1.4: Commit Phase 1

- [ ] **Step 1: Run phase tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_challenge_corpus.py \
  appV2.4/tests/test_workflow_repair.py \
  appV2.4/tests/test_workflow_quality_gate.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Commit**

Run:

```bash
git add appV2.4/appv24/workflows/challenge_corpus.py appV2.4/tests/test_workflow_challenge_corpus.py appV2.4/appv24/workflows/repair_directives.py
git commit -m "test(appv24): add workflow repair challenge corpus"
```

Expected: commit includes corpus and any targeted directive improvements.

---

## Phase 2: Workflow Enablement v1

### Task 2.1: Add Package-Level Enablement Report Types

**Files:**
- Create: `appV2.4/appv24/workflows/enablement.py`
- Create: `appV2.4/tests/test_workflow_enablement.py`
- Modify in Task 2.3: `appV2.4/appv24/workflows/__init__.py`

- [ ] **Step 1: Write failing report test**

Create `appV2.4/tests/test_workflow_enablement.py`:

```python
from __future__ import annotations

from appv24.workflows.enablement import build_workflow_enablement_report
from appv24.workflows.examples import golden_workflow_examples


def test_enablement_v1_report_covers_all_fixtures_and_capabilities() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    report = build_workflow_enablement_report(example.documents, broker=example.broker)

    assert report.workflow_name == "telegram_support_ticket"
    assert report.validation_ok is True
    assert report.compile_ok is True
    assert report.policy_ok is True
    assert report.quality_ok is True
    assert report.ok is True
    assert tuple(item.action for item in report.capability_plan) == (
        "ticket.create_draft",
        "telegram.send_message",
    )
    assert {fixture.fixture_name for fixture in report.fixtures} == {"happy_path.yaml", "rejection_path.yaml"}
    assert report.issues == ()
```

- [ ] **Step 2: Run failing report test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_enablement.py::test_enablement_v1_report_covers_all_fixtures_and_capabilities -q
```

Expected: fails because `enablement.py` does not exist.

- [ ] **Step 3: Implement report dataclasses**

Create `appV2.4/appv24/workflows/enablement.py`:

```python
"""Package-level workflow enablement reports for appv24."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.ir import StepKind, WorkflowIR
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.quality_gate import WorkflowFixtureQualityReport, check_workflow_quality_documents


@dataclass(frozen=True)
class CapabilityPlanItem:
    step_id: str
    action: str
    side_effect: bool
    side_effect_class: str | None
    execution_mode: str | None
    approval_required: bool | None
    approval_id: str | None
    dry_run_supported: bool | None
    idempotency_key_fields: tuple[str, ...]
    allowed_outputs: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowEnablementV1Report:
    workflow_name: str
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    quality_ok: bool
    capability_plan: tuple[CapabilityPlanItem, ...]
    fixtures: tuple[WorkflowFixtureQualityReport, ...]
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.validation_ok and self.compile_ok and self.policy_ok and self.quality_ok and not self.issues

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 4: Implement report builder**

Append:

```python
def build_workflow_enablement_report(
    documents: PackageDocuments,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowEnablementV1Report:
    workflow_name = str(documents.workflow.get("name", ""))
    quality = check_workflow_quality_documents(documents, broker=broker)
    capability_plan: tuple[CapabilityPlanItem, ...] = ()

    compiled = compile_workflow_documents(documents)
    if compiled.ir is not None:
        workflow_name = compiled.ir.name
        capability_plan = tuple(_capability_plan(compiled.ir))

    return WorkflowEnablementV1Report(
        workflow_name=workflow_name,
        validation_ok=quality.validation_ok,
        compile_ok=quality.compile_ok,
        policy_ok=quality.policy_ok,
        quality_ok=quality.ok,
        capability_plan=capability_plan,
        fixtures=quality.fixture_reports,
        issues=tuple(issue.message for issue in quality.issues),
    )


def _capability_plan(ir: WorkflowIR) -> list[CapabilityPlanItem]:
    items: list[CapabilityPlanItem] = []
    for step in ir.steps.values():
        if step.kind is StepKind.TERMINAL or step.uses is None:
            continue
        connector = ir.connectors.get(step.uses.connector_id)
        action = connector.actions.get(step.uses.action_id) if connector is not None else None
        if action is None:
            continue
        items.append(
            CapabilityPlanItem(
                step_id=step.step_id,
                action=step.uses.dotted,
                side_effect=action.side_effect,
                side_effect_class=action.side_effect_class,
                execution_mode=action.execution_mode,
                approval_required=action.approval_required,
                approval_id=step.approval,
                dry_run_supported=action.dry_run_supported,
                idempotency_key_fields=action.idempotency_key_fields,
                allowed_outputs=action.allowed_outputs,
            )
        )
    return items
```

- [ ] **Step 5: Run report test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_enablement.py::test_enablement_v1_report_covers_all_fixtures_and_capabilities -q
```

Expected: pass.

### Task 2.2: Block Reports On Quality-Gate Failures

**Files:**
- Modify: `appV2.4/tests/test_workflow_enablement.py`
- Modify: `appV2.4/appv24/workflows/enablement.py`

- [ ] **Step 1: Add blocked report test**

Append:

```python
from copy import deepcopy
from dataclasses import replace


def test_enablement_v1_report_blocks_quality_gate_failures() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = deepcopy(example.documents.workflow)
    workflow["steps"][1].pop("next", None)
    documents = replace(example.documents, workflow=workflow)

    report = build_workflow_enablement_report(documents, broker=example.broker)

    assert report.ok is False
    assert report.validation_ok is True
    assert report.compile_ok is True
    assert report.policy_ok is True
    assert report.quality_ok is False
    assert any("no next transition" in issue for issue in report.issues)
```

- [ ] **Step 2: Run blocked report test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_enablement.py::test_enablement_v1_report_blocks_quality_gate_failures -q
```

Expected: pass with the Task 2.1 implementation.

### Task 2.3: Export Stable Enablement API

**Files:**
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Modify: `appV2.4/tests/test_workflow_enablement.py`

- [ ] **Step 1: Add public export test**

Append:

```python
def test_enablement_v1_public_api_exports() -> None:
    import appv24.workflows as workflows

    assert hasattr(workflows, "CapabilityPlanItem")
    assert hasattr(workflows, "WorkflowEnablementV1Report")
    assert hasattr(workflows, "build_workflow_enablement_report")
```

- [ ] **Step 2: Run failing export test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_enablement.py::test_enablement_v1_public_api_exports -q
```

Expected: fails until exports are added.

- [ ] **Step 3: Add exports**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.enablement import (
    CapabilityPlanItem,
    WorkflowEnablementV1Report,
    build_workflow_enablement_report,
)
```

Add to `__all__`:

```python
"CapabilityPlanItem",
"WorkflowEnablementV1Report",
"build_workflow_enablement_report",
```

- [ ] **Step 4: Run export test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_enablement.py::test_enablement_v1_public_api_exports -q
```

Expected: pass.

### Task 2.4: Commit Phase 2

- [ ] **Step 1: Run phase tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_enablement.py \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_quality_gate.py \
  appV2.4/tests/test_workflow_broker.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Commit**

Run:

```bash
git add appV2.4/appv24/workflows/enablement.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_enablement.py
git commit -m "feat(appv24): add workflow enablement v1 report"
```

Expected: one commit with headless workflow API changes only.

---

## Phase 3: Capability Catalog

### Task 3.1: Add Built-In Capability Catalog

**Files:**
- Create: `appV2.4/appv24/workflows/catalog.py`
- Create: `appV2.4/appv24/workflows/catalog_builtins.py`
- Create: `appV2.4/tests/test_workflow_catalog.py`

- [ ] **Step 1: Write failing catalog lookup test**

Create `appV2.4/tests/test_workflow_catalog.py`:

```python
from __future__ import annotations

from appv24.workflows.catalog import built_in_capability_catalog


def test_built_in_catalog_contains_draft_safe_actions() -> None:
    catalog = built_in_capability_catalog()

    assert catalog.require("ticket.create_draft").execution_mode == "brokered"
    assert catalog.require("telegram.send_message_draft").side_effect_class == "send"
    assert catalog.require("gmail.create_draft").side_effect_class == "create"
    assert catalog.require("github.create_issue_draft").dry_run_supported is True
```

- [ ] **Step 2: Run failing catalog test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_catalog.py::test_built_in_catalog_contains_draft_safe_actions -q
```

Expected: fails because `catalog.py` does not exist.

- [ ] **Step 3: Implement catalog dataclasses**

Create `appV2.4/appv24/workflows/catalog.py`:

```python
"""Capability catalog contracts for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class CatalogCapability:
    action: str
    adapter: str
    side_effect: bool
    side_effect_class: str | None
    execution_mode: str
    approval_required: bool
    dry_run_supported: bool
    required_scopes: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    idempotency_key_fields: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityCatalog:
    capabilities: Mapping[str, CatalogCapability]

    def get(self, action: str) -> CatalogCapability | None:
        return self.capabilities.get(action)

    def require(self, action: str) -> CatalogCapability:
        capability = self.get(action)
        if capability is None:
            raise KeyError(action)
        return capability


def built_in_capability_catalog() -> CapabilityCatalog:
    from appv24.workflows.catalog_builtins import BUILT_IN_CAPABILITIES

    return CapabilityCatalog(BUILT_IN_CAPABILITIES)
```

- [ ] **Step 4: Implement built-ins**

Create `appV2.4/appv24/workflows/catalog_builtins.py`:

```python
"""Built-in appv24 draft-safe capability manifests."""

from __future__ import annotations

from appv24.workflows.catalog import CatalogCapability


BUILT_IN_CAPABILITIES: dict[str, CatalogCapability] = {
    "ticket.create_draft": CatalogCapability(
        action="ticket.create_draft",
        adapter="manual",
        side_effect=True,
        side_effect_class="create",
        execution_mode="brokered",
        approval_required=True,
        dry_run_supported=True,
        required_scopes=("ticket:draft",),
        allowed_outputs=("ticket_id",),
        idempotency_key_fields=("source_id", "summary"),
    ),
    "telegram.send_message_draft": CatalogCapability(
        action="telegram.send_message_draft",
        adapter="direct_api",
        side_effect=True,
        side_effect_class="send",
        execution_mode="brokered",
        approval_required=True,
        dry_run_supported=True,
        required_scopes=("telegram:draft",),
        allowed_outputs=("draft_id",),
        idempotency_key_fields=("chat_id", "message_hash"),
    ),
    "gmail.create_draft": CatalogCapability(
        action="gmail.create_draft",
        adapter="direct_api",
        side_effect=True,
        side_effect_class="create",
        execution_mode="brokered",
        approval_required=True,
        dry_run_supported=True,
        required_scopes=("gmail:draft",),
        allowed_outputs=("draft_id", "thread_id"),
        idempotency_key_fields=("to", "subject", "body_hash"),
    ),
    "github.create_issue_draft": CatalogCapability(
        action="github.create_issue_draft",
        adapter="direct_api",
        side_effect=True,
        side_effect_class="create",
        execution_mode="brokered",
        approval_required=True,
        dry_run_supported=True,
        required_scopes=("issues:draft",),
        allowed_outputs=("draft_id",),
        idempotency_key_fields=("repository", "title", "body_hash"),
    ),
}
```

- [ ] **Step 5: Run catalog lookup test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_catalog.py::test_built_in_catalog_contains_draft_safe_actions -q
```

Expected: pass.

### Task 3.2: Validate Connector Actions Against Catalog

**Files:**
- Modify: `appV2.4/appv24/workflows/semantic.py`
- Modify: `appV2.4/tests/test_workflow_catalog.py`

- [ ] **Step 1: Add unknown catalog action test**

Append:

```python
from copy import deepcopy
from dataclasses import replace

from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.validator import validate_workflow_documents


def test_catalog_rejects_unknown_brokered_action() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    connectors = deepcopy(example.documents.connectors)
    connectors["connectors"][0]["actions"][0]["id"] = "unknown_external_send"
    workflow = deepcopy(example.documents.workflow)
    workflow["steps"][1]["uses"] = "ticket.unknown_external_send"
    documents = replace(example.documents, workflow=workflow, connectors=connectors)

    result = validate_workflow_documents(documents)

    assert "unknown_catalog_action" in {issue.code for issue in result.issues}
```

- [ ] **Step 2: Run failing unknown action test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_catalog.py::test_catalog_rejects_unknown_brokered_action -q
```

Expected: fails until semantic validation checks the catalog.

- [ ] **Step 3: Add catalog check to semantic validation**

Modify `appV2.4/appv24/workflows/semantic.py`:

```python
from appv24.workflows.catalog import built_in_capability_catalog
```

Add inside `validate_semantics()` after `action_entries` is built:

```python
    issues.extend(_catalog_issues(action_entries))
```

Add helper:

```python
def _catalog_issues(actions: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[WorkflowIssue]:
    catalog = built_in_capability_catalog()
    issues: list[WorkflowIssue] = []
    for (connector_id, action_id), action in sorted(actions.items()):
        action_ref = f"{connector_id}.{action_id}"
        if action.get("custom") is True and action.get("execution_mode") == "design_only":
            continue
        if catalog.get(action_ref) is None:
            issues.append(
                WorkflowIssue(
                    "unknown_catalog_action",
                    "connectors.yaml",
                    (),
                    f"{action_ref} is not in the built-in capability catalog",
                    repair_hint="Use a built-in catalog action or mark custom design-only actions with custom: true and execution_mode: design_only.",
                )
            )
    return issues
```

- [ ] **Step 4: Run unknown action test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_catalog.py::test_catalog_rejects_unknown_brokered_action -q
```

Expected: pass.

### Task 3.3: Enforce Catalog Metadata Compatibility

**Files:**
- Modify: `appV2.4/appv24/workflows/semantic.py`
- Modify: `appV2.4/tests/test_workflow_catalog.py`

- [ ] **Step 1: Add metadata mismatch test**

Append:

```python
def test_catalog_rejects_side_effect_metadata_mismatch() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    connectors = deepcopy(example.documents.connectors)
    connectors["connectors"][0]["actions"][0]["side_effect_class"] = "delete"
    documents = replace(example.documents, connectors=connectors)

    result = validate_workflow_documents(documents)

    assert "catalog_metadata_mismatch" in {issue.code for issue in result.issues}
```

- [ ] **Step 2: Run failing mismatch test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_catalog.py::test_catalog_rejects_side_effect_metadata_mismatch -q
```

Expected: fails until catalog compatibility is checked.

- [ ] **Step 3: Extend `_catalog_issues` compatibility checks**

Replace the body after `capability = catalog.get(action_ref)`:

```python
        capability = catalog.get(action_ref)
        if capability is None:
            issues.append(
                WorkflowIssue(
                    "unknown_catalog_action",
                    "connectors.yaml",
                    (),
                    f"{action_ref} is not in the built-in capability catalog",
                    repair_hint="Use a built-in catalog action or mark custom design-only actions with custom: true and execution_mode: design_only.",
                )
            )
            continue
        expected = {
            "side_effect": capability.side_effect,
            "side_effect_class": capability.side_effect_class,
            "execution_mode": capability.execution_mode,
            "approval_required": capability.approval_required,
            "dry_run_supported": capability.dry_run_supported,
        }
        for field, expected_value in expected.items():
            if action.get(field) != expected_value:
                issues.append(
                    WorkflowIssue(
                        "catalog_metadata_mismatch",
                        "connectors.yaml",
                        (),
                        f"{action_ref} {field} must match catalog value {expected_value!r}",
                    )
                )
```

- [ ] **Step 4: Run catalog tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_catalog.py -q
```

Expected: all catalog tests pass.

### Task 3.4: Add Catalog-Aware Corpus Cases

**Files:**
- Modify: `appV2.4/appv24/workflows/challenge_corpus.py`
- Modify: `appV2.4/tests/test_workflow_challenge_corpus.py`

- [ ] **Step 1: Add inventory assertion**

Update the inventory assertion to include:

```python
"unknown_catalog_action",
"catalog_metadata_mismatch",
```

- [ ] **Step 2: Add challenge builders**

Add builders in `challenge_corpus.py`:

```python
def _unknown_catalog_action() -> WorkflowRepairChallenge:
    base = _base()
    workflow = deepcopy(base.workflow)
    connectors = deepcopy(base.connectors)
    connectors["connectors"][0]["actions"][0]["id"] = "unknown_external_send"
    workflow["steps"][1]["uses"] = "ticket.unknown_external_send"
    return WorkflowRepairChallenge(
        name="unknown_catalog_action",
        documents=replace(base, workflow=workflow, connectors=connectors),
        expected_issue_codes=("unknown_catalog_action",),
        expected_repaired_paths=("connectors.yaml:connectors.0.actions.0.id",),
    )


def _catalog_metadata_mismatch() -> WorkflowRepairChallenge:
    base = _base()
    connectors = deepcopy(base.connectors)
    connectors["connectors"][0]["actions"][0]["side_effect_class"] = "delete"
    return WorkflowRepairChallenge(
        name="catalog_metadata_mismatch",
        documents=replace(base, connectors=connectors),
        expected_issue_codes=("catalog_metadata_mismatch",),
        expected_repaired_paths=("connectors.yaml:connectors.0.actions.0.side_effect_class",),
    )
```

Add both to `_BUILDERS`.

- [ ] **Step 3: Run corpus tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_challenge_corpus.py appV2.4/tests/test_workflow_catalog.py -q
```

Expected: all selected tests pass.

### Task 3.5: Commit Phase 3

- [ ] **Step 1: Run phase tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_catalog.py \
  appV2.4/tests/test_workflow_challenge_corpus.py \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_packages.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Commit**

Run:

```bash
git add \
  appV2.4/appv24/workflows/catalog.py \
  appV2.4/appv24/workflows/catalog_builtins.py \
  appV2.4/appv24/workflows/semantic.py \
  appV2.4/appv24/workflows/challenge_corpus.py \
  appV2.4/tests/test_workflow_catalog.py \
  appV2.4/tests/test_workflow_challenge_corpus.py
git commit -m "feat(appv24): add workflow capability catalog"
```

Expected: one commit with catalog and catalog-aware challenge cases.

---

## Phase 4: Approval And Execution Contract Ledger

### Task 4.1: Add Approval Plan Dataclasses

**Files:**
- Create: `appV2.4/appv24/workflows/approval_ledger.py`
- Create: `appV2.4/tests/test_workflow_approval_ledger.py`

- [ ] **Step 1: Write failing approval plan test**

Create `appV2.4/tests/test_workflow_approval_ledger.py`:

```python
from __future__ import annotations

from appv24.workflows.approval_ledger import build_approval_plan
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.examples import golden_workflow_examples


def test_approval_plan_lists_mutating_steps_with_idempotency() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None

    plan = build_approval_plan(compiled.ir)

    assert tuple(item.step_id for item in plan.items) == ("create_ticket_draft", "notify_admin")
    assert all(item.approval_id == "admin_approval" for item in plan.items)
    assert all(item.idempotency_key_fields for item in plan.items)
```

- [ ] **Step 2: Run failing approval plan test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_approval_ledger.py::test_approval_plan_lists_mutating_steps_with_idempotency -q
```

Expected: fails because `approval_ledger.py` does not exist.

- [ ] **Step 3: Implement approval plan**

Create `appV2.4/appv24/workflows/approval_ledger.py`:

```python
"""Approval and execution contract ledger helpers for appv24 workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from appv24.workflows.ir import WorkflowIR
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS


@dataclass(frozen=True)
class ApprovalPlanItem:
    step_id: str
    action: str
    side_effect_class: str
    approval_id: str
    idempotency_key_fields: tuple[str, ...]
    allowed_outputs: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalPlan:
    workflow_name: str
    items: tuple[ApprovalPlanItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_approval_plan(ir: WorkflowIR) -> ApprovalPlan:
    items: list[ApprovalPlanItem] = []
    for step in ir.steps.values():
        if step.uses is None or step.side_effect_class not in MUTATING_SIDE_EFFECTS:
            continue
        connector = ir.connectors.get(step.uses.connector_id)
        action = connector.actions.get(step.uses.action_id) if connector is not None else None
        if action is None or step.approval is None:
            continue
        items.append(
            ApprovalPlanItem(
                step_id=step.step_id,
                action=step.uses.dotted,
                side_effect_class=step.side_effect_class,
                approval_id=step.approval,
                idempotency_key_fields=action.idempotency_key_fields,
                allowed_outputs=action.allowed_outputs,
            )
        )
    return ApprovalPlan(workflow_name=ir.name, items=tuple(items))
```

- [ ] **Step 4: Run approval plan test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_approval_ledger.py::test_approval_plan_lists_mutating_steps_with_idempotency -q
```

Expected: pass.

### Task 4.2: Add Immutable Ledger Events

**Files:**
- Modify: `appV2.4/appv24/workflows/approval_ledger.py`
- Modify: `appV2.4/tests/test_workflow_approval_ledger.py`

- [ ] **Step 1: Add JSONL round-trip test**

Append:

```python
from appv24.workflows.approval_ledger import ApprovalLedgerEvent, encode_ledger_event, decode_ledger_event


def test_approval_ledger_event_round_trip_jsonl() -> None:
    event = ApprovalLedgerEvent(
        event_id="evt-1",
        workflow_name="telegram_support_ticket",
        fixture_name="happy_path.yaml",
        step_id="notify_admin",
        action="telegram.send_message",
        status="planned",
        approval_id="admin_approval",
        idempotency_key="telegram_support_ticket:notify_admin:msg-1",
        issues=(),
    )

    line = encode_ledger_event(event)
    decoded = decode_ledger_event(line)

    assert decoded == event
```

- [ ] **Step 2: Run failing JSONL test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_approval_ledger.py::test_approval_ledger_event_round_trip_jsonl -q
```

Expected: fails until event helpers exist.

- [ ] **Step 3: Implement immutable event helpers**

Append to `approval_ledger.py`:

```python
import json


@dataclass(frozen=True)
class ApprovalLedgerEvent:
    event_id: str
    workflow_name: str
    fixture_name: str
    step_id: str
    action: str
    status: str
    approval_id: str | None
    idempotency_key: str | None
    issues: tuple[str, ...]


def encode_ledger_event(event: ApprovalLedgerEvent) -> str:
    return json.dumps(asdict(event), sort_keys=True, separators=(",", ":"))


def decode_ledger_event(line: str) -> ApprovalLedgerEvent:
    data = json.loads(line)
    return ApprovalLedgerEvent(
        event_id=str(data["event_id"]),
        workflow_name=str(data["workflow_name"]),
        fixture_name=str(data["fixture_name"]),
        step_id=str(data["step_id"]),
        action=str(data["action"]),
        status=str(data["status"]),
        approval_id=data["approval_id"] if isinstance(data.get("approval_id"), str) else None,
        idempotency_key=data["idempotency_key"] if isinstance(data.get("idempotency_key"), str) else None,
        issues=tuple(str(issue) for issue in data.get("issues", [])),
    )
```

- [ ] **Step 4: Run JSONL test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_approval_ledger.py::test_approval_ledger_event_round_trip_jsonl -q
```

Expected: pass.

### Task 4.3: Integrate Approval Plan Into Enablement Report

**Files:**
- Modify: `appV2.4/appv24/workflows/enablement.py`
- Modify: `appV2.4/tests/test_workflow_enablement.py`

- [ ] **Step 1: Add report approval plan test**

Append to `test_workflow_enablement.py`:

```python
def test_enablement_v1_report_includes_approval_plan() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    report = build_workflow_enablement_report(example.documents, broker=example.broker)

    assert tuple(item.step_id for item in report.approval_plan.items) == (
        "create_ticket_draft",
        "notify_admin",
    )
```

- [ ] **Step 2: Run failing report approval test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_enablement.py::test_enablement_v1_report_includes_approval_plan -q
```

Expected: fails until report includes approval plan.

- [ ] **Step 3: Add approval plan field**

Modify `enablement.py`:

```python
from appv24.workflows.approval_ledger import ApprovalPlan, build_approval_plan
```

Add field to `WorkflowEnablementV1Report`:

```python
    approval_plan: ApprovalPlan
```

In `build_workflow_enablement_report()` initialize:

```python
    approval_plan = ApprovalPlan(workflow_name=workflow_name, items=())
```

After compile succeeds:

```python
        approval_plan = build_approval_plan(compiled.ir)
```

Pass `approval_plan=approval_plan` to the return value.

- [ ] **Step 4: Run enablement tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_enablement.py appV2.4/tests/test_workflow_approval_ledger.py -q
```

Expected: all selected tests pass.

### Task 4.4: Commit Phase 4

- [ ] **Step 1: Run phase tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_approval_ledger.py \
  appV2.4/tests/test_workflow_enablement.py \
  appV2.4/tests/test_workflow_simulator.py \
  appV2.4/tests/test_workflow_examples.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Commit**

Run:

```bash
git add \
  appV2.4/appv24/workflows/approval_ledger.py \
  appV2.4/appv24/workflows/enablement.py \
  appV2.4/tests/test_workflow_approval_ledger.py \
  appV2.4/tests/test_workflow_enablement.py
git commit -m "feat(appv24): add workflow approval ledger contract"
```

Expected: one commit with approval contract and report integration.

---

## Phase 5: Draft-Only Connector Pilot

### Task 5.1: Add Draft Connector Provider Interface

**Files:**
- Create: `appV2.4/appv24/workflows/connectors/__init__.py`
- Create: `appV2.4/appv24/workflows/connectors/draft_gmail.py`
- Create: `appV2.4/tests/test_workflow_draft_connectors.py`

- [ ] **Step 1: Write dry-run default test**

Create `appV2.4/tests/test_workflow_draft_connectors.py`:

```python
from __future__ import annotations

from appv24.workflows.broker import BrokerRequest
from appv24.workflows.connectors.draft_gmail import gmail_create_draft_provider


def test_gmail_draft_provider_dry_run_never_sends() -> None:
    result = gmail_create_draft_provider(
        BrokerRequest(
            step_id="draft_reply",
            connector_action="gmail.create_draft",
            input_ref={"with": {"to": "admin@example.com", "subject": "Ticket", "body": "Draft only"}},
            fixture={},
            allowed_outputs=("draft_id", "thread_id"),
            dry_run=True,
        )
    )

    assert result.status == "simulated"
    assert result.provider_id == "gmail_draft"
    assert set(result.outputs) == {"draft_id", "thread_id"}
    assert result.issues == ()
```

- [ ] **Step 2: Run failing connector test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_draft_connectors.py::test_gmail_draft_provider_dry_run_never_sends -q
```

Expected: fails because connector module does not exist.

- [ ] **Step 3: Create connector package**

Create `appV2.4/appv24/workflows/connectors/__init__.py`:

```python
"""Draft-only workflow connector providers."""
```

Create `appV2.4/appv24/workflows/connectors/draft_gmail.py`:

```python
"""Draft-only Gmail provider for appv24 workflow broker tests."""

from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult


def gmail_create_draft_provider(request: BrokerRequest) -> BrokerResult:
    if not request.dry_run:
        return BrokerResult(
            status="blocked",
            provider_id="gmail_draft",
            outputs={},
            issues=("gmail draft provider only supports dry-run in appv24 phase 5",),
        )
    return BrokerResult(
        status="simulated",
        provider_id="gmail_draft",
        outputs={
            "draft_id": f"dryrun-{request.step_id}",
            "thread_id": f"thread-{request.step_id}",
        },
    )
```

- [ ] **Step 4: Run dry-run default test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_draft_connectors.py::test_gmail_draft_provider_dry_run_never_sends -q
```

Expected: pass.

### Task 5.2: Block Non-Dry-Run Draft Provider Calls

**Files:**
- Modify: `appV2.4/tests/test_workflow_draft_connectors.py`

- [ ] **Step 1: Add non-dry-run block test**

Append:

```python
def test_gmail_draft_provider_blocks_non_dry_run() -> None:
    result = gmail_create_draft_provider(
        BrokerRequest(
            step_id="draft_reply",
            connector_action="gmail.create_draft",
            input_ref={},
            fixture={},
            allowed_outputs=("draft_id",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("gmail draft provider only supports dry-run in appv24 phase 5",)
```

- [ ] **Step 2: Run non-dry-run test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_draft_connectors.py::test_gmail_draft_provider_blocks_non_dry_run -q
```

Expected: pass.

### Task 5.3: Add Telegram Draft Provider

**Files:**
- Create: `appV2.4/appv24/workflows/connectors/draft_telegram.py`
- Modify: `appV2.4/tests/test_workflow_draft_connectors.py`

- [ ] **Step 1: Add Telegram draft test**

Append:

```python
from appv24.workflows.connectors.draft_telegram import telegram_send_message_draft_provider


def test_telegram_draft_provider_dry_run_never_sends() -> None:
    result = telegram_send_message_draft_provider(
        BrokerRequest(
            step_id="draft_admin_message",
            connector_action="telegram.send_message_draft",
            input_ref={"with": {"chat_id": "admin", "text": "Draft only"}},
            fixture={},
            allowed_outputs=("draft_id",),
            dry_run=True,
        )
    )

    assert result.status == "simulated"
    assert result.provider_id == "telegram_draft"
    assert result.outputs == {"draft_id": "dryrun-draft_admin_message"}
```

- [ ] **Step 2: Run failing Telegram test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_draft_connectors.py::test_telegram_draft_provider_dry_run_never_sends -q
```

Expected: fails because `draft_telegram.py` does not exist.

- [ ] **Step 3: Implement Telegram draft provider**

Create `appV2.4/appv24/workflows/connectors/draft_telegram.py`:

```python
"""Draft-only Telegram provider for appv24 workflow broker tests."""

from __future__ import annotations

from appv24.workflows.broker import BrokerRequest, BrokerResult


def telegram_send_message_draft_provider(request: BrokerRequest) -> BrokerResult:
    if not request.dry_run:
        return BrokerResult(
            status="blocked",
            provider_id="telegram_draft",
            outputs={},
            issues=("telegram draft provider only supports dry-run in appv24 phase 5",),
        )
    return BrokerResult(
        status="simulated",
        provider_id="telegram_draft",
        outputs={"draft_id": f"dryrun-{request.step_id}"},
    )
```

- [ ] **Step 4: Run draft connector tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_draft_connectors.py -q
```

Expected: all selected tests pass.

### Task 5.4: Add Pilot Golden Example

**Files:**
- Modify: `appV2.4/appv24/workflows/examples.py`
- Modify: `appV2.4/tests/test_workflow_examples.py`

- [ ] **Step 1: Add pilot inventory test**

Append to `test_workflow_examples.py`:

```python
def test_golden_examples_include_gmail_draft_triage() -> None:
    examples = golden_workflow_examples()

    assert "gmail_draft_triage" in examples
    assert examples["gmail_draft_triage"].documents.workflow["name"] == "gmail_draft_triage"
```

- [ ] **Step 2: Run failing pilot inventory test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_examples.py::test_golden_examples_include_gmail_draft_triage -q
```

Expected: fails until example is added.

- [ ] **Step 3: Add example function**

Modify `golden_workflow_examples()`:

```python
return {
    "telegram_support_ticket": _telegram_support_ticket(),
    "gmail_draft_triage": _gmail_draft_triage(),
}
```

Add `_gmail_draft_triage()` by copying the shape of `_telegram_support_ticket()` with:

```python
workflow["name"] = "gmail_draft_triage"
workflow["triggers"] = [{"id": "incoming_email", "type": "event"}]
workflow["entrypoint"] = "classify_email"
workflow["steps"] = [
    {
        "id": "classify_email",
        "type": "decision",
        "branches": {"needs_reply": "draft_reply", "ignore": "ignored"},
    },
    {
        "id": "draft_reply",
        "type": "action",
        "uses": "gmail.create_draft",
        "side_effect_class": "create",
        "approval": "human_approval",
        "next": "done",
    },
    {"id": "ignored", "type": "terminal"},
    {"id": "done", "type": "terminal"},
]
```

Use connector action:

```python
{
    "id": "create_draft",
    "operationId": "gmail_create_draft",
    "execution_mode": "brokered",
    "side_effect": True,
    "side_effect_class": "create",
    "approval_required": True,
    "dry_run_supported": True,
    "required_scopes": ["gmail:draft"],
    "outputs": {"allowed": ["draft_id", "thread_id"]},
    "idempotency": {"key_fields": ["to", "subject", "body_hash"]},
}
```

Use broker provider:

```python
"gmail.create_draft": gmail_create_draft_provider
```

- [ ] **Step 4: Run pilot inventory test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_examples.py::test_golden_examples_include_gmail_draft_triage -q
```

Expected: pass.

### Task 5.5: Verify Pilot Through Full Enablement Pipeline

**Files:**
- Modify: `appV2.4/tests/test_workflow_draft_connectors.py`

- [ ] **Step 1: Add full pipeline test**

Append:

```python
from appv24.workflows.enablement import build_workflow_enablement_report
from appv24.workflows.examples import golden_workflow_examples


def test_gmail_draft_pilot_passes_enablement_without_network() -> None:
    example = golden_workflow_examples()["gmail_draft_triage"]

    report = build_workflow_enablement_report(example.documents, broker=example.broker)

    assert report.ok is True
    assert report.issues == ()
    assert tuple(item.action for item in report.capability_plan) == ("gmail.create_draft",)
    assert tuple(item.step_id for item in report.approval_plan.items) == ("draft_reply",)
```

- [ ] **Step 2: Run pilot pipeline test**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_draft_connectors.py::test_gmail_draft_pilot_passes_enablement_without_network -q
```

Expected: pass.

### Task 5.6: Commit Phase 5

- [ ] **Step 1: Run phase tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_draft_connectors.py \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_enablement.py \
  appV2.4/tests/test_workflow_catalog.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run all appv24 tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests -q
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

Run:

```bash
git add \
  appV2.4/appv24/workflows/connectors \
  appV2.4/appv24/workflows/examples.py \
  appV2.4/tests/test_workflow_draft_connectors.py \
  appV2.4/tests/test_workflow_examples.py
git commit -m "feat(appv24): add draft-only workflow connector pilot"
```

Expected: one commit with draft-only connector providers and pilot golden example.

---

## Final Verification Matrix

- [ ] **Red-zone check**

Run:

```bash
git diff --name-only next/appv24...HEAD | rg 'appV2\\.4/appv24/(agent/|ai/stream\\.py|ai/validation\\.py|compaction/|coding_agent/session_store\\.py|app\\.py)' && exit 1 || exit 0
```

Expected: exit 0 with no red-zone output.

- [ ] **Workflow suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_broker.py \
  appV2.4/tests/test_workflow_catalog.py \
  appV2.4/tests/test_workflow_challenge_corpus.py \
  appV2.4/tests/test_workflow_draft_connectors.py \
  appV2.4/tests/test_workflow_enablement.py \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_quality_gate.py \
  appV2.4/tests/test_workflow_repair.py \
  appV2.4/tests/test_workflow_simulator.py -q
```

Expected: all selected tests pass.

- [ ] **Full appv24 suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests -q
```

Expected: all tests pass.

- [ ] **No real side-effect check**

Run:

```bash
rg -n "requests\\.|httpx\\.|urllib\\.|gmail\\.users\\.|telegram\\.Bot|send_message\\(|messages\\.send|socket\\." appV2.4/appv24/workflows appV2.4/tests/test_workflow_draft_connectors.py
```

Expected: no direct network/send API calls in workflow provider code. Any string mentions must be test names, comments, or passive metadata.

- [ ] **TUI smoke after all workflow tests**

Run one manual TUI save test only after workflow APIs pass:

```bash
APPV24_WORKER_LLM_ENABLED=true \
APPV24_WORKER_LLM_MODEL=qwen/qwen3-coder-next \
APPV24_WORKER_LLM_TEMPERATURE=0.2 \
APPV24_WORKER_LLM_MAX_TOKENS=7000 \
PYTHONPATH=appV2.4 \
.venv/bin/python appV2.4/scripts/appv24_tui.py \
  --cwd /tmp/appv24-workflow-enable-smoke \
  --dotenv .env \
  --provider openrouter \
  --model qwen/qwen3-coder-next \
  --profile workflow-designer-lite \
  --thinking off \
  --temperature 0.2 \
  --max-tokens 7000 \
  --tui
```

Expected: the TUI can still generate, repair, validate, quality-gate, and save a workflow package. Do not use this as a replacement for unit tests.

## Acceptance Criteria By Phase

- Phase 0 accepted when current quality-gate save enforcement is committed and all appv24 tests pass.
- Phase 1 accepted when every challenge case fails for the intended reason and repair prompts include targeted directives.
- Phase 2 accepted when a package-level enablement report summarizes validation, compile, policy, quality, capability plan, fixtures, and blocked issues.
- Phase 3 accepted when connector actions are checked against a built-in capability catalog and catalog-aware corpus cases exist.
- Phase 4 accepted when approval plan and immutable JSONL event helpers exist and reports expose approval requirements without committing failed dry-run side effects.
- Phase 5 accepted when one draft-only provider passes the full enablement pipeline without network, send, credential, or external side effects.

## Rollback Strategy

- Phase 0 rollback: revert the quality-gate save enforcement commit only.
- Phase 1 rollback: revert the challenge corpus commit; workflow runtime behavior remains unchanged.
- Phase 2 rollback: revert enablement v1 report commit; existing quality gate and broker remain available.
- Phase 3 rollback: revert catalog commit; existing connector validation returns to pre-catalog rules.
- Phase 4 rollback: revert approval ledger commit; simulation ledger remains unchanged unless Task 4.3 modified report fields.
- Phase 5 rollback: revert draft connector pilot commit; no real external state was modified.

## Self-Review

- Spec coverage: covers all five requested phases plus closeout.
- Zone coverage: plan forbids red-zone edits and allows yellow-zone only as thin UI integration after workflow APIs pass.
- External source alignment: Temporal supports broker/activity side-effect isolation; LangGraph supports persisted approval/resume concepts; n8n supports workflow/node/credential/execution vocabulary.
- Scope check: the five phases are separate branches and commits; each phase is independently testable.
- Placeholder scan: no placeholder markers or open-ended implementation steps remain.
- Type consistency: new planned symbols are `WorkflowRepairChallenge`, `build_workflow_enablement_report`, `CapabilityCatalog`, `CatalogCapability`, `ApprovalPlan`, `ApprovalLedgerEvent`, and draft provider functions.
