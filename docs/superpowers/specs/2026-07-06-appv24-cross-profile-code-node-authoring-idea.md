# Idea: Cross-Profile Code-Node Authoring

Date: 2026-07-06
Status: parked idea only
Scope: appV2.4 / appv24

## Boundary

This note is intentionally separate from the current workflow execution design.

Do not mix this into the active appv24 workflow runner, connector, live execution,
approval, or transport work unless Lewis explicitly promotes this idea into a
design or implementation plan.

This idea must not touch red-zone kernel files.

## Summary

appv24 already has a coding profile and a workflow-designer profile. A future
platform feature could let the workflow profile call the coding profile at
build time when a workflow needs a custom deterministic node that is not already
available in the workflow catalog.

The key constraint is that appv24 should remain deterministic-first. Most
workflow nodes should come from the built-in catalog or connector registry. The
coding profile should be an escape hatch for missing business logic, not the
main workflow engine.

## Product Principle

Use this hierarchy:

```text
1. Use deterministic built-in nodes when available.
2. Use configured connector actions when available.
3. Use LLM nodes only for semantic language tasks.
4. Use custom code nodes only for deterministic logic missing from the catalog.
5. Require validation, tests, sandboxing, and approval before generated code runs.
```

Good platform message:

```text
Deterministic automation first.
Agentic generation only where it creates real leverage.
Validated, testable, approval-gated throughout.
```

## Why This Fits

This fits the appv24 direction because it keeps new use-case logic beside the
kernel:

```text
workflow-designer-lite = business/process planner
coding profile         = implementation generator for missing deterministic nodes
workflow validator     = schema and policy gate
runner/broker          = execution gate
```

The workflow runtime should not call the coding profile during normal production
execution. Generated code should be authored at build time, saved as a package
artifact, validated, tested, fingerprinted, and then executed as a pinned node.

## Example: Reducer Case

User request:

```text
From the extractor output, send only 3 out of 5 data fields into the LLM node.
```

This should not call the coding profile. That is a core workflow operation and
should be handled by a deterministic data-shaping node.

Example:

```yaml
- id: reduce_for_llm
  action: data.project
  input_ref: steps.extract_customer.output
  with:
    include:
      - customer_name
      - recent_orders
      - complaint_summary
  output_schema:
    type: object
    required:
      - customer_name
      - recent_orders
      - complaint_summary

- id: classify_customer_issue
  action: llm.extract_structured
  input_ref: steps.reduce_for_llm.output
```

This supports schema slimming and token control without generated code.

## Data-Shaping Nodes The Catalog Should Prefer

Before invoking code-node authoring, the workflow catalog should cover common
deterministic shaping:

```text
data.project   pick selected fields
data.omit      remove selected fields
data.rename    rename keys
data.flatten   flatten nested objects
data.filter    filter arrays
data.map       map array items with declarative selectors
data.join      combine fields or records
data.template  create a prompt/input object from selected fields
```

These should be validator-known and runner-supported deterministic nodes.

## When Coding Profile Should Be Used

The coding profile becomes appropriate when the requested node requires custom
algorithmic business logic that cannot be represented by catalog nodes.

Examples:

```text
pick the three most relevant invoices based on aging, customer tier, and dispute history
normalize vendor-specific payloads into an internal schema
calculate fraud risk features from nested transaction records
deduplicate records using custom matching rules
compare policy matrices with exception handling and precedence rules
```

In those cases, the workflow profile should emit a code-node requirement rather
than trying to hide the complexity in prompt text.

## Build-Time Flow

Recommended future flow:

```text
workflow-designer-lite
  -> designs workflow package
  -> detects missing deterministic node
  -> emits code-node requirement spec

coding profile
  -> receives only the node spec, fixtures, schemas, constraints, and target language
  -> generates code artifact, tests, and manifest

workflow validator
  -> validates workflow package
  -> validates code-node package
  -> checks permissions, tests, schemas, fingerprints, and side-effect policy

runner
  -> executes pinned validated code artifact in local, sandbox, or approved live mode
```

The coding profile should not inherit the full workflow chat. It should receive a
small contract:

```text
node goal
input schema
output schema
fixtures
constraints
allowed language
forbidden side effects
test expectations
```

## Code-Node Package Shape

A generated code node should be a mini package, not loose code embedded in YAML.

```text
code_nodes/
  normalize_invoice/
    node.yaml
    src/main.py
    tests/test_main.py
    fixtures/happy_path.json
    fixtures/error_path.json
    README.md
```

The manifest should declare:

```text
name
language: python | js | ts
entrypoint
input_schema
output_schema
allowed_imports
network: false by default
filesystem: scratch-only by default
secrets_required: []
timeout_ms
idempotency_behavior
test_command
```

Workflow reference:

```yaml
- id: normalize_invoice
  action: code.run_sandbox
  with:
    node: code_nodes/normalize_invoice
    input_ref: steps.fetch_invoice.output
```

## Safety Model

Generated code nodes should be treated as untrusted until proven otherwise.

Required gates:

```text
schema validation
static manifest validation
fixture tests
network disabled by default
filesystem limited to scratch/output directories
secret access denied by default
timeout required
idempotency behavior declared
fingerprints recorded
human approval before live use
```

Fingerprint inputs:

```text
node spec hash
input schema hash
output schema hash
fixture hash
generated code hash
test result hash
```

## Zone Compatibility

This idea should stay in green-zone workflow/profile code if implemented later.

Likely green-zone areas:

```text
appV2.4/appv24/workflows/code_nodes.py
appV2.4/appv24/workflows/code_node_schema.py
appV2.4/appv24/workflows/code_node_validator.py
appV2.4/appv24/workflows/code_node_fingerprints.py
tests/workflows/
docs/superpowers/specs/
```

Possible yellow-zone areas only if user-facing commands are added:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/
appV2.4/appv24/profiles/
```

Do not touch:

```text
appV2.4/appv24/agent/
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

## Decision

Park this as a future appv24 platform idea:

```text
deterministic workflow catalog first
code-node authoring as a build-time escape hatch
no runtime agent improvisation by default
no current-design changes until explicitly promoted
```
