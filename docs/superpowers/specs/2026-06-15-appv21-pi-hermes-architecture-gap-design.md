# AppV2.1 PI + Hermes Architecture Gap Design

Date: 2026-06-15
Status: Draft for implementation planning
Scope: `appV2.1/appv21/`, `tests/appv21/`, and supporting live probes

## Purpose

AppV2.1 should become the runtime-first agent architecture that keeps the best parts of PI and Hermes while preserving AppV2's stricter safety model.

The target is not to copy either project directly. The target is:

- PI-style observe, act, revise loops.
- PI-style provider/model flexibility.
- PI-style session continuity and context-driven tool use.
- Hermes-style single runtime facade behind every surface.
- Hermes-style tool mediation, hooks, and adapter boundaries.
- Hermes-style context discipline and background maintenance patterns.
- AppV2-style typed decisions, evidence, mutation leases, artifact validation, verification receipts, and auditability.

This document records the current AppV2.1 state, the gaps, and the implementation work needed to reach that target.

## Current AppV2.1 State

AppV2.1 currently has a good runtime-first foundation.

Implemented core pieces:

- `AppV21AgentRuntime` is the main runtime facade.
- Runtime state is event-sourced through `RuntimeEvent`, `reduce_events`, `InMemoryEventStore`, and `JsonlSessionStore`.
- Providers return typed `RuntimeDecision` objects.
- The runtime routes decisions for observe, read/tool call, plan, mutation intent, verify, compact, pause, and finalize.
- `ToolBroker` owns `repo_snapshot`, `read_file`, mutation intent validation, mutation lease derivation, and lease execution.
- Mutation writes require runtime-issued `MutationLease` objects.
- Forged or tampered leases are denied.
- High-risk mutations pause before lease issuance or write execution.
- Pause/resume can rehydrate from JSONL session records.
- `DualContextManager` separates conversation summary from world refs at a basic level.
- `PromptBuilder` exposes a runtime contract and available tool specs.
- `PlannerExtension` is advisory and plans from observed repo state.
- `VerifierExtension` records runtime verification checks.
- `ArtifactValidator` validates decision evidence and artifact evidence.
- Tests cover runtime-first observation, tool calls, bad mutations, high-risk pause/resume, durable resume, extension failure isolation, prompt contract, and missing evidence rejection.

Current limits:

- The provider loop exists, but the deterministic provider is still the main proven path.
- The planner is workspace-cleanup-specific instead of a general planning extension.
- ToolBroker exposes only two read tools and internal mutation helpers.
- Context management is a thin summary layer, not a full evidence store with budgets, relevance, raw payload retention, and compaction strategies.
- The runtime loop is implemented imperatively, not as a formal state machine with transition policy.
- Session lineage is append-only, but does not yet support branching, replay tooling, run inspection, or deterministic snapshots.
- Extension hooks exist, but the lifecycle is small and not yet a plugin ecosystem.
- Artifacts are mostly final summary and workspace manifest oriented.
- Live model behavior is partially adapted through `AppV2EnvAgentProvider`, but not yet hardened through broad evals.

## What AppV2.1 Already Gets Right

AppV2.1 already made the most important architectural correction: runtime observation now happens before planning. Planner output is advisory and rejectable.

The strongest existing design patterns are:

- **Facade:** `AppV21AgentRuntime` is the public runtime entrypoint.
- **Event sourcing:** state is rebuilt from runtime events.
- **Strategy:** providers implement the `AgentProvider` protocol.
- **Mediator:** `ToolBroker` sits between model decisions and file/tool effects.
- **Proposal / judge split:** the model proposes `RuntimeDecision`; runtime validates and executes.
- **Lease-based mutation:** writes require a concrete runtime-issued lease.
- **Evidence-bound finalization:** finalization requires verification or explicit no-op evidence.
- **Fault-isolated extensions:** extension failures emit events instead of breaking the main run.
- **Durable pause/resume:** pauses can survive process restart through JSONL rehydration.

These should remain the architectural spine.

## PI Patterns To Adopt

PI's strongest relevant patterns are agent-loop and provider flexibility patterns.

Adopt:

- **Open observe-act-revise loop:** the model should choose the next step from current world evidence, not obey a fixed pipeline.
- **Tool result continuation:** tool results should feed the next decision turn as first-class evidence.
- **Provider registry:** models should be selected by role, capability, cost, context size, JSON support, tool support, and thinking support.
- **Session continuity:** runs should be resumable and inspectable across turns, not just one-shot probes.
- **Context replacement/compaction:** long sessions need controlled replacement of prompt context while preserving raw world evidence.
- **Runtime services composition:** the runtime should be assembled from explicit services, not hidden globals.
- **Model-specific compatibility shims:** providers may need normalization for tool payload shape, JSON schema behavior, and redundant action coercion.

