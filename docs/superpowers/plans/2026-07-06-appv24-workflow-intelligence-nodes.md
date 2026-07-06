# appv24 Workflow Intelligence Nodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add generic workflow platform primitives for read-source nodes, typed LLM transform nodes, deterministic policy gates, and notification actions without making appv24 finance-specific.

**Architecture:** Keep this inside appv24 workflow packages and runners. A workflow can call cataloged `source.*`, `llm.*`, `policy.*`, and notification actions; validation and policy enforce metadata, no free-form agent nodes, output schemas, approvals, and no external mutation by default. Financial catalyst alerts become an example/template on top of the platform primitives, not runtime behavior.

**Tech Stack:** Python dataclasses, existing appv24 workflow package schema/semantic validator/compiler/policy/local runner/live-read runner, pytest, YAML workflow fixtures.

---

## Zone Contract

Red zone: no changes.

Do not modify:
- `appV2.4/appv24/agent/`
- `appV2.4/appv24/ai/stream.py`
- `appV2.4/appv24/ai/types.py`
- `appV2.4/appv24/ai/validation.py`
- `appV2.4/appv24/compaction/`
- `appV2.4/appv24/coding_agent/session_store.py`
- `appV2.4/appv24/app.py`

Yellow zone: only if required and with tests.
- `appV2.4/appv24/profiles/builtins.py`
- `appV2.4/appv24/tui/interactive_mode.py`

Green zone:
- `appV2.4/appv24/workflows/`
- `appV2.4/tests/test_workflow_*.py`
- `docs/appv24/`
- `examples/appv24/`

## File Map

Modify:
- `appV2.4/appv24/workflows/catalog.py` - add generic built-in actions only.
- `appV2.4/appv24/workflows/schema.py` - allow `llm` adapter and typed metadata fields.
- `appV2.4/appv24/workflows/semantic.py` - validate LLM/source/policy contracts.
- `appV2.4/appv24/workflows/policy.py` - block unsafe LLM/action shapes.
- `appV2.4/appv24/workflows/local_artifacts.py` - simulate source, LLM, policy, and notification outputs.
- `appV2.4/appv24/workflows/prompt_fragments.py` - teach workflow designer the generic catalog.
- `appV2.4/appv24/profiles/builtins.py` - only if prompt fragment text must be surfaced.

Create:
- `appV2.4/appv24/workflows/contracts.py`
- `examples/appv24/workflows/financial-catalyst-alerts/`
- `docs/appv24/workflow-intelligence-nodes.md`

Tests:
- `appV2.4/tests/test_workflow_packages.py`
- `appV2.4/tests/test_workflow_policy.py` if split exists; otherwise keep policy tests in `test_workflow_ir.py`.
- `appV2.4/tests/test_workflow_runner.py`
- `appV2.4/tests/test_workflow_staging.py`
- `appV2.4/tests/test_profiles.py`

## Platform Contract

Allowed generic catalog additions:

```python
{
    "source.fetch_items",
    "source.fetch_document",
    "llm.extract_structured",
    "llm.classify",
    "llm.summarize",
    "policy.check",
    "notification.create_draft",
}
```

Not allowed:

```python
{
    "llm.do_anything",
    "agent.run",
    "http.request",
    "finance.find_catalysts",
    "trading.recommend",
    "broker.place_order",
}
```

LLM node contract:

```yaml
id: extract_catalysts
type: action
uses: llm.extract_structured
with:
  task: extract_financial_catalysts
  input_ref: fetch_news.items
  output_contract: event_alerts_v1
  safety:
    disallow:
      - personalized_financial_advice
      - buy_sell_hold_recommendation
      - price_target
  model:
    temperature: 0
    max_tokens: 2000
side_effect_class: read
next: catalyst_policy
```

Token rule: prefer compact `output_contract` names over inline `output_schema`. Inline `output_schema` remains valid for uncommon custom shapes, but built-in examples, prompts, and repair text must use short contract names to avoid token bloat.

