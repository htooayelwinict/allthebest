# AppV2 Runtime-First Architecture Plan: Pi + Hermes + Typed AppV2

Date: 2026-06-15

Objective: build a new greenfield `appV2.1/` runtime-first agent harness that combines Pi-style observe-act-revise behavior, Hermes-style runtime/tool/context discipline, and AppV2 typed evidence, mutation leases, artifact validators, pause/resume, and verification.

Core decision: do not refactor the existing `appV2/` planner-first implementation. Create `appV2.1/` as a new implementation. Decomposer and planner become runtime extensions. Runtime owns state, observation, tools, planning gates, mutation leases, validation, context compaction, pause/resume, and finalization.

Implementation workspace:
- User-facing runtime directory: `appV2.1/`
- Importable internal package: `appV2.1/appv21/`
- Tests: `tests/appv21/`
- Existing `appV2/` remains untouched except for temporary adapter/probe comparison if explicitly needed.

## 1. Problem with planner-first AppV2

Current issue:
- AppV2 asks decomposer/planner to produce artifact topology, mutation scope, and phase contracts before the runtime has observed the repository.
- The worker then spends money repairing planner guesses through topology repair, synthesis aliases, policy expansion, retry loops, and model-budget patches.
- The repeated live probe failures are architectural symptoms, not isolated bugs.

Target:
- Runtime observes first.
- Planner only plans from observed world state.
- Policy validates concrete operations and leases; it does not invent scope from natural language.

Acceptance:
- No new `appV2.1/` production path depends on `decomposer -> planner -> policy compiler -> worker`.
- Existing `appV2/` code is not refactored to force the new architecture.
- Any compatibility adapter is external to the `appV2.1/` runtime core.

## 2. Hermes lessons to adopt

Use Hermes patterns:
- One core runtime behind all surfaces.
- Tool mediator/broker controls tool schema, execution, hooks, validation, and result shaping.
- Transport adapters are thin and do not own reasoning logic.
- Context management is a runtime service with preflight, post-response, and overflow recovery.
- Session/runtime events are observable and resumable.

Do not copy blindly:
- Avoid one huge gateway coordinator.
- AppV2 needs stronger typed world state than Hermes-style generic tool history.

Acceptance:
- AppV2 has one `AgentRuntime` facade.
- CLI/API/probe paths call the same runtime.
- Tool execution flows through one `ToolBroker`.

## 3. Pi lessons to adopt

Use Pi behavior:
- Observe before planning.
- Plan only enough for the current slice.
- Act, observe, revise.
- Treat planning as fluid, not sacred.
- Let tool results drive reasoning.

AppV2 improvement over Pi:
- Typed world state.
- Runtime-owned evidence ledgers.
- Mutation leases.
- Artifact validators.
- Verification receipts.
- Pause/resume state.

Acceptance:
- A run can revise its plan after any observation or failed action.
- Planner output is advisory and rejectable by runtime.

## 4. Better AppV2 principles

Principles:
- Runtime is authority.
- World evidence beats model claims.
- Conversation context and world context are separate.
- Planner/decomposer are extensions.
- Policy validates and leases concrete operations.
- Artifacts have lifecycle and trust states.
- Mutations require a lease.
- Verification is evidence-backed.
- Pauses are runtime states.

Non-goals:
- Do not make planner smarter with more upfront schema.
- Do not infer mutation scope before observation.
- Do not keep adding artifact alias repairs as core architecture.

Acceptance:
- Architecture doc and code boundaries reflect these principles.

## 5. State harness

Implement a custom event-sourced state harness.

Core state:
```python
class AgentState:
    session_id: str
    run_id: str
    request: RequestEnvelope
    mode: RuntimeMode
    conversation: ConversationState
    world: WorldState
    plan: PlanState | None
    phase: PhaseState | None
    tools: ToolState
    mutations: MutationState
    verification: VerificationState
    context: ContextState
    pauses: list[PauseState]
    costs: CostState
```

Runtime modes:
- `START`
- `OBSERVE`
- `THINK`
- `PLAN`
- `ACT`
- `VERIFY`
- `REVISE`
- `COMPACT`
- `PAUSE`
- `FINALIZE`
- `FAILED`

Event categories:
- input events
- observation events
- tool events
- planning events
- mutation events
- artifact events
- verification events
- context events
- pause/resume events
- terminal events

Acceptance:
- Runtime state can be rebuilt from events.
- Probe output includes event stream and reduced state summary.
- Paused runs can resume from persisted state.

## 6. Agent loop

Replace planner-first execution with one runtime loop:

```text
while not terminal:
  ingest events
  reduce state
  build context
  select next decision source
  get model/extension decision
  validate decision
  execute allowed action/tool
  append events
  compact if needed
  pause/finalize if needed
```

Decision branches:
- `observe`
- `plan`
- `tool_calls`
- `mutation`
- `verify`
- `ask_human`
- `final`

