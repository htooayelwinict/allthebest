# AppV2.1 Next Phase: Agentic Loop + Decision Protocol

Date: 2026-06-15
Status: Planned
Scope: Greenfield `appV2.1/` only

## Objective

Turn AppV2.1 from a runtime-first deterministic harness into a real Pi/Hermes-style agent runtime:

- Conversation enters the agent loop first.
- The loop decides whether to observe, plan, tool-call, mutate, verify, pause, compact, or finalize.
- Planner/decomposer become optional extensions, not mandatory first-class phases.
- ToolBroker becomes the only mediation boundary for tools, mutation leases, tool result compaction, and artifact validation.
- Session lineage, event bus, extension hooks, and prompt/context contracts remain runtime-owned.

## Design Principle

The runtime owns safety and state. The model owns reasoning and next-step selection.

This means:

- The model can propose decisions.
- The runtime validates and executes decisions.
- Extensions can advise.
- Tools can observe or act only through ToolBroker.
- Mutation leases remain runtime-issued and runtime-enforced.
- Verified artifacts require runtime evidence.

## Why This Is Next

The current AppV2.1 implementation fixed the original mistake: planner no longer runs before observation. But it is still mostly deterministic:

- `ObserverExtension` always runs.
- `DecomposerExtension` always runs.
- `PlannerExtension` always runs.
- Runtime then applies one generated mutation plan.

That is safe, but not Pi-like. Pi is an agent loop, not a planner pipeline. Hermes also keeps one runtime facade and a mediated tool boundary. AppV2.1 needs the same loop shape with AppV2-grade gates.

## Target Architecture

```text
User/Event
  -> AppV21AgentRuntime.run_turn()
  -> load/reduce session state
  -> build prompt/context/tool contract
  -> model/provider returns RuntimeDecision
  -> runtime validates decision
  -> ToolBroker / extensions / verifier / pause / compactor execute
  -> append events
  -> repeat until terminal or pause
```

## Phase 1: Typed Runtime Decision Protocol

Create `appV2.1/appv21/runtime/decisions.py`.

Decision types:

- `ObserveDecision`
- `ReadFileDecision`
- `PlanDecision`
- `ToolCallDecision`
- `MutationIntentDecision`
- `VerifyDecision`
- `PauseDecision`
- `CompactDecision`
- `FinalizeDecision`

Each decision must include:

- `decision_id`
- `kind`
- `reason`
- `evidence_refs`
- `payload`

Rules:

- Decisions are proposals, not actions.
- Runtime rejects decisions that reference missing evidence.
- Runtime rejects mutation decisions not compiled into a lease.
- Runtime rejects finalization without verified artifact/evidence.

Acceptance criteria:

- Decision parser validates known kinds.
- Unknown decision kinds produce `DecisionRejected` event.
- Mutation decisions cannot directly write files.

## Phase 2: Provider Adapter Seam

Create `appV2.1/appv21/providers/`.

Files:

- `providers/base.py`
- `providers/deterministic.py`
- `providers/null_model.py`

Initial provider contract:

```python
class AgentProvider(Protocol):
    def decide(self, prompt_payload: dict) -> RuntimeDecision: ...
```

Initial providers:

- `DeterministicWorkspaceProvider`: preserves current probe behavior through the new decision protocol.
- `NullModelProvider`: returns pause/failure-safe decisions for test fallback.

Do not wire a live model yet unless explicitly requested. The protocol comes first.

Acceptance criteria:

- Current probe still passes through provider decision flow.
- Runtime no longer directly calls planner as mandatory step.
- Planner is invoked only if provider emits `PlanDecision` or runtime fallback requires it.

## Phase 3: Agent Loop Refactor

Update `AppV21AgentRuntime` to support loop execution.

New methods:

- `run(user_goal, constraints=None)` remains public convenience.
- `run_turn(state)` builds prompt, gets decision, routes decision.
- `route_decision(state, decision)` validates and executes.
- `should_continue(state)` handles terminal/pause/max-turn limits.

