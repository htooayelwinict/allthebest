# appv24 Workflow Enablement v0

Workflow Enablement v0 proves that a saved appv24 workflow package can be validated, compiled, policy-checked, and routed through a no-side-effect local broker during dry-run simulation.

This is not workflow execution. It does not call external APIs, send messages, provision infrastructure, read secrets, run shell commands, or mutate files.

## Pipeline

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

The quality gate requires static terminal reachability and terminal completion for declared dry-run fixtures. It does not execute real connectors.

## Python API

```python
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.reports import build_enablement_report

example = golden_workflow_examples()["telegram_support_ticket"]
fixture = require_fixture(example.fixtures, "happy_path.yaml")
report = build_enablement_report(
    example.documents,
    "happy_path.yaml",
    fixture,
    broker=example.broker,
)
```

## Safety Boundary

- broker requests are dry-run only
- provider outputs are filtered to declared `outputs.allowed`
- unknown connector actions block simulation
- quality-gate failures block enablement reports before a package is considered ready
- real connector execution is out of scope
- CLI/TUI commands are deferred