Acceptance:
- There is no separate downstream worker that obeys a precomputed plan.
- Planner extension is callable from inside the loop.
- The loop can alternate observe/plan/tool/mutate/verify dynamically.

## 7. Dual context management

Split context into two planes.

Conversation context:
- user request
- relevant messages
- task summary
- active decisions
- retry/repair summaries
- user preferences

World context:
- repo maps
- file hashes
- tool observations
- file contents
- artifact ledger
- mutation receipts
- verification receipts
- known failures

Rules:
- Conversation can be summarized.
- World state must be evidence-backed and referenced by IDs.
- Full tool payloads live in world store, not indefinitely in prompt.

Acceptance:
- Prompt context uses world refs and summaries.
- Full raw tool outputs remain available outside prompt.
- Compaction never drops mutation receipts or verification evidence.

## 8. ToolBroker and tool result envelopes

Create `ToolBroker` as runtime mediator.

Responsibilities:
- expose available tool specs
- validate tool-call schema
- enforce permissions
- enforce mutation leases
- execute tools
- normalize outputs
- redact secrets
- store full results in world state
- return compact prompt summaries
- trigger artifact/evidence validators

Tool result envelope:
```json
{
  "tool_result_id": "toolres_123",
  "tool_name": "read_file",
  "status": "completed",
  "trust": "runtime_observed",
  "full_result_ref": "world://toolres_123",
  "prompt_summary": {},
  "artifacts": []
}
```

Acceptance:
- Model never directly writes trusted tool observations.
- Tool results are runtime-owned.
- Prompt receives compact summaries and refs.

## 9. MutationLease model

Replace upfront mutation policy guessing with runtime mutation leases.

Flow:
```text
planner/model proposes concrete operations
runtime derives touched paths
policy gate validates against observed repo and user intent
runtime issues MutationLease
executor applies only leased operations
runtime records mutation receipt
verifier checks result
```

Mutation lease:
```json
{
  "lease_id": "lease_123",
  "operation_batch_id": "move_workspace_files",
  "allowed_operations": [],
  "allowed_sources": [],
  "allowed_destinations": [],
  "expires_after_turn": 12,
  "requires_human": false,
  "risk_level": "low"
}
```

Acceptance:
- Move operations validate both source and destination.
- Lease is derived from concrete operations, not phase metadata.
- No mutation can execute without a valid lease.

## 10. Artifact validator model

Artifacts remain useful, but they become runtime-state proposals.

Validator layers:
- schema validator
- evidence validator
- lifecycle validator
- trust validator
- dependency validator

Artifact lifecycle:
- `proposed`
- `accepted`
- `rejected`
- `runtime_verified`
- `human_approved`
- `stale`
- `superseded`

Trust levels:
- `model_reported`
- `runtime_observed`
- `runtime_verified`
- `human_approved`

Acceptance:
- Model can propose artifacts.
- Runtime decides whether artifacts become trusted state.
- Runtime rejects artifacts with unsupported evidence.

## 11. Skill system

Skills are runtime plugins, not global prompt dumps.

Skill pack contributes:
- activation rules
- prompt patch
- tool preferences
- artifact templates
- validators
- compaction hints
- verification checklist

Example:
```text
WorkspaceCleanupSkill
  activates on file-management requests
  prefers repo_snapshot + classify_file_management_candidates
  provides move_plan template
  provides workspace manifest schema
  provides verification checklist
```

Acceptance:
- Only activated skills affect prompt.
- Skills can add validators/templates without core code branching.
- Skill activation uses request + world context.

## 12. Planner/decomposer extension contracts

Decomposer extension:
- structures user request into intent, constraints, ambiguity.
- cannot own execution.

Planner extension:
- receives observed world state.
- proposes next plan slice.
- can request more observation.
- can propose mutation intent.
- can propose verification intent.
- can request pause.

Planner input requires:
- request
- world summary
- relevant world refs
- constraints
- current failures

Planner output:
```json
{
  "intent": "",
  "steps": [],
  "required_tools": [],
  "mutation_intent": {},
  "verification_intent": {},
  "needs_observation": [],
  "unknowns": []
}
```

Acceptance:
- Planner cannot produce mutation scope before observation exists.
- Runtime can reject planner output and request observation/revision.

## 13. Pause/HITL state

HITL belongs to runtime, not planner.

Pause types:
- `approval_required`
- `scope_expansion_required`
- `ambiguous_goal`
- `destructive_action`
- `budget_exceeded`
- `unsafe_tool_request`

Pause state:
```json
{
  "pause_id": "pause_001",
  "type": "scope_expansion_required",
  "summary": "",
  "options": [],
  "resume_event": null
}
```

Acceptance:
- Pause serializes to event log.
- Resume injects a typed `HumanInputReceived` event.
- Runtime continues from same state harness.

## 14. Compaction strategy

Use Hermes-style dual-pass compaction adapted for AppV2.

