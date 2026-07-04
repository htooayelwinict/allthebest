# appv24 Workflow IR And Policy Simulator Design

Date: 2026-07-04
Status: proposed design
Scope: appV2.4 / appv24 workflow package layer only

## Summary

appv24 can now generate, validate, repair, and save workflow packages. The next layer should prove that a valid package can be lowered into a strict internal intent graph and evaluated without executing connectors.

This design adds a passive workflow IR compiler, capability index, policy checker, fixture simulator, and simulation ledger under `appV2.4/appv24/workflows/`.

The goal is:

```text
valid workflow package
  -> typed internal IR
  -> static policy report
  -> dry-run fixture simulation
  -> structured ledger
```

This is not a workflow runner. It must not call Telegram, email, ticketing, cloud, shell, browser, MCP, or other external APIs.

## Boundary

Allowed implementation areas:

```text
appV2.4/appv24/workflows/ir.py
appV2.4/appv24/workflows/compiler.py
appV2.4/appv24/workflows/capabilities.py
appV2.4/appv24/workflows/policy.py
appV2.4/appv24/workflows/simulator.py
appV2.4/appv24/workflows/ledger.py
appV2.4/appv24/workflows/__init__.py
appV2.4/tests/test_workflow_ir.py
appV2.4/tests/test_workflow_simulator.py
docs/appv24/
```

Allowed only if a small shared helper is clearly needed:

```text
appV2.4/appv24/workflows/loader.py
appV2.4/appv24/workflows/validator.py
appV2.4/appv24/workflows/semantic.py
appV2.4/tests/test_workflow_packages.py
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

Avoid yellow-zone files in this phase:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/
appV2.4/appv24/profiles/
```

No CLI or TUI command is added in v1. The public surface is a Python API and tests.

## Approaches Considered

### A. Library/Test-Only IR And Simulator

Add the IR compiler and simulator as pure workflow modules. Tests call the API directly.

Pros:

- Stays fully inside the green-zone workflow package layer.
- No CLI/TUI surface before behavior is stable.
- Easy to test with saved packages and synthetic fixtures.
- Low rollback risk.

Cons:

- Lewis cannot run a friendly command yet.
- Product UX remains hidden until the next phase.

Decision: choose this for v1.

### B. Add `appv24 workflow simulate <package>`

Expose the compiler and simulator through CLI immediately.

Pros:

- Faster manual inspection.
- Closer to user-facing workflow enablement.

Cons:

- Touches yellow-zone CLI.
- Forces output formatting and UX decisions before the core contract is settled.

Decision: defer.

### C. Build A Real Connector Runner

Compile packages and execute brokered actions.

Pros:

- Demonstrates end-to-end automation.

Cons:

- Too early.
- Needs secrets, external APIs, approval UX, durable execution logs, retries, and security review.
- High risk of turning workflow design into unsafe automation.

Decision: explicitly out of scope.

## Architecture

Add six small modules:

```text
ir.py
  frozen dataclasses for typed workflow intent graph

capabilities.py
  connector/action capability extraction from connectors.yaml and permissions.yaml

compiler.py
  PackageDocuments -> WorkflowIR

policy.py
  static checks over WorkflowIR

simulator.py
  deterministic dry-run traversal over WorkflowIR and one fixture

ledger.py
  structured simulation trace, policy report, and final summary
```

The compiler accepts `PackageDocuments`, not raw paths. Callers choose whether to load from disk or parse a draft first.

The compiler should require a package that already passes `validate_workflow_documents()`. If validation fails, caller receives the existing validation issues before compilation. The compiler may still add stricter compile issues for IR-only invariants.

## IR Model

`ir.py` defines only plain Python dataclasses and enums.

Core types:

```python
WorkflowIR
WorkflowStep
WorkflowEdge
ConnectorRef
ActionCapability
ApprovalRequirement
SideEffectContract
WorkflowCompileIssue
WorkflowCompileResult
```

