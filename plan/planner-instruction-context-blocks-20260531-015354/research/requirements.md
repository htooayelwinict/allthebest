# Requirements

## User Request

Plan a prompt-only fix to make planner step instructions self-contained using a small context block.

## Required Scope

- Prompt-level only.
- No runtime, schema, validator, worker-kernel, graph, or topology changes.
- Preserve existing direct-support and worker-plan behavior.

## Desired Instruction Shape

```text
Known facts: ...
Unknowns: ...
Do now: ...
Do not do: ...
Output: ...
```

## Rationale

Workers only receive limited task context. The planner should embed essential context in `step.instruction` so workers can act safely without relying on hidden envelope state.
