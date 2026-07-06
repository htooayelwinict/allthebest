# appv24 Workflow Bundle Export

Workflow bundle export turns a valid workflow package into deterministic runtime artifacts without
running the workflow.

This is an enablement step between validated package design and live execution.

## Contract

Bundle export:

- validates the package
- compiles workflow IR
- evaluates policy
- inspects connector registry metadata
- exports credential and transport env hints
- writes runtime metadata
- never calls provider APIs
- never calls connector transports
- never reads or writes secret values
- never creates workflow run records

## Files

```text
workflow.ir.json
connectors.manifest.json
policy.json
secrets.required.json
triggers.json
runtime.json
README.md
```

`secrets.required.json` contains env names and scope hints only. It must not contain raw tokens,
API keys, cookies, or credential values.

`runtime.json` always uses:

```json
{
  "runtime_mode": "export-only",
  "execution_status": "not-started",
  "side_effects": "none"
}
```

## API

```python
from appv24.workflows.bundle_export import build_workflow_bundle, write_workflow_bundle

bundle = build_workflow_bundle("examples/appv24/workflows/telegram-production-alert", env={})
written = write_workflow_bundle("examples/appv24/workflows/telegram-production-alert")
```

The default write target is:

```text
<package>/.workflow-bundle/
```

Use this before live execution to inspect exactly what appv24 thinks the package needs.