Do not adopt:

- Unrestricted shell/write behavior as a default tool affordance.
- Conversational assistant text as proof that files changed or verification passed.
- Provider-specific behavior leaking into runtime state semantics.

## Hermes Patterns To Adopt

Hermes' strongest relevant patterns are surface separation, tool mediation, and context discipline.

Adopt:

- **One runtime behind all surfaces:** CLI, API, probes, workers, and future UI should call the same `AppV21AgentRuntime`.
- **Thin adapters:** surfaces translate transport details into runtime requests; they must not own planning, mutation, verification, or context semantics.
- **Tool manager as policy hub:** tool discovery, schemas, execution, hooks, result shaping, redaction, and cache invalidation belong behind ToolBroker-like mediation.
- **Hook lifecycle:** before and after decision, tool, mutation, verification, compaction, pause, resume, and finalize hooks should be explicit.
- **Session-aware orchestration:** active, paused, interrupted, queued, and failed states need clear lifecycle rules.
- **Context compressor topology:** combine cheap deterministic cleanup with optional model summarization for long sessions.
- **Background maintenance:** periodic review/curation can improve skills, memory, and artifacts, but must run outside the critical response path and with restricted tools.

Do not adopt:

- A single large gateway coordinator that owns unrelated concerns.
- Hidden mutable caches without explicit invalidation events.
- Background learning that can silently change safety policy or trusted evidence.

## AppV2 Safety Patterns To Preserve

The target architecture must preserve the AppV2 safety model:

- Runtime-owned evidence beats model claims.
- Model output is a proposal, not authority.
- All trusted observations are runtime-created.
- Mutations are concrete operations, validated against root-path policy.
- High-risk operations pause before write execution.
- Verification must produce runtime receipts.
- Artifacts have trust and lifecycle states.
- Final output references evidence.
- Invalid decisions are rejected and counted.
- Repeated invalid behavior fails deterministically.

This is the main way AppV2.1 improves on PI and Hermes for automated coding tasks.

## Missing Subsystems

### 1. Full ToolBroker Registry

Current ToolBroker has hard-coded `repo_snapshot` and `read_file` specs.

Needed:

- Tool registry with typed tool definitions.
- Tool categories: observe, inspect, search, analyze, plan-helper, mutate, verify, external.
- Tool policy per state, role, risk level, and user constraints.
- Tool argument schema validation.
- Tool result validation.
- Before/after tool hooks.
- Denial envelopes that still become world evidence.
- Tool cache and invalidation events.
- Secret redaction before prompt summaries.
- Raw result storage separate from prompt summaries.

The runtime should never execute a tool outside ToolBroker.

### 2. Context Management System

Current `DualContextManager` exposes conversation summary and world refs, but does not yet own enough lifecycle.

Needed:

- Separate stores for conversation, world evidence, artifacts, memory, and transient scratch.
- Raw tool result retention by world ref.
- Prompt summaries with deterministic size limits.
- Context budgets by provider/model.
- Relevance selection for files, tool results, plans, receipts, and artifacts.
- Deterministic compaction pass for stale/noisy context.
- Optional model compaction pass for conversation summaries.
- Overflow recovery path when provider context fails.
- "Never compact away" classes: mutation leases, mutation receipts, verification receipts, pause state, user constraints, and final artifact evidence.
- Context diff events so the runtime can explain what changed.

Target shape:

```text
ConversationStore
WorldEvidenceStore
ArtifactStore
MemoryStore
ContextSelector
ContextCompactor
PromptBuilder
```

### 3. Provider and Model Routing

Current providers are deterministic, null, and AppV2-env JSON provider.

Needed:

- `ModelRegistry` with provider capabilities.
- Role-based model selection: agent, planner, verifier, compactor, reviewer.
- Capability flags: JSON schema, tool calls, streaming, long context, reasoning/thinking, vision, cost tier.
- Fallback routing when a model fails JSON, refuses schema, loops, or exceeds context.
- Provider normalization for common tool payload variants.
- Cost accounting in `CostState`.
- Live-provider eval matrix before enabling a provider by default.

Provider-specific quirks should stay inside provider adapters, not runtime route logic.

### 4. Formal Runtime State Machine

Current runtime modes exist, but route logic is mostly imperative.

Needed:

- Explicit transition table for START, OBSERVE, THINK, PLAN, ACT, VERIFY, REVISE, COMPACT, PAUSE, FINALIZE, FAILED.
- Guard conditions per transition.
- Rejection taxonomy: missing evidence, unsupported decision, unsafe tool, invalid mutation, stale plan, verification failed, repeated loop.
- Loop progress detector to prevent non-productive repeated observe/plan/tool decisions.
- Interrupt and cancellation states.
- Run timeout and max cost guards.

This will make runtime behavior easier to test and reason about.

### 5. General Planning Extension

