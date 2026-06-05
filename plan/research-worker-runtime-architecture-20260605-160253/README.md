# Worker Runtime Architecture Deep Dive

Generated: 2026-06-05 16:02 Asia/Yangon

## Question

Where are the decompressor, planner, and worker runtimes now, why is the worker runtime still inconsistent, and what architecture should replace the current patch-heavy worker loop?

## Sources Read

- Local runtime code under `app/decompressor`, `app/planner`, `app/worker_kernel`, `app/graph.py`, and `app/schemas.py`.
- Recent probe context, especially the successful Xiaomi run that completed only after retry/replan/retry behavior.
- Context7 documentation for `/openai/openai-agents-python`.
- Context7 documentation for `/websites/platform_claude_en_api`.
- Official OpenAI Agents SDK docs:
  - https://github.com/openai/openai-agents-python/blob/main/docs/agents.md
  - https://github.com/openai/openai-agents-python/blob/main/docs/tools.md
  - https://github.com/openai/openai-agents-python/blob/main/docs/guardrails.md
- Official Anthropic tool-use docs:
  - https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
  - https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools

## Current State

The decompressor is reasonably clean. It is a narrow LLM boundary that produces an `Envelope`, tracks latency/repair metrics, and does not try to plan execution.

The planner is usable but heavier than ideal. It creates phase-aware plans, validates worker types, artifacts, write-scope wiring, literal JSON keys, and replan payloads. It is doing some execution-contract work that probably belongs closer to the worker compiler.

The worker runtime is the bottleneck. It now has good pieces: `AgentRunLoop`, `ToolObservation`, permission-gated tools, `WorkerMemoryController`, artifact quality checks, runtime matrix tracing, retry ownership classification, and internal replan. But these pieces are layered on top of older control flow rather than replacing it.

The latest successful live probes show the real status: the system can finish meaningful file-management/coding tasks, but often only after model budget hits, artifact-quality repair, retry memory injection, and sometimes planner replan. That means the model is not the main root cause anymore. Runtime semantics are.

## Key Code Findings

- `app/worker_kernel/runtime.py` is still a large control plane with per-step execution, retry, budget normalization, verification feedback repair, replan handling, artifact promotion, and final reconciliation in one file.
- `app/worker_kernel/agentic.py` still owns too much: worker decision normalization, worker group iteration, tool execution handling, artifact quality repair, fallback synthesis, prompt construction, and worker registry construction.
- `app/worker_kernel/tools.py` is a strong but overloaded boundary. It mixes tool definitions, permission checks, write-policy validation, local command execution, web fetch/search, JSON manifest semantics, mutation-scope logic, and repairable denial generation.
- `app/schemas.py` contains too many cross-layer concepts in one place. It mixes planner-facing plan schemas, worker task schemas, tool/write-policy schemas, runtime result schemas, and graph state.
- `app/planner/validator.py` requires detailed mutation and verification wiring. That protects the runtime, but it also means some low-level execution assumptions are embedded in planner validation.
- Worker prompts are now very specific and domain-shaped. That helped probes, but it is a sign prompts are carrying semantics that should live in typed tools, validators, and deterministic artifact derivation.

## Why We Are Getting Stuck

The runtime has too many overlapping loops:

- decompressor draft/repair loop
- planner draft/repair/replan-repair loop
- graph sequential node loop
- kernel per-step loop
- kernel per-attempt respawn loop
- agent model/tool/final loop
- local malformed-decision repair loop
- write-operation denial repair loop
- artifact-quality repair/fallback loop
- verification feedback mutation-repair loop
- planner replan loop

The issue is not that loops exist. Production agents need loops. The issue is that ownership is split. A failure can be interpreted by the tool layer, agent layer, memory layer, kernel controller, verification-repair path, and replan path. That creates inconsistent outcomes.

The worker runtime also has a trust inversion problem. The model is asked to produce exact artifacts, exact report schemas, exact mutation scopes, exact verification claims, and sometimes exact rollback/diff metadata. But the tools already know the real filesystem state, command output, diffs, manifests, and write results. Tool evidence should be the source of truth; the model should decide and explain, not author proof that the kernel can derive.

The artifact model is too generic at the runtime boundary. `ArtifactPayload.content` being `Any` is fine for planner compatibility, but worker finalization needs typed step-specific contracts earlier. Today many failures are caught after a worker spends calls, not before the task is compiled or after a tool event can deterministically produce the artifact.

The replan boundary is still too porous. The prompts tell workers when to use `needs_replan`, and the controller classifies some runtime-owned failures, but the model can still escalate semantic-looking failures from tool/runtime confusion.

## External Design Guidance

