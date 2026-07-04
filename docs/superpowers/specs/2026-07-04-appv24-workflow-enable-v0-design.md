# appv24 Workflow Enablement v0 Design

Date: 2026-07-04
Status: proposed design
Scope: appV2.4 / appv24 workflow package layer only

## Summary

appv24 already proves this pipeline:

```text
natural language
  -> workflow-designer-lite draft
  -> validator
  -> repair loop
  -> saveable workflow package
  -> typed IR
  -> static policy report
  -> dry-run fixture simulation ledger
```

The next layer should prove that a compiled workflow can be routed through a deterministic, broker-shaped interface without executing real connectors.

This phase adds **Workflow Enablement v0**:

```text
valid package
  -> compiled IR
  -> policy report
  -> local broker plan
  -> golden fixture simulation
  -> deterministic enablement report
```

It is still not a workflow runner. It must not call Telegram, Gmail, GitHub, MCP servers, cloud APIs, shell commands, browsers, databases, or local secrets.

## Decision

Build a **no-side-effect local broker skeleton plus golden workflow fixtures**.

The broker is a future-facing contract. It maps IR action steps to capability handlers, but v0 handlers are deterministic dry-run providers only. They read fixture data and return simulated outputs into the ledger.

This design combines:

- capability registry and policy contracts
- broker-shaped simulation
- golden package fixtures
- deterministic reports

It deliberately defers:

- real connector execution
- OAuth or API key handling
- background workers
- scheduler
- webhooks
- CLI/TUI commands
- cloud deployment

## Boundary

Allowed implementation areas:

```text
appV2.4/appv24/workflows/broker.py
appV2.4/appv24/workflows/fixtures.py
appV2.4/appv24/workflows/examples.py
appV2.4/appv24/workflows/reports.py
appV2.4/appv24/workflows/simulator.py
appV2.4/appv24/workflows/ledger.py
appV2.4/appv24/workflows/__init__.py
appV2.4/tests/test_workflow_broker.py
appV2.4/tests/test_workflow_examples.py
appV2.4/tests/test_workflow_simulator.py
docs/appv24/
```

Allowed only for small compatibility changes:

```text
appV2.4/appv24/workflows/ir.py
appV2.4/appv24/workflows/capabilities.py
appV2.4/appv24/workflows/policy.py
appV2.4/appv24/workflows/compiler.py
```

Avoid yellow-zone files in this phase:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/
appV2.4/appv24/profiles/
packages/appv24-cli/
Dockerfile.appv23.release
.github/workflows/
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

## Approaches Considered

### A. Policy And Capability Contracts Only

This would harden metadata and static checks without changing simulation.

Pros:

- lowest risk
- fully green-zone
- strong foundation for later execution

Cons:

- does not prove how actions are dispatched
- leaves the future runner interface vague

Decision: include this, but not as the whole phase.

### B. App-Owned `/workflow-simulate`

This would add a CLI/TUI command to run validation, compile, policy, and simulation.

Pros:

- easier manual testing
- more visible product behavior

Cons:

- touches yellow-zone CLI/TUI
- creates UX decisions before the broker contract is stable
- increases regression surface

Decision: defer until v0 broker and golden fixtures are stable.

### C. No-Side-Effect Broker Skeleton

This creates the interface that future execution will use, but only with local deterministic providers.

Pros:

- proves routing from IR action steps to capability handlers
- keeps side effects impossible in v0
- creates a stable seam for later real broker work
- can be tested entirely offline

Cons:

- more design responsibility than pure policy checks
- must avoid looking like real execution

Decision: choose this as the core of v0.

### D. Golden Workflow Fixture Pack

This adds canonical workflow packages and expected simulation reports.

Pros:

- excellent regression coverage
- gives prompt/fixer examples later
- makes appv24 workflow quality visible

Cons:

- fixtures alone are not an architecture

Decision: include this as the test harness for the broker.

## Architecture

Add four small workflow modules:

```text
broker.py
  LocalWorkflowBroker, BrokerProvider, BrokerRequest, BrokerResult

fixtures.py
  fixture loading and deterministic fixture lookup helpers

examples.py
  in-memory golden packages used by tests and docs

reports.py
  enablement report shape combining validation, compile, policy, broker, and ledger facts
```