Recommended shape:

```python
@dataclass(frozen=True)
class WorkflowIR:
    name: str
    version: str
    entrypoint: str
    triggers: tuple[WorkflowTrigger, ...]
    steps: Mapping[str, WorkflowStep]
    connectors: Mapping[str, ConnectorCapability]
    approvals: Mapping[str, ApprovalRequirement]
    design: WorkflowDesignContract
```

`WorkflowStep` should normalize:

```text
id
kind: action | decision | manual_approval | terminal
uses: connector.action | None
side_effect_class
approval
next
branches
raw
```

The IR keeps `raw` mappings for traceability, but all simulator and policy logic should use normalized fields.

## Compiler

`compiler.py` is responsible for lowering validated documents into IR.

Responsibilities:

- Build connector/action capability indexes.
- Normalize step ids and reject duplicates defensively.
- Normalize `next` and `branches` into outbound edges.
- Resolve `uses` into connector/action references.
- Attach side-effect metadata from both workflow steps and connector actions.
- Attach approval requirements from `permissions.yaml`.
- Preserve trigger and design metadata.
- Return structured compile issues instead of raising for package-content problems.

Suggested public API:

```python
def compile_workflow_documents(documents: PackageDocuments) -> WorkflowCompileResult: ...
def compile_workflow_package(path: str | Path) -> WorkflowCompileResult: ...
```

`WorkflowCompileResult.ok` is true only when validation and compile checks both pass.

Compile checks should include:

- entrypoint exists
- every transition target exists
- every connector action reference resolves
- every mutating step resolves to a side-effect contract
- every approval reference resolves
- no unsupported step kind after normalization
- step count does not exceed a conservative limit, default `100`

These checks overlap with semantic validation intentionally, but the compiler reports IR-specific failures and protects future callers that bypass validation by mistake.

## Capabilities

`capabilities.py` extracts capability facts from connectors and permissions.

Capability facts:

```text
connector id
adapter type: mcp | openapi | asyncapi | webhook | direct_api | iac | manual | platform_export
action id
side_effect boolean
side_effect_class
execution_mode
approval_required
dry_run_supported
idempotency key fields
allowed outputs
required scopes
granted scopes
permission entry present
design_only permission flag
```

The simulator never executes capabilities. It only uses them to decide what would have happened and which policy rule applies.

## Policy

`policy.py` evaluates static safety over `WorkflowIR`.

Policy result types:

```python
PolicySeverity = Literal["error", "warning", "info"]
PolicyIssue
PolicyReport
```

v1 policy rules:

```text
P001 mutating step requires explicit approval
P002 approval id must exist
P003 approval.required_for must include the exact step id
P004 brokered mutating action requires dry_run_supported
P005 brokered mutating action requires idempotency key fields for create/send/publish/provision
P006 brokered mutating action must define outputs.allowed without wildcard
P007 design_only action must not be used by a workflow step
P008 required scopes must be granted in permissions.yaml
P009 workflow must include rollback notes when mutations exist
P010 workflow must include dry-run fixtures when mutations exist
P011 raw secrets must not appear in package documents
P012 rollback notes must remain manual and non-executable
P013 step count must stay under configured limit
P014 transitions must resolve to known steps
P015 simulation-only: fixture must drive at least one terminal path
```

Rules P001-P012 reuse validator semantics. The policy layer should not invent conflicting rules; it packages existing facts into a run-oriented report.

Policy default:

```text
errors block simulation success
warnings allow simulation but mark ledger as degraded
```

## Simulator

`simulator.py` runs a deterministic dry-run traversal over one fixture.

Inputs:

```python
WorkflowIR
fixture name
fixture mapping
policy report
```

Behavior:

