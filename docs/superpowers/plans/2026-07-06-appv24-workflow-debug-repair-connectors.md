# appv24 Workflow Debug, Repair, and Connector Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build workflow explainability first, then repair-cost reporting/scoped repair, then a conservative connector catalog v0.

**Architecture:** Keep all core behavior in green-zone workflow modules. Add only narrow CLI/TUI command wiring after module-level tests pass. Do not touch red-zone/kernel files.

**Tech Stack:** Python dataclasses, existing appv24 workflow validator/compiler/policy/run-store modules, pytest, existing CLI/TUI command patterns.

---

## File Structure

Create:

- `appV2.4/appv24/workflows/explain.py`: package and run explanation data models plus formatters.
- `appV2.4/appv24/workflows/repair_report.py`: repair attempt summary and report formatting.
- `appV2.4/appv24/workflows/repair_scope.py`: issue fingerprints, repeated-pattern detection, scoped repair prompt helpers.
- `appV2.4/appv24/workflows/connector_catalog.py`: typed connector action metadata.
- `appV2.4/appv24/workflows/connector_packs.py`: built-in connector pack definitions.
- `appV2.4/tests/test_workflow_explain.py`
- `appV2.4/tests/test_workflow_repair_report.py`
- `appV2.4/tests/test_workflow_connector_catalog.py`
- `docs/appv24/workflow-debugger.md`
- `docs/appv24/workflow-repair-optimizer.md`
- `docs/appv24/workflow-connector-pack-v0.md`

Modify only if needed:

- `appV2.4/appv24/workflows/catalog.py`: delegate built-in action set to connector catalog after compatibility tests exist.
- `appV2.4/appv24/workflows/repair.py`: consume scoped repair/report helpers without changing repair-loop ownership.
- `appV2.4/appv24/workflows/prompt_fragments.py`: prefer draft-safe connector vocabulary in workflow prompts.
- `appV2.4/appv24/tui/interactive_mode.py`: add `/workflow-explain`, `/workflow-run-explain`, `/workflow-repair-report`.
- `appV2.4/appv24/cli.py`: add matching non-TUI command handling only if current CLI flow supports it cleanly.
- `appV2.4/tests/test_tui.py`
- `appV2.4/tests/test_cli.py`

Do not modify:

- `appV2.4/appv24/agent/agent.py`
- `appV2.4/appv24/agent/agent_loop.py`
- `appV2.4/appv24/ai/stream.py`
- `appV2.4/appv24/app.py`
- red-zone compaction/session/provider kernel files from `rules.md`

---

### Task 1: Workflow Package Explanation Core

**Files:**

- Create: `appV2.4/appv24/workflows/explain.py`
- Test: `appV2.4/tests/test_workflow_explain.py`

- [x] **Step 1: Write failing tests for valid package explanation**

Add tests that use an existing golden workflow package or a temporary saved package, then assert:

```python
def test_package_explanation_reports_validation_compile_policy_and_actions(tmp_path):
    package_path = write_valid_workflow_package(tmp_path)

    explanation = explain_workflow_package(package_path)

    assert explanation.package_path == package_path
    assert explanation.workflow_name
    assert explanation.validation_status == "passed"
    assert explanation.compile_status == "passed"
    assert explanation.policy_status == "passed"
    assert "source.fetch_document" in explanation.connector_actions or explanation.connector_actions
```

- [x] **Step 2: Run test and verify it fails**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_explain.py -q
```

Expected: import or function-not-found failure.

- [x] **Step 3: Implement package explanation models**

In `explain.py`, add dataclasses:

```python
@dataclass(frozen=True)
class WorkflowPackageExplanation:
    package_path: Path
    workflow_name: str
    validation_status: str
    compile_status: str
    policy_status: str
    steps: tuple[str, ...]
    connector_actions: tuple[str, ...]
    approvals: tuple[str, ...]
    issues: tuple[str, ...]
```

Implement `explain_workflow_package(path: Path) -> WorkflowPackageExplanation` using:

- `load_package_documents`
- `validate_workflow_documents`
- `compile_workflow_documents`
- `evaluate_policy`

Stop at the first failed gate and preserve issue messages.

- [x] **Step 4: Implement formatter**

Add:

```python
def format_workflow_package_explanation(explanation: WorkflowPackageExplanation) -> str:
    lines = [
        f"Package: {explanation.workflow_name or '<unknown>'}",
        f"Validation: {explanation.validation_status}",
        f"Compile: {explanation.compile_status}",
        f"Policy: {explanation.policy_status}",
    ]
    if explanation.steps:
        lines.append("Steps:")
        lines.extend(f"- {step}" for step in explanation.steps)
    if explanation.connector_actions:
        lines.append("Connector actions:")
        lines.extend(f"- {action}" for action in explanation.connector_actions)
    if explanation.approvals:
        lines.append("Approvals:")
        lines.extend(f"- {approval}" for approval in explanation.approvals)
    if explanation.issues:
        lines.append("Issues:")
        lines.extend(f"- {issue}" for issue in explanation.issues)
    return "\n".join(lines)
