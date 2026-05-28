# Phase 1 LangGraph Runtime Architecture Plan

Goal: implement a strict Phase 1 runtime architecture with exactly three top-level LangGraph nodes (`decompressor_node`, `planner_node`, `worker_kernel_node`) and four core runtime schemas (`Envelope`, `Plan`, `Task`, `Result`).

This repository currently has no application package or tests beyond project metadata, so the implementation should create the requested `app/` structure rather than refactor existing runtime code.

Primary implementation source of truth: [`plan.md`](./plan.md).

Research notes:

- [`research/requirements.md`](./research/requirements.md)
- [`research/existing-code.md`](./research/existing-code.md)
- [`research/references.md`](./research/references.md)
- [`research/stack-requirements-docs.md`](./research/stack-requirements-docs.md)
- [`research/brainstorm-decompressor-prompt-chaining.md`](./research/brainstorm-decompressor-prompt-chaining.md)
- [`research/suggest-prompt-chain-decompressor.md`](./research/suggest-prompt-chain-decompressor.md)
- [`research/brainstorm-option-1-deterministic-decompressor.md`](./research/brainstorm-option-1-deterministic-decompressor.md)

Phases:

1. [`phase-1-project-dependencies-and-schemas.md`](./phases/phase-1-project-dependencies-and-schemas.md)
2. [`phase-2-decompressor-and-planner.md`](./phases/phase-2-decompressor-and-planner.md)
3. [`phase-3-worker-kernel.md`](./phases/phase-3-worker-kernel.md)
4. [`phase-4-langgraph-and-tests.md`](./phases/phase-4-langgraph-and-tests.md)
