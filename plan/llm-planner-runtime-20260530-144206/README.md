# LLM Planner Runtime Plan

## Goal

Replace the current static planner skeleton with an LLM-powered planner runtime that consumes the decompressor envelope and emits a validated, worker-safe `Plan` for `WorkerKernelRuntime`.

## Why

The decompressor envelope now contains enough semantic detail for robust planning, but the current planner collapses that detail into one static planner choice. Complex requests like SDK discovery + async integration + performance debugging require multi-step plans with discovery, research, mutation gates, and verification.

## Recommended Direction

Build one primary LLM plan compiler:

```text
Envelope -> LLM plan draft -> deterministic validation -> optional LLM repair -> Plan -> WorkerKernelRuntime
```

Keep deterministic validation as a safety boundary. The LLM decides the plan shape; validation ensures worker types, artifact dependencies, budgets, permissions, and safety gates are valid.

## Key Artifacts

- `plan.md`: implementation source of truth.
- `research/requirements.md`: user requirements and envelope behavior.
- `research/existing-code.md`: repo-local planner/worker research.
- `research/references.md`: source pointers.
- `phases/`: implementation-ready phase documents.

## Status

Planning only. No runtime code has been changed for this planner work.