Policy gate contract:

```yaml
id: catalyst_policy
type: action
uses: policy.check
with:
  input_ref: extract_catalysts.alerts
  checks:
    require_source_urls: true
    require_source_timestamps: true
    disallow_financial_advice: true
    min_confidence: medium
side_effect_class: read
next: create_alert_draft
```

## Tasks

### Task 1: Catalog The Generic Primitives

**Files:**
- Modify: `appV2.4/appv24/workflows/catalog.py`
- Test: `appV2.4/tests/test_workflow_ir.py`

- [x] Add a failing test:

```python
def test_policy_accepts_generic_intelligence_catalog_actions(tmp_path: Path) -> None:
    from appv24.workflows.catalog import is_builtin_workflow_action

    assert is_builtin_workflow_action("source.fetch_items")
    assert is_builtin_workflow_action("llm.extract_structured")
    assert is_builtin_workflow_action("policy.check")
    assert is_builtin_workflow_action("notification.create_draft")
    assert not is_builtin_workflow_action("finance.find_catalysts")
    assert not is_builtin_workflow_action("llm.do_anything")
```

- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py::test_policy_accepts_generic_intelligence_catalog_actions -q
```

Expected: fail because actions are missing.

- [x] Update `BUILTIN_WORKFLOW_ACTIONS` with the generic actions above.

- [x] Rerun the same test.

Expected: pass.

### Task 2: Add Metadata Validation For LLM Nodes

**Files:**
- Create: `appV2.4/appv24/workflows/contracts.py`
- Modify: `appV2.4/appv24/workflows/schema.py`
- Modify: `appV2.4/appv24/workflows/semantic.py`
- Test: `appV2.4/tests/test_workflow_packages.py`

- [x] Add tests that reject:
  - `llm.extract_structured` without either `with.output_contract` or `with.output_schema`
  - `llm.extract_structured` with unknown `with.output_contract`
  - `llm.extract_structured` with mutating side effects
  - `llm.do_anything`
  - `finance.find_catalysts`

- [x] Add tests that accept:
  - `source.fetch_items -> llm.extract_structured -> policy.check -> notification.create_draft`
  - all actions declared in `connectors.yaml`
  - all actions allowed in `permissions.yaml`

- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py -q
```

Expected first run: fail on missing validation.

- [x] Implement semantic checks:
  - every executable `steps[].uses` must exist in built-in catalog
  - `llm.*` actions require compact `with.output_contract` or inline `with.output_schema`
  - known `with.output_contract` values are loaded from `contracts.py`
  - `llm.*` actions must be read-only/transform-only
  - `llm.*` actions must not require approval or idempotency
  - `policy.check` requires `with.checks`

- [x] Rerun the same tests.

Expected: pass.

### Task 3: Local Runner Simulation

**Files:**
- Modify: `appV2.4/appv24/workflows/local_artifacts.py`
- Test: `appV2.4/tests/test_workflow_runner.py`

- [x] Add a package fixture in the test that runs:

```text
source.fetch_items
-> llm.extract_structured
-> policy.check
-> notification.create_draft
-> terminal
```

- [x] Assert happy path:

```python
assert report.status == "passed"
assert report.terminal_step == "done"
assert [(a.step_id, a.connector_action, a.broker_status) for a in report.brokered_actions] == [
    ("fetch_news", "source.fetch_items", "simulated"),
    ("extract_catalysts", "llm.extract_structured", "simulated"),
    ("check_policy", "policy.check", "simulated"),
    ("create_alert_draft", "notification.create_draft", "simulated"),
]
```

- [x] Assert rejection path:

```python
assert report.status == "blocked"
assert "policy rejected" in " ".join(report.issues)
```

- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_runner.py -q
```

Expected first run: fail because local providers are missing.

- [x] Add local providers:
  - `source_fetch_items`
  - `source_fetch_document`
  - `llm_extract_structured`
  - `llm_classify`
  - `llm_summarize`
  - `policy_check`
  - `notification_create_draft`

- [x] Rerun tests.

Expected: pass.

### Task 4: Prompt And Repair Hygiene

**Files:**
- Modify: `appV2.4/appv24/workflows/prompt_fragments.py`
- Modify: `appV2.4/appv24/workflows/repair_directives.py`
- Test: `appV2.4/tests/test_workflow_staging.py`
- Test: `appV2.4/tests/test_workflow_repair.py`

- [x] Add tests asserting workflow-designer prompts say:
  - use generic platform actions
  - do not invent `finance.*`, `trading.*`, `llm.do_anything`, or `http.request`
  - LLM nodes must be typed transforms with compact `output_contract` names when possible
  - financial catalyst examples must be informational alerts only

- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_staging.py appV2.4/tests/test_workflow_repair.py -q
```

Expected first run: fail on prompt text assertions.

- [x] Update prompt fragments and repair directives.

- [x] Rerun focused tests.

Expected: pass.

### Task 5: Example Package

**Files:**
- Create: `examples/appv24/workflows/financial-catalyst-alerts/workflow.yaml`
- Create: `examples/appv24/workflows/financial-catalyst-alerts/connectors.yaml`
- Create: `examples/appv24/workflows/financial-catalyst-alerts/permissions.yaml`
- Create: `examples/appv24/workflows/financial-catalyst-alerts/dry_run_fixtures/happy_path.yaml`
- Create: `examples/appv24/workflows/financial-catalyst-alerts/dry_run_fixtures/rejection_path.yaml`
- Create: `examples/appv24/workflows/financial-catalyst-alerts/rollback.md`
- Test: `appV2.4/tests/test_workflow_packages.py`

- [x] Add a test that validates the example package path.

- [x] Add a test that local-runs both fixtures.

- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_runner.py -q
```

Expected: pass.

### Task 6: Docs And Manual TUI QA

**Files:**
- Create: `docs/appv24/workflow-intelligence-nodes.md`
- Modify: `docs/appv24/workflow-run-live-path.md`

- [x] Document:
  - generic node contract
  - LLM node is not an agent loop
  - compact `output_contract` names avoid repeated schema token bloat
  - financial alerts are examples only
  - live-read can read allowlisted sources only
  - auto-send requires approval and remains gated

- [x] Run full tests:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
```

Expected: all tests pass and diff check is clean.

- [x] Run manual gated TUI QA with cost-bearing model only when approved:

```bash
TERM=xterm-256color PYTHONPATH=appV2.4 .venv/bin/python appV2.4/scripts/appv24_tui.py \
  --dotenv .env \
  --cwd /tmp/appv24-intelligence-node-qa \
  --profile workflow-designer-lite \
  --provider openrouter \
  --model qwen/qwen3-coder-next \
  --thinking off \
  --max-tokens 12000
```

Prompt:

```text
I do not know workflow building. I want a workflow that watches public company news and filings, extracts important catalysts with an LLM, blocks anything that looks like trading advice, and drafts a Telegram or email alert with source links. Make the appv24 package so I can save and test it.
```

Expected TUI result:
- workflow draft validation passes
- `/workflow-save saved-catalyst-alert`
- `/workflow-run-local saved-catalyst-alert happy_path.yaml` passes
- `/workflow-run-local saved-catalyst-alert rejection_path.yaml` blocks or rejects before notification

## Final Verification

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git diff --name-only | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
```

Expected:
- full appv24 test suite passes
- diff check clean
- red-zone command prints nothing

## Commit Plan

Only after verification:

```bash
git add appV2.4/appv24/workflows appV2.4/tests docs/appv24 examples/appv24
git commit -m "feat(appv24): add workflow intelligence nodes"
```

Do not publish npm or GHCR from this plan.
