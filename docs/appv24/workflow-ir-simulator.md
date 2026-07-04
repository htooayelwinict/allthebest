# appv24 Workflow IR And Simulator

appv24 workflow packages can be compiled into a typed internal IR and simulated against dry-run fixtures.

This layer is passive. It does not execute connectors, call APIs, send messages, provision cloud resources, run shell commands, or read secrets.

## Pipeline

```text
workflow package
  -> validate_workflow_package
  -> compile_workflow_package
  -> evaluate_policy
  -> simulate_workflow_fixture
  -> SimulationLedger
```

## Public API

```python
from appv24.workflows import (
    compile_workflow_package,
    evaluate_policy,
    simulate_workflow_fixture,
)
from appv24.workflows.loader import load_package_documents

documents = load_package_documents("path/to/package")
compiled = compile_workflow_package("path/to/package")
if compiled.ok and compiled.ir is not None:
    report = evaluate_policy(compiled.ir)
    fixture = documents.dry_run_fixtures["happy_path.yaml"]
    ledger = simulate_workflow_fixture(compiled.ir, "happy_path.yaml", fixture)
```

## Boundaries

The simulator uses fixture data only. Connector actions are recorded as simulated side effects in the ledger, never executed.

## Ledger

The ledger reports:

- workflow name
- fixture name
- status
- ordered step trace
- terminal step
- simulated side-effect step ids
- blocking issues