```

The formatter must include package name, validation/compile/policy status, steps, connector actions, approvals, and issues.

- [x] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_explain.py -q
```

Expected: pass.

---

### Task 2: Workflow Run Explanation Core

**Files:**

- Modify: `appV2.4/appv24/workflows/explain.py`
- Modify: `appV2.4/tests/test_workflow_explain.py`

- [x] **Step 1: Write failing tests for run artifact explanation**

Add a test that runs an existing local workflow and then explains the saved run:

```python
def test_run_explanation_reads_trace_report_and_events(tmp_path):
    package_path = write_valid_workflow_package(tmp_path)
    report = run_workflow(WorkflowRunRequest(package_path=package_path, fixture_name="happy_path.yaml", mode=WorkflowRunMode.LOCAL))

    explanation = explain_workflow_run(package_path, report.run_id)

    assert explanation.run_id == report.run_id
    assert explanation.status == "passed"
    assert explanation.terminal_step == report.terminal_step
    assert explanation.trace
    assert explanation.brokered_actions
```

- [x] **Step 2: Implement run explanation model**

Add:

```python
@dataclass(frozen=True)
class WorkflowRunExplanation:
    package_path: Path
    run_id: str
    status: str
    terminal_step: str | None
    trace: tuple[str, ...]
    brokered_actions: tuple[str, ...]
    approvals: tuple[str, ...]
    issues: tuple[str, ...]
```

Implement `explain_workflow_run(package_path: Path, run_id: str | None = None)`.

- [x] **Step 3: Implement latest-run selection**

If `run_id` is `None`:

- choose the only run under `.workflow-runs`
- if none exist, return a controlled explanation issue
- if multiple exist, return a controlled issue listing available run ids

- [x] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_explain.py -q
```

Expected: pass.

---

### Task 3: TUI Explain Commands

**Files:**

- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Modify: `appV2.4/tests/test_tui.py`

- [x] **Step 1: Write failing TUI command tests**

Add tests for:

- `/workflow-explain <path>`
- `/workflow-run-explain <path> <run-id>`

Expected output must include status lines from the formatter and must not execute a new workflow run.

- [x] **Step 2: Add slash command metadata**

Add command entries near existing workflow commands:

```python
{"name": "workflow-explain", "description": "Explain a saved workflow package without running it"}
{"name": "workflow-run-explain", "description": "Explain a saved workflow run artifact"}
```

- [x] **Step 3: Add command handlers**

Add parsing functions beside existing workflow command parsers:

```python
def _is_workflow_explain_command(prompt: str) -> bool:
    return prompt == "/workflow-explain" or prompt.startswith("/workflow-explain ")


def _is_workflow_run_explain_command(prompt: str) -> bool:
    return prompt == "/workflow-run-explain" or prompt.startswith("/workflow-run-explain ")
```

Call `explain_workflow_package` and `explain_workflow_run`, then render `StatusLine`.

- [x] **Step 4: Run TUI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py -q
```

Expected: pass.

---

### Task 4: Repair Report Core

**Files:**

- Create: `appV2.4/appv24/workflows/repair_report.py`
- Test: `appV2.4/tests/test_workflow_repair_report.py`

- [x] **Step 1: Write failing tests for attempt report formatting**

Test:

```python
def test_repair_report_formats_attempts_and_convergence():
    report = WorkflowRepairReport(
        attempts=(
            WorkflowRepairAttemptSummary(attempt=1, status="failed", issue_fingerprints=("unknown_catalog_action:workflow.yaml",)),
            WorkflowRepairAttemptSummary(attempt=2, status="passed", issue_fingerprints=()),
        )
    )

    text = format_workflow_repair_report(report)

    assert "Attempt 1: failed" in text
    assert "unknown_catalog_action" in text
    assert "Attempt 2: passed" in text
```

- [x] **Step 2: Implement report dataclasses and formatter**

Add:

```python
@dataclass(frozen=True)
class WorkflowRepairAttemptSummary:
    attempt: int
    status: str
    issue_fingerprints: tuple[str, ...]
    repeated: bool = False

@dataclass(frozen=True)
class WorkflowRepairReport:
    attempts: tuple[WorkflowRepairAttemptSummary, ...]
```