- Start at `entrypoint`.
- For action steps, read fixture `mock_responses` or equivalent fixture data.
- Do not call connectors.
- Store simulated outputs under the step id.
- For decision steps, evaluate simple branch forms used by current packages:
  - `branches: {key: step_id}` with fixture-provided decision value
  - `condition.field` and boolean/equality values when present
  - `next` fallback when no branch matches
- For manual approval steps, use fixture-provided approval decisions when present.
- Stop at terminal steps.
- Detect cycles with a max transition budget, default `200`.
- Produce a ledger even when simulation cannot complete.

The simulator should be intentionally narrow. If a condition cannot be evaluated, it records a simulation issue instead of trying to become a full expression engine.

## Ledger

`ledger.py` defines a durable, JSON-serializable dry-run result.

Core types:

```python
SimulationStatus = passed | failed | blocked | degraded
StepTrace
SimulationLedger
```

Each `StepTrace` includes:

```text
step_id
kind
status
selected_branch
connector_action
side_effect_class
approval_id
simulated_outputs
issues
```

The ledger includes:

```text
workflow name
fixture name
started_at logical timestamp or omitted for deterministic tests
status
policy summary
step traces
terminal step
simulated side effects
blocked reason
```

Do not write ledger files in v1. Return the ledger object and provide `to_dict()` or `asdict()` support for future CLI/TUI.

## Error Handling

Use structured results, not broad exceptions, for package-content failures.

Raise only for programmer errors such as unsupported object types passed into public APIs.

Compile, policy, and simulation issues should include:

```text
code
message
file or source
path or step_id
severity
hint
```

Where possible, reuse `WorkflowIssue` or mirror its shape to avoid another incompatible error vocabulary.

## Testing Strategy

Add focused unit tests with synthetic packages and existing saved-package shapes.

Compiler tests:

- valid minimal package compiles to IR
- all steps indexed by id
- action step resolves connector/action capability
- decision branches normalize into edges
- unknown connector creates compile issue
- unknown transition creates compile issue
- duplicate step ids create compile issue
- compile package API validates before compiling

Policy tests:

- valid brokered mutation has no policy errors
- mutating send without approval is blocked
- design-only action referenced by a step is blocked
- missing required scope is blocked
- wildcard output allowlist is blocked
- missing idempotency for send/create is blocked
- unsafe rollback remains blocked through policy report

Simulator tests:

- authorized Telegram fixture reaches success terminal
- unauthorized Telegram fixture reaches rejection terminal and records no side effects
- ticket creation failure fixture reaches failure terminal
- manual approval fixture controls approve/reject branch
- unknown condition produces blocked ledger
- cycle is detected and blocked
- policy error prevents passed ledger status

Integration-style tests:

- save a valid generated-style package fixture under `tests/fixtures/workflows/telegram-support-ticket-bot`
- validate -> compile -> policy -> simulate three fixtures
- assert no real connector execution is possible

Verification:

```text
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_ir.py appV2.4/tests/test_workflow_simulator.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_workflow_repair.py appV2.4/tests/test_workflow_staging.py
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

## Rollout

Phase 1:

- Add IR dataclasses and compiler.
- Add capability extraction.
- Add static policy report.
- Add tests.

Phase 2:

- Add simulator and ledger.
- Add tests for generated-style packages and dry-run fixtures.

Phase 3:

- Document the API.
- Optionally add CLI/TUI wrapper later, after behavior is stable.

## Non-Goals

Do not:

- execute real connectors
- call external APIs
- read secrets
- create tickets
- send Telegram messages
- run shell commands
- add a scheduler
- add retries against real systems
- add CLI/TUI commands in v1
- modify appv24 kernel, provider streaming, compaction, or session storage

## Success Criteria

This design is successful when:

- a saved valid workflow package compiles into a typed `WorkflowIR`
- policy reports are deterministic and structured
- all provided dry-run fixtures produce a ledger
- mutating actions are represented as simulated side effects only
- red-zone files remain untouched
- yellow-zone files remain untouched
- full appV2.4 tests pass