Current planner is a workspace-cleanup heuristic.

Needed:

- Planning extension interface with request, world refs, constraints, active skills, and previous failures.
- Plan objects that are advisory and evidence-bound.
- Plan lifecycle: proposed, accepted, revised, superseded, rejected.
- Plan can request more observation instead of inventing scope.
- Planner cannot issue leases or mark verification passed.
- Planner can emit mutation intent, verification intent, and unknowns.

The first general planner can be schema-first and model-backed, with deterministic fixtures for tests.

### 6. Artifact Lifecycle

Current artifact validation is minimal.

Needed lifecycle states:

- proposed
- runtime_observed
- runtime_generated
- runtime_verified
- rejected
- superseded

Needed artifact types:

- plan
- file_change_manifest
- verification_report
- final_report
- context_summary
- run_matrix
- user_approval

Each runtime-verified artifact must reference evidence refs that exist in state.

### 7. Session Lineage and Replay

Current JSONL session storage is enough for pause resume, but not enough for operational debugging.

Needed:

- Run index and session index.
- Replay command/API for a session/run.
- Branching after resume or user correction.
- Parent event links.
- Snapshot checkpoints for faster rehydration.
- Session inspection summaries.
- Corruption handling for partial JSONL writes.
- Cross-run comparison for eval probes.

This follows PI's durable session strengths while keeping AppV2.1 event authority.

### 8. Extension and Plugin Strategy

Current `ExtensionRunner` is advisory but small.

Needed:

- Stable extension protocol.
- Capability declaration.
- Hook ordering and timeout policy.
- Hook result schema.
- Extension failure isolation and dead-letter events.
- Tool-contributing extensions, routed through ToolBroker.
- Context-contributing extensions, routed through ContextManager.
- Verifier-contributing extensions, routed through verification policy.

Extensions can advise and contribute tools, but must not bypass ToolBroker, state reducer, leases, or artifact validators.

### 9. Verification and Eval Matrix

Needed:

- Verification policies by task type.
- Freshness checks: verify after mutation, not against stale observations.
- File hash receipts before/after mutation.
- Tool-based verification as brokered tools.
- Model-assisted review as advisory only.
- Deterministic tests for each state transition.
- Live model matrix for provider behavior.
- Long-context tests for compaction.
- Pause/resume tests across process restart.
- Bad tool payload tests.
- Bad mutation and tampered lease tests.
- Repeated rejection and loop-progress tests.

## Target Architecture

The target runtime shape:

```text
Surface Adapter
  -> AppV21AgentRuntime
  -> RuntimeStateMachine
  -> ContextManager builds prompt payload
  -> AgentProvider returns RuntimeDecision
  -> DecisionValidator validates evidence and transition
  -> ToolBroker / Planner / Verifier / ContextCompactor execute through services
  -> RuntimeEvents appended
  -> Reducer rebuilds AgentState
  -> Loop continues, pauses, fails, or finalizes
```

Core ownership:

- `AppV21AgentRuntime`: public facade and orchestration boundary.
- `RuntimeStateMachine`: transition legality and loop progress.
- `DecisionValidator`: decision kind, evidence refs, payload schema, finalization rules.
- `ToolBroker`: all tool schema, execution, permission, hooks, leases, and result envelopes.
- `ContextManager`: context selection, budgets, summaries, compaction, overflow recovery.
- `ProviderRegistry`: model routing and provider capability matching.
- `ExtensionRunner`: advisory hooks and extension isolation.
- `ArtifactValidator`: artifact lifecycle and evidence validation.
- `SessionStore`: durable events, replay, pause/resume, branching.

## Context Management Design

Context must be split into planes:

- **Conversation plane:** user messages, assistant summaries, decisions, repair summaries, and unresolved questions.
- **World plane:** repo snapshots, file reads, search results, tool results, file hashes, receipts, and verification evidence.
- **Artifact plane:** plans, manifests, reports, and summaries with lifecycle and evidence refs.
- **Memory plane:** stable user/project preferences and learned process notes.
- **Scratch plane:** temporary reasoning hints that can be dropped.

Rules:

- World evidence is never trusted unless runtime-created.
- Raw world payloads are stored outside prompt context and referenced by ID.
- Prompt context gets compact summaries and selected raw snippets only when relevant.
- Verification and mutation receipts are permanent for a run.
- Compaction emits events and can be audited.
- Model-generated summaries cannot replace runtime evidence.

Initial implementation should be deterministic first:

1. Add token/character budgets to prompt sections.
2. Store raw tool payloads by world ref.
3. Add relevance selection for world refs.
4. Add immutable evidence classes.
5. Add deterministic compaction.
6. Add optional model summary provider only after deterministic tests pass.

## Tool Calling Design

Tool calls should use a strict envelope:

```json
{
  "tool_call_id": "toolcall_...",
  "tool_name": "read_file",
  "arguments": {},
  "decision_id": "dec_...",
  "evidence_refs": []
}
```

Tool results should use a strict envelope:

```json
{
  "tool_result_id": "toolres_...",
  "tool_name": "read_file",
  "status": "completed",
  "trust": "runtime_observed",
  "payload_ref": "world://tool_result/toolres_...",
  "prompt_summary": {},
  "evidence_refs": [],
  "artifacts": []
}
```

Rules:

- Unknown tools are denied.
- Invalid arguments are denied.
- Denials are recorded as evidence.
- Read tools produce `runtime_observed` refs.
- Mutating tools require leases.
- External tools require explicit policy.
- Tool summaries are redacted and budgeted.
- Tool result payloads are retained outside the prompt.

Mutation flow:

```text
RuntimeDecision(kind=mutation_intent)
  -> validate concrete operations
  -> classify risk
  -> pause if high risk
  -> derive MutationLease
  -> apply lease
  -> record MutationReceipt
  -> verify
```

This preserves the current good behavior and extends it to all future mutating tools.

## Implementation Phases

### Phase 1: Architecture Hardening

- Add `RuntimeStateMachine`.
- Move decision validation out of `ArtifactValidator` into `DecisionValidator`.
- Add transition tests for every mode.
- Add loop progress detection.
- Add explicit rejection reason enum/string constants.

### Phase 2: ToolBroker Registry

- Add tool definition model.
- Convert `repo_snapshot` and `read_file` to registered tools.
- Add tool call/result schemas.
- Add tool hooks and denial envelopes.
- Store raw tool result payloads by world ref.
- Add redaction and compact summaries.

### Phase 3: Context System

- Add context budgets.
- Add world evidence store abstraction.
- Add relevance selector.
- Add deterministic compaction policy.
- Add immutable evidence classes.
- Add context overflow recovery events.

### Phase 4: Provider Registry

- Expand model registry with capabilities.
- Add role-based provider selection.
- Move provider quirks out of runtime logic.
- Add cost accounting.
- Add provider fallback policy.

### Phase 5: General Planner Extension

- Define planner extension contract.
- Add schema-first plan proposal.
- Add plan lifecycle events.
- Keep workspace cleanup planner as deterministic test fixture.
- Add model-backed planner behind provider config.

### Phase 6: Artifacts and Verification

- Add artifact lifecycle states.
- Add validators per artifact type.
- Add file hash receipts.
- Add stale-verification prevention.
- Add verification tool registry integration.

### Phase 7: Session Replay and Surfaces

- Add session/run index.
- Add replay and inspect API.
- Add branch/resume lineage.
- Keep CLI/API/probe adapters thin.
- Add contract tests proving every surface uses the same runtime facade.

### Phase 8: Extension Ecosystem

- Stabilize extension protocol.
- Add hook timeout and ordering policy.
- Add tool-contributing extension flow through ToolBroker.
- Add context-contributing extension flow through ContextManager.
- Add extension failure and dead-letter inspection.

### Phase 9: Live Model Eval Matrix

- Add deterministic eval scenarios.
- Add live provider matrix scenarios.
- Add compaction stress scenarios.
- Add bad tool/bad mutation/bad evidence scenarios.
- Gate default live behavior on passing matrix results.

## Test Strategy

Test categories:

- Unit tests for state transitions.
- Unit tests for decision validation.
- Unit tests for tool schema validation and result envelopes.
- Unit tests for context selection and compaction.
- Unit tests for mutation lease validation and tamper denial.
- Integration tests for observe-plan-mutate-verify-finalize.
- Integration tests for pause/resume after process restart.
- Integration tests for extension failures.
- Replay tests for JSONL session rehydration.
- Live matrix probes for provider behavior.

Important invariant tests:

- Planner cannot plan without observed world refs.
- Model cannot create trusted world refs.
- Model cannot write files directly.
- Mutation cannot execute without issued lease.
- High-risk mutation pauses before lease/write.
- Finalize fails without verification unless explicit no-op.
- Compaction does not remove receipts or pause state.
- All surfaces enter through `AppV21AgentRuntime`.

## Non-goals

This design does not require:

- Rewriting existing `appV2/`.
- Copying PI or Hermes source code into AppV2.1.
- Building a full user-facing UI.
- Enabling unrestricted shell tools.
- Trusting model-authored verification.
- Adding background learning before core runtime correctness is stable.
- Making the planner more complex before context and tool evidence are stronger.

## Recommended Next Step

Start with Phase 1 and Phase 2 together:

- Add the formal state machine and decision validator.
- Upgrade ToolBroker into a registry-backed mediator.
- Keep provider behavior deterministic while those boundaries are hardened.

This gives later context, provider, planner, artifact, and extension work stable interfaces to build on.