- [x] **Step 3: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair_report.py -q
```

Expected: pass.

---

### Task 5: Repair Scope and Repeated Pattern Detection

**Files:**

- Create: `appV2.4/appv24/workflows/repair_scope.py`
- Modify: `appV2.4/tests/test_workflow_repair_report.py`

- [x] **Step 1: Write failing fingerprint tests**

Test that issue fingerprints use code, file, and location, not full prose:

```python
def test_issue_fingerprint_ignores_message_wording():
    issue_a = SimpleNamespace(code="unknown_catalog_action", file="workflow.yaml", location="steps[1].uses", message="A")
    issue_b = SimpleNamespace(code="unknown_catalog_action", file="workflow.yaml", location="steps[1].uses", message="B")

    assert workflow_issue_fingerprint(issue_a) == workflow_issue_fingerprint(issue_b)
```

- [x] **Step 2: Implement fingerprint helpers**

Add:

```python
def workflow_issue_fingerprint(issue: object) -> str:
    code = str(getattr(issue, "code", "validation_issue"))
    file_name = str(getattr(issue, "file", "<unknown>"))
    location = str(getattr(issue, "location", "<unknown>"))
    return f"{code}:{file_name}:{location}"

def workflow_validation_fingerprint(result: WorkflowDraftValidationResult) -> tuple[str, ...]:
    if result.validation.ok:
        return ()
    return tuple(workflow_issue_fingerprint(issue) for issue in result.validation.issues)
```

- [x] **Step 3: Implement repeated pattern helper**

Add:

```python
def repeated_validation_count(history: Sequence[tuple[str, ...]], current: tuple[str, ...]) -> int:
    return sum(1 for prior in history if prior == current)
```

- [x] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair_report.py -q
```

Expected: pass.

---

### Task 6: Integrate Repair Reports Without Changing Repair Semantics

**Files:**

- Modify: `appV2.4/appv24/workflows/repair.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Modify: `appV2.4/tests/test_workflow_repair.py`
- Modify: `appV2.4/tests/test_tui.py`

- [x] **Step 1: Add tests that repair result carries report data**

Extend tests so a failed-then-passed repair result exposes attempt summaries.

- [x] **Step 2: Extend `WorkflowDraftRepairResult` safely**

Add optional field:

```python
repair_report: WorkflowRepairReport | None = None
```

Keep existing fields compatible.

- [x] **Step 3: Record attempt summaries inside repair loop**

At each validation failure:

- compute issue fingerprint
- mark repeated if same fingerprint appeared before
- append attempt summary

Do not change max-attempt behavior in this task.

- [x] **Step 4: Add TUI `/workflow-repair-report`**

Render the latest repair report if present. If no report exists, show:

```text
Workflow repair report unavailable: no repair has run in this session
```

- [x] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_tui.py -q
```

Expected: pass.

---

### Task 7: Scoped Repair Prompt Optimizer

**Files:**

- Modify: `appV2.4/appv24/workflows/repair_scope.py`
- Modify: `appV2.4/appv24/workflows/repair.py`
- Modify: `appV2.4/tests/test_workflow_repair.py`

- [x] **Step 1: Write tests for scoped prompt payload**

Assert scoped payload includes:

- validation issues
- failed edit scope
- repair directives
- required labeled fenced sections
- schema reminders

Assert it excludes unrelated large examples when no issue requires them.

- [x] **Step 2: Extract prompt-building helpers**

Move issue fingerprint and failed-scope helpers into `repair_scope.py`, then call them from `repair.py`.

- [x] **Step 3: Add fallback gate**

If no scope can be derived, existing full repair prompt behavior remains unchanged.

- [x] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_repair_report.py -q
```

Expected: pass.

---

### Task 8: Connector Catalog V0

**Files:**

- Create: `appV2.4/appv24/workflows/connector_catalog.py`
- Create: `appV2.4/appv24/workflows/connector_packs.py`
- Test: `appV2.4/tests/test_workflow_connector_catalog.py`

- [x] **Step 1: Write failing catalog tests**

Test expected action metadata:

```python
def test_connector_pack_v0_classifies_draft_and_send_actions():
    catalog = builtin_connector_catalog()

    assert catalog.action("source.fetch_document").live_read_eligible is True
    assert catalog.action("telegram.create_message_draft").side_effect_class == "create"
    assert catalog.action("telegram.send_message").live_read_eligible is False
    assert catalog.action("gmail.create_draft").side_effect_class == "create"