Pass 1: deterministic world compaction
- dedupe repeated repo snapshots
- collapse repeated read results
- keep latest file hash
- preserve mutation receipts
- preserve verification receipts
- replace large payloads with refs + summaries

Pass 2: conversation compaction
- summarize user goal
- summarize decisions made
- summarize current plan
- summarize failures and do-not-repeat constraints
- preserve last active tool-call pairs exactly

Rules:
- Never break active assistant/tool message pairs.
- Never summarize away world evidence without a world ref.
- Never compact away open mutation lease or pause state.

Acceptance:
- Long tool-heavy runs preserve correctness after compaction.
- Prompt token budget stays bounded.
- World store retains full evidence refs.

## 15. Greenfield implementation and old-code boundary

Do not start by deleting current `appV2/`. Treat it as legacy comparison/reference only.

Do not port:
- planner-first runtime flow
- policy compiler as mutation-scope inventor
- worker runtime as downstream executor
- artifact topology repair as correctness core
- probe-specific synthesis alias patching

Rebuild or selectively copy only stable primitives:
- model client redaction
- runtime matrix/event logging
- tool registry
- policy gate primitives
- ledger concepts
- schemas worth keeping
- verification tools

Acceptance:
- `appV2.1/appv21/` does not import planner-first runtime modules from `appV2/`.
- Any reused code is copied/adapted into `appV2.1/appv21/` with runtime-first ownership.
- The old `appV2/` remains runnable only as legacy until the new probe path replaces it.

## 16. Probe success criteria

Primary probe:
```text
file_workspace_cleanup
```

Success means:
- runtime observes repo before planning
- planner receives repo map/world refs
- concrete move plan is generated from observed files
- mutation lease covers source and destination paths
- mutation applies file moves and manifest write
- verification checks moved files, held files, manifest
- final result is completed

Failure diagnostics must identify:
- state mode
- current decision
- failing validator
- relevant world refs
- mutation lease state
- context compaction state

Acceptance:
- Probe passes without planner-first compatibility path.
- Probe output includes event log and compact world summary.

## 17. Implementation sequence

Phase 1: Greenfield skeleton
- Create `appV2.1/` workspace.
- Create importable `appV2.1/appv21/` package.
- Create `tests/appv21/`.
- Write runtime-first architecture doc.
- Define `AgentState`, `RuntimeEvent`, reducers, and runtime modes.
- Define `WorldState` and `ConversationState`.

Phase 2: ToolBroker
- Move tool execution behind broker.
- Add result envelope.
- Store full results as world refs.

Phase 3: Observer-first loop
- Implement mandatory initial observation.
- Build repo/file/dir map world artifact.

Phase 4: Extensions
- Add decomposer extension.
- Add planner extension requiring world context.
- Add skill router.

Phase 5: MutationLease
- Add lease derivation from concrete operations.
- Gate mutations through lease.
- Record mutation receipts.

Phase 6: Validators
- Add artifact validators.
- Add evidence/trust/lifecycle validation.

Phase 7: Context management
- Add dual context manager.
- Add deterministic world compaction.
- Add conversation compaction.

Phase 8: Pause/resume
- Add runtime pause states.
- Add resume event handling.

Phase 9: New probe path
- Add `scripts/live_appv21_runtime_probe.py`.
- Run probes against `appV2.1/appv21/` only.
- Compare legacy `appV2/` only for diagnostics, not as a dependency.

## 18. Review gates

Each phase requires:
- implementation worker
- spec review
- code-quality review
- focused tests
- probe check when relevant

No phase should add planner-first repairs as a shortcut. If a feature needs planner-first assumptions, reject the feature shape and redesign it around observed world state.

Review questions:
- Does runtime own truth?
- Did observation happen before planning?
- Is world evidence referenced, not hallucinated?
- Is mutation scope derived from concrete operations?
- Is context compaction safe for active work?
- Can the run pause/resume?

## 19. Final target

Final AppV2 shape:

```python
runtime = AppV2AgentRuntime(
    tool_broker=ToolBroker(...),
    extensions=[
        ObserverExtension(),
        DecomposerExtension(),
        PlannerExtension(),
        SkillRouter(),
        VerifierExtension(),
    ],
    context_manager=DualContextManager(...),
    state_store=EventSourcedStateStore(...),
)

result = runtime.run(user_request)
```

Runtime behavior:
```text
receive request
observe world
activate skills
decompose if needed
plan next slice
execute tools
validate artifacts
derive mutation lease
mutate
verify
compact context
revise or finalize
```

Definition of done:
- `appV2.1/` is a new runtime-first implementation, not a refactor of `appV2/`.
- `appV2.1/` does not depend on planner-first architecture.
- Hermes-style runtime/tool/context discipline is present.
- Pi-style observe-act-revise loop is present.
- Typed AppV2 world state, leases, validators, pause/resume, and verification receipts are present.
- The file workspace cleanup probe completes through the runtime-first path.
