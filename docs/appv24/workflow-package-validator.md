# appv24 Workflow Package Validator

The appv24 workflow validator checks workflow-as-code packages before appv24 has any runner or cloud executor.

The validator is offline only. It does not run workflows, call APIs, send messages, provision cloud resources, or read secrets.

## Package Layout

```text
workflow.yaml
connectors.yaml
permissions.yaml
dry_run_fixtures/
  happy_path.yaml
  rejection_path.yaml
rollback.md
```

## Validation Layers

1. Required package files are present.
2. YAML files load through `yaml.safe_load`.
3. `workflow.yaml`, `connectors.yaml`, and `permissions.yaml` match appv24 JSON Schemas.
4. Workflow step IDs are unique.
5. Entrypoint and transitions point to existing steps.
6. `connector.action` references point to declared connectors and actions.
7. Permissions allow every connector action used by workflow steps.
8. Mutating steps require `side_effect_class` and an explicit approval gate.
9. Inline secret-looking values are rejected. Use `secretRef` instead.
10. Mutating workflows require `dry_run_fixtures/happy_path.yaml`, `dry_run_fixtures/rejection_path.yaml`, and `rollback.md`.

## Python API

```python
from appv24.workflows import validate_workflow_package

result = validate_workflow_package("examples/appv24/workflows/dependency-security-review")
if not result.ok:
    for message in result.messages():
        print(message)
```

## Adapter Targets

Connector adapters may reference MCP, OpenAPI, AsyncAPI, webhook, direct API, IaC, manual, or platform export targets. These are design-time targets in v1. They are not executed by the validator.