```

- [x] **Step 2: Implement action metadata**

Add:

```python
@dataclass(frozen=True)
class ConnectorActionMetadata:
    action_ref: str
    side_effect: bool
    side_effect_class: str | None
    required_scopes: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    live_read_eligible: bool = False
    live_draft_eligible: bool = False
    live_production_enabled: bool = False
    design_only: bool = False
```

- [x] **Step 3: Implement built-in pack**

Include:

- `source.fetch_items`
- `source.fetch_document`
- `gmail.create_draft`
- `telegram.create_message_draft`
- `ticket.create_draft`
- `notification.create_draft`
- `llm.extract_structured`
- `llm.classify`
- `llm.summarize`
- `policy.check`

Keep existing compatibility actions if current tests require them:

- `telegram.send_message`
- `github.create_issue`
- `github.list_dependabot_alerts`

- [x] **Step 4: Run catalog tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_catalog.py -q
```

Expected: pass.

---

### Task 9: Wire Connector Catalog Into Existing Checks

**Files:**

- Modify: `appV2.4/appv24/workflows/catalog.py`
- Modify: `appV2.4/appv24/workflows/prompt_fragments.py`
- Modify: `appV2.4/tests/test_workflow_packages.py`
- Modify: `appV2.4/tests/test_workflow_repair.py`

- [x] **Step 1: Add compatibility tests**

Assert:

```python
assert is_builtin_workflow_action("telegram.create_message_draft")
assert is_builtin_workflow_action("telegram.send_message")
assert not is_builtin_workflow_action("telegram.receive_webhook")
```

- [x] **Step 2: Delegate `BUILTIN_WORKFLOW_ACTIONS`**

Change `catalog.py` to derive the frozen action set from `builtin_connector_catalog()`.

- [x] **Step 3: Update prompt fragments**

Prompt catalog should prefer:

```text
telegram.create_message_draft
gmail.create_draft
notification.create_draft
```

It must warn:

```text
Do not invent receive_webhook, send_live, close_ticket, post_chat, or cloud mutation actions.
```

- [x] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_catalog.py appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_repair.py -q
```

Expected: pass.

---

### Task 10: Documentation

**Files:**

- Create: `docs/appv24/workflow-debugger.md`
- Create: `docs/appv24/workflow-repair-optimizer.md`
- Create: `docs/appv24/workflow-connector-pack-v0.md`
- Modify: `docs/appv24/later-chores.md`

- [x] **Step 1: Document debugger commands**

Include examples for:

```text
/workflow-explain support-ticket-triage
/workflow-run-explain support-ticket-triage run-09b476f5287d4bcb9f9f10f2eec326cf
```

- [x] **Step 2: Document repair optimizer**

Explain:

- issue fingerprints
- repeated failure detection
- scoped prompts
- full prompt fallback
- why validator still consumes full drafts

- [x] **Step 3: Document connector pack v0**

Explicitly state:

- Telegram `sendMessage` is mutating
- Gmail `drafts.create` creates drafts
- Gmail `drafts.send` sends externally and remains future-only
- live production remains disabled

- [x] **Step 4: Run docs grep sanity**

Run:

```bash
rg "live production remains disabled|telegram.create_message_draft|workflow-run-explain" docs/appv24
```

Expected: all key phrases present.

---

### Task 11: Full Verification and Zone Check

**Files:**

- No new files unless tests reveal a necessary narrow fix.

- [x] **Step 1: Run focused workflow test group**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_explain.py \
  appV2.4/tests/test_workflow_repair_report.py \
  appV2.4/tests/test_workflow_connector_catalog.py \
  appV2.4/tests/test_workflow_repair.py \
  appV2.4/tests/test_workflow_packages.py \
  appV2.4/tests/test_tui.py \
  -q
```

Expected: pass.

- [x] **Step 2: Run full appV2.4 suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected: pass.

- [x] **Step 3: Check red-zone files were not modified**

Run:

```bash
git diff --name-only | rg 'appV2\\.4/appv24/(agent/agent\\.py|agent/agent_loop\\.py|agent/types\\.py|ai/types\\.py|ai/stream\\.py|ai/validation\\.py|compaction/compressor\\.py|compaction/timing\\.py|coding_agent/session_store\\.py|app\\.py)' || true
```

Expected: no output.

- [x] **Step 4: Check intended appv24-only scope**

Run:

```bash
git diff --name-only | rg -v '^(appV2\\.4/|docs/appv24/|docs/superpowers/)' || true
```

Expected: no output unless there is a separately approved repo metadata change.

- [x] **Step 5: Commit only after verification**

If Lewis asks for gitops, stage only intended appv24/docs/test files and commit:

```bash
git add appV2.4 docs/appv24 docs/superpowers
git commit -m "feat(appv24): add workflow explainability and connector planning"
```