Loop modes:

- `OBSERVE`
- `THINK`
- `PLAN`
- `ACT`
- `VERIFY`
- `COMPACT`
- `PAUSE`
- `FINALIZE`
- `FAILED`

Hard guardrails:

- `max_turns` default: 12.
- Repeated same rejected decision: fail after 3.
- Missing required observation: runtime can force `ObserveDecision` once.
- No mutation without lease.
- No finalize without artifact validation.

Acceptance criteria:

- Event order proves conversation -> prompt -> decision -> tool/action.
- Planner is no longer a fixed early phase.
- Runtime can pause without losing state.

## Phase 4: ToolBroker as Full Tool Mediator

Extend `appV2.1/appv21/tools/broker.py`.

Add:

- `execute_tool_call(tool_name, arguments)`
- `validate_tool_call(tool_name, arguments)`
- `tool_result_envelope(...)`
- `compact_tool_result(...)`
- `tool_policy_for(state)`

Tool result envelope:

```json
{
  "tool_result_id": "toolres_...",
  "tool_name": "repo_snapshot",
  "status": "completed|failed|denied",
  "trust": "runtime_observed|runtime_owned",
  "payload": {},
  "prompt_summary": {},
  "evidence_refs": []
}
```

Rules:

- Tool outputs go into world refs, not raw conversation by default.
- Large outputs require prompt summary.
- Mutating tools are blocked unless backed by issued lease.
- Tool denial emits `ToolCallDenied`.

Acceptance criteria:

- Runtime never calls tools directly outside broker.
- Tool output is compactable without losing evidence IDs.
- Bad tool call is denied and loop can revise.

## Phase 5: Planner and Decomposer as Extensions

Move current behavior behind extension hooks.

Planner extension becomes:

- advisory
- callable by `PlanDecision`
- evidence-bound to `repo_snapshot`

Decomposer extension becomes:

- advisory skill/context enricher
- not a required phase

Rules:

- Planner cannot mutate.
- Planner cannot create leases.
- Planner can produce mutation intent only from observed refs.
- Runtime compiles intent into lease.

Acceptance criteria:

- Runtime works with planner extension disabled for non-planning decisions.
- Runtime works with deterministic provider using planner extension for workspace cleanup.
- Planner request without observed repo map produces observation need, not mutation scope.

## Phase 6: HITL Pause/Resume

Add persistent pause state.

Files:

- `runtime/pause.py`
- update `state/models.py`
- update `runtime/session_store.py`

Events:

- `PauseRequested`
- `PauseResolved`
- `RunPaused`
- `RunResumed`

Pause types:

- `approval_required`
- `ambiguous_goal`
- `high_risk_mutation`
- `missing_context`
- `tool_blocked`

Resume contract:

```python
runtime.resume(pause_id: str, user_input: dict) -> dict
```

Rules:

- High-risk leases require pause.
- Ambiguous destructive requests require pause.
- Resume appends event and continues loop from persisted state.

Acceptance criteria:

- Probe can pause on high-risk mutation.
- Resume can approve/deny and continue.
- Session JSONL contains pause lineage.

## Phase 7: Dual Context Compaction

Implement Hermes-style dual compaction.

Files:

- `context/compactor.py`
- update `context/manager.py`

Two layers:

- Runtime compaction: deterministic digest from events/world refs/tool summaries.
- Model compaction: optional provider-generated conversation summary later.

Preserved invariants:

- active request
- current mode
- open pause
- active leases
- latest observed world refs
- verification receipts
- artifact evidence refs
- unresolved errors

Events:

- `ContextCompactionRequested`
- `ContextCompacted`
- `ContextCompactionRejected`

Acceptance criteria:

- Compaction never drops evidence needed for artifact validation.
- Tool results can be summarized but evidence IDs remain stable.
- Runtime can continue after compaction.

## Phase 8: Artifact Validators as Tool Blockers

