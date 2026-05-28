# Implementation Plan: LLM Heavy Prompt-Chain Decompressor

## Goal

Implement an optional, injectable LLM-powered prompt-chain mode inside `DecompressorRuntime` that produces the existing `Envelope` contract while preserving deterministic fallback behavior and the current LangGraph topology.

## Success criteria

- `DecompressorRuntime().run(...)` remains backward-compatible and deterministic with no model client configured.
- An injected LLM prompt-chain path can run staged model-backed decompression and return a valid `Envelope`.
- The graph stays `decompressor_node -> planner_node -> worker_kernel_node -> END`; prompt-chain stages do not become LangGraph nodes.
- All model outputs are Pydantic-validated, label-clamped, and safe-fallbackable before reaching planner/runtime code.
- Unit tests use fake/canned model responses only; no live model calls occur in tests.
- Prompt input redacts common secret-like strings before external model calls.

## Implementation status

Completed. The runtime now supports explicit model-client injection while preserving deterministic default behavior and graph topology.

## Plan source

This plan implements the recommendation from:

- `plan/research-llm-heavy-promptchain-decompressor-20260529-011000/README.md`

## Artifacts

- Main implementation plan: `plan.md`
- Requirements research: `research/requirements.md`
- Existing code research: `research/existing-code.md`
- Reference notes: `research/references.md`
- Phase execution files: `phases/`

## Recommended first implementation step

Start with Phase 1: add internal decompressor contracts, allowed-label constants, redaction helpers, and fake-test-client scaffolding without changing runtime behavior.