OpenAI Agents SDK centers the design on agents with instructions, tools, structured outputs, handoffs, guardrails, sessions/context, lifecycle hooks, and tracing. It explicitly frames a manager pattern where a central orchestrator invokes specialist agents as tools and keeps control, or a handoff pattern where control transfers. Our design is closer to manager/orchestrator, so the kernel should keep control and expose specialist workers/tools through one consistent run loop.

Anthropic tool-use docs describe the canonical client-tool loop: model emits a structured tool request, application executes it, then tool results return to the model as observations. They also emphasize strict tool schemas, tool choice control, and the cost/latency overhead of tool definitions and tool-result blocks. This supports a simpler worker loop with better tool schemas and fewer prompt-only contracts.

Both sources point in the same direction: one agent loop, strict tool schemas/guardrails, typed observations, compact memory/session context, deterministic validation, and clear tracing. They do not point toward more nested repair paths.

## Recommended Architecture

Do not merge decompressor and planner right now. That would reduce one graph hop but would not fix the worker inconsistency. The decompressor and planner are not where the current thrash lives.

Refactor the worker runtime as a V2 control-plane beside the current runtime, keeping `WorkerKernelRuntime.run(plan, envelope=...)` stable:

1. `TaskFrameCompiler`
   - Converts `Envelope + PlanStep + completed artifacts` into one typed `TaskFrame`.
   - Resolves canonical artifact names, permissions, write policy, expected outputs, and success criteria before dispatch.
   - Produces preflight errors that are clearly `kernel`, `plan`, or `user/context` owned.

2. `AgentRunController`
   - Owns the single worker loop: model turn, tool call, tool observation, final candidate, local repair.
   - No separate worker-group repair/fallback semantics outside this controller.
   - Worker groups can remain planner-visible names, but internally they should run through this same controller.

3. `ToolRouter`
   - Owns strict tool schemas, permission gates, write policy, command allowlists, timeouts, and tool result envelopes.
   - Emits `ToolEvent`/`ToolObservation`; does not decide final task status.

4. `EvidenceStore`
   - Stores tool events, file diffs, command results, web sources, write ledgers, and denials.
   - Feeds compact memory to retries and verification.
   - Replaces scattered memory snapshots and ad hoc failed-step artifacts.

5. `ArtifactDeriver`
   - Deterministically creates artifacts that tools can prove: `patch_diff`, `rollback_patch`, `changed_files`, manifest validation, scope verification, command evidence, and file operation ledgers.
   - LLM-authored artifacts are reserved for explanations, summaries, tradeoffs, and user-facing reports.

6. `IssueClassifier`
   - Single owner of failure classification.
   - Runtime/tool/model/budget failures retry locally.
   - Semantic plan failures replan.
   - User ambiguity blocks or asks.
   - Verification implementation failures trigger one targeted mutation repair through the same controller, not a special loop.

7. `ResultReconciler`
   - Converts controller outcome plus evidence/artifacts into final `Result`.
   - This is the only place that decides `completed`, `failed`, `blocked`, `needs_replan`, `budget_exceeded`, or `completed_with_failed_verification`.

## Simplification Targets

- Collapse verification feedback repair into the normal retry path with a retry cause of `verification_failed`.
- Move fallback synthesis out of `agentic.py` into deterministic `ArtifactDeriver`.
- Move tool policy/write-policy code out of `tools.py` into a separate policy module.
- Keep prompts shorter by moving exact manifest/report/file-operation contracts into typed tool schemas and artifact contracts.
- Keep worker memory, but make it event-derived from the `EvidenceStore`, not separately interpreted by the kernel and agent group.
- Treat `needs_replan` as a kernel-approved status, not merely a worker-authored status.
- Add native provider tool-call support behind the existing adapter boundary once the V2 event loop is stable.

## Best Next Implementation Plan

1. Freeze current runtime as `v1` and keep probes running against it.
2. Add V2 skeleton behind an env flag such as `WORKER_RUNTIME_VERSION=v2`, with the same public `run(plan, envelope=...)` API.
3. Extract `ToolObservation` into a richer `ToolEvent` model and make tools return that consistently.
4. Add `EvidenceStore` and route all tool observations, writes, denials, command outputs, and artifact derivations through it.
5. Move mutation/verification artifact synthesis into `ArtifactDeriver`.
6. Replace special verification repair with a normal retry event carrying verification failure evidence.
7. Make `IssueClassifier` the only runtime/replan ownership decider.
8. Run the existing probe matrix against both runtimes until V2 is equal or better.
9. Delete V1 compatibility shims only after the probe matrix is stable.

## Bottom Line

We are not off the rails, but we are at the point where more local patches will have diminishing returns. The current worker runtime proved the concept; now it needs a control-plane refactor. The model is good enough. The missing piece is a cleaner runtime state machine where tools produce evidence, deterministic components derive proof artifacts, and the LLM focuses on choosing actions and explaining results.