Extend existing modules lightly:

```text
simulator.py
  route action steps through LocalWorkflowBroker when provided

ledger.py
  include broker provider id and broker result status in StepTrace

__init__.py
  export stable workflow enablement API
```

The v0 pipeline:

```text
load package documents
  -> validate_workflow_documents
  -> compile_workflow_documents
  -> evaluate_policy
  -> create LocalWorkflowBroker from IR capabilities
  -> simulate_workflow_fixture(..., broker=broker)
  -> build WorkflowEnablementReport
```

## Broker Contract

The broker is intentionally narrow.

```python
@dataclass(frozen=True)
class BrokerRequest:
    step_id: str
    connector_action: str
    input_ref: Mapping[str, object]
    fixture: Mapping[str, object]
    allowed_outputs: tuple[str, ...]
    dry_run: bool = True

@dataclass(frozen=True)
class BrokerResult:
    status: str
    provider_id: str
    outputs: Mapping[str, object]
    issues: tuple[str, ...] = ()
```

Rules:

- broker v0 always runs in dry-run mode
- providers cannot receive secrets
- providers cannot read files
- providers cannot call network
- providers cannot call shell
- provider output is filtered to `allowed_outputs`
- mutating action steps still only produce simulated outputs
- unknown connector actions return a blocked result

The broker never imports CLI, TUI, agent loop, provider streaming, compaction, session store, Docker, browser, or MCP execution code.

## Golden Fixtures

Add at least three canonical package scenarios:

```text
telegram_support_ticket
  authorized Telegram customer -> ticket draft -> admin notification

gmail_invoice_triage
  inbound invoice email -> classify -> approval -> accounting notification draft

github_customer_bug
  customer report -> dedupe decision -> GitHub issue draft -> support reply draft
```

Each example includes:

- package documents as Python dictionaries
- happy-path fixture
- rejection fixture
- expected simulated terminal step
- expected simulated side-effect step ids
- expected policy status

No example contains secrets, tokens, real chat ids, real email addresses, or live endpoints.

## Enablement Report

`reports.py` should define a compact report:

```python
@dataclass(frozen=True)
class WorkflowEnablementReport:
    workflow_name: str
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    simulation_status: str
    terminal_step: str | None
    simulated_side_effects: tuple[str, ...]
    issues: tuple[str, ...]
```

This is the user-facing shape for later CLI/TUI rendering, but v0 exposes it only as Python API and docs.

## Error Handling

Use structured blocked results, not exceptions, for package-content problems.

Exceptions are acceptable only for programmer errors, such as passing a non-mapping fixture to an internal helper.

Blocked conditions:

- validation failed
- compile failed
- policy failed
- broker provider missing
- provider returned fields outside allowed outputs
- fixture has no mocked response for required connector action
- simulation exceeds transition budget

## Tests

Add offline tests only.

Required coverage:

- broker filters provider outputs to allowed fields
- broker blocks unknown connector actions
- broker blocks non-dry-run requests
- simulation with broker is deterministic across repeated runs
- golden Telegram support ticket package passes compile, policy, and happy-path simulation
- golden rejection fixture reaches rejection terminal without simulated send/create side effects
- malformed package never reaches broker
- no test uses network, shell, real credentials, MCP, or external APIs

## Rollout

Phase v0:

- pure Python API
- tests
- docs
- no CLI/TUI
- no connector execution

Phase v0.1:

- optional `/workflow-simulate` app-owned command
- render the enablement report in TUI
- still no connector execution

Phase v0.2:

- broker permission manifest
- connector pack metadata
- dry-run connector adapters
- explicit execution threat model

Phase v1:

- real connector broker only after approval UX, secrets handling, durable logs, retries, idempotency, and security review are designed separately.

## Open Bridge Review Note

An Open Bridge architecture pass recommended the no-side-effect broker skeleton as the best bridge before execution, with golden fixtures as the verification harness. The useful retained point is that appv24 should define the future connector plug now, while making side effects impossible in this phase.

## Success Criteria

- No red-zone files touched.
- No yellow-zone files touched.
- Full change remains inside workflow modules, tests, and docs.
- Full appV2.4 pytest suite passes.
- Golden examples compile into IR.
- Broker simulation is deterministic.
- No code path performs real connector execution.