Strengthen validators so they block invalid finalization and unsafe tool loops.

Add validator interfaces:

- `validate_decision(decision, state)`
- `validate_tool_call(tool_call, state)`
- `validate_tool_result(tool_result, state)`
- `validate_artifact(artifact, state)`

Rules:

- Artifact validator rejects runtime-verified artifacts without evidence.
- Tool blocker rejects writes outside lease.
- Verifier rejects stale manifest and mismatched move receipts.
- Finalize requires at least one accepted artifact or explicit no-op verified result.

Acceptance criteria:

- Bad model finalization is rejected.
- Bad model mutation intent is denied before write.
- Tool-blocked run can revise or pause.

## Phase 9: Probe Suite

Create stronger probes under `scripts/` and tests under `tests/appv21/`.

Required probes:

1. `live_appv21_agent_loop_probe.py`
   - model/provider emits observe -> plan -> mutate -> verify -> finalize.

2. `live_appv21_bad_mutation_probe.py`
   - provider proposes unsafe write.
   - broker denies.
   - runtime revises or fails safely.

3. `live_appv21_pause_resume_probe.py`
   - high-risk mutation requests approval.
   - resume approves.
   - run completes.

4. `live_appv21_context_compaction_probe.py`
   - large world/tool context triggers compaction.
   - run continues and verifies.

5. `live_appv21_planner_disabled_probe.py`
   - planner extension disabled.
   - provider can still observe/read/finalize safe no-op.

Acceptance criteria:

- All probes produce JSON reports under `plan/`.
- Reports include event order, decision count, tool count, denied count, pause count, compaction count.
- Tests assert safety boundaries, not only happy path completion.

## Implementation Order

1. Decision protocol.
2. Provider adapter seam with deterministic provider.
3. Agent loop routing.
4. ToolBroker mediation upgrade.
5. Planner/decomposer extension demotion.
6. HITL pause/resume.
7. Dual context compaction.
8. Artifact/tool validators.
9. Probe suite.

## Files Expected To Change

New files:

- `appV2.1/appv21/runtime/decisions.py`
- `appV2.1/appv21/providers/__init__.py`
- `appV2.1/appv21/providers/base.py`
- `appV2.1/appv21/providers/deterministic.py`
- `appV2.1/appv21/providers/null_model.py`
- `appV2.1/appv21/runtime/pause.py`
- `appV2.1/appv21/context/compactor.py`
- `scripts/live_appv21_agent_loop_probe.py`
- `scripts/live_appv21_bad_mutation_probe.py`
- `scripts/live_appv21_pause_resume_probe.py`
- `scripts/live_appv21_context_compaction_probe.py`
- `scripts/live_appv21_planner_disabled_probe.py`

Existing files likely to change:

- `appV2.1/appv21/runtime/agent_runtime.py`
- `appV2.1/appv21/runtime/services.py`
- `appV2.1/appv21/runtime/reducer.py`
- `appV2.1/appv21/state/events.py`
- `appV2.1/appv21/state/models.py`
- `appV2.1/appv21/tools/broker.py`
- `appV2.1/appv21/extensions/planner.py`
- `appV2.1/appv21/extensions/decomposer.py`
- `appV2.1/appv21/context/manager.py`
- `appV2.1/appv21/validators/artifacts.py`
- `tests/appv21/test_runtime_first_probe.py`

## Non-Goals

- Do not refactor legacy `appV2/`.
- Do not wire a live external model yet.
- Do not make planner mandatory again.
- Do not let extensions directly mutate files.
- Do not let model-authored schema become executable policy.

## Success Definition

AppV2.1 is successful after this phase when:

- It behaves like Pi at the loop level.
- It behaves like Hermes at the runtime/tool/context boundary.
- It keeps AppV2 safety through leases, validators, receipts, and evidence refs.
- Planner and decomposer are useful extensions, not architectural masters.
- The probe suite proves bad model behavior is contained.
