# Worker Runtime Deep Dive

## Question

Why is the decompressor/planner/worker system still getting stuck despite capable worker models, and what architecture should we move toward?

## Summary

The decompressor and planner are mostly serviceable for the next phase. The worker runtime is the bottleneck. It is directionally correct as a control plane, but it has accumulated too many compensating layers around one fragile contract: workers produce custom JSON decisions instead of using provider-native tool calls, then the kernel tries to normalize, classify, retry, synthesize, and replan after the fact.

The result is not one clean agent loop. It is a model/tool loop inside a worker group, inside a retry loop, inside a step loop, inside a replan loop, with schema-normalization and fallback synthesis scattered across schemas, compiler, tools, agentic runner, control, and runtime.

## Current Strengths

- Decompressor has a clear boundary: describe input, do not plan.
- Planner has a clear worker catalog, phase/mode contract, and deterministic validation.
- Kernel owns budgets, retries, artifact promotion, failed/partial artifacts, replan, and runtime matrix.
- Worker tools are permission-gated and mostly side-effect safe.
- Runtime matrix now gives useful trace evidence.
- Mutation safety moved closer to the tool boundary, which matches production agent patterns better than hard pre-dispatch mutation-scope blocking.

## Current Weak Points

- `app/worker_kernel/agentic.py` is too large and owns model decision normalization, group orchestration, tool loop, artifact QA, mutation fallback, verification fallback, prompts, registry construction, and retry-facing behavior.
- `app/worker_kernel/runtime.py` is also too large and owns step orchestration, budget normalization, retry scheduling, memory injection, artifact lifecycle, replan payloads, and terminal status reconciliation.
- `app/schemas.py` mixes core contracts with legacy parsing/path extraction. This makes safety depend on heuristic interpretation of free-form worker artifacts.
- Worker tool calls are not native function/tool calls. The model returns JSON text, then `_normalize_worker_decision` tries to repair many possible shapes. This is a reliability tax.
- Artifact validation mostly checks id presence/non-empty content, not typed per-artifact semantic correctness.
- Planner prompts are very long and prescriptive. They force the planner to solve worker-runtime weaknesses instead of only producing a clear workflow.
- Worker group fanout is sequential, not truly parallel, and most groups are single-instance anyway.
- Budget logic is inflated to support retries and finalization, but budget exhaustion is often a symptom of worker/tool-contract weakness, not real task complexity.
- Fallback synthesis can produce "completed" artifacts after model budget exhaustion, which is sometimes pragmatic but can hide incomplete work.
- Replan is structurally correct, but workers often emit poor diagnostic evidence, so replan can repair shape but not always intent.

## Probe Evidence

Recent live probes show repeated patterns:

- `file_workspace_cleanup` repeatedly fails around `docs/workspace_manifest.json`: moves succeed, but manifest creation or manifest schema fails.
- Several runs ended `completed_with_failed_verification`, meaning the kernel correctly detected failure, but the worker did not repair using test feedback.
- Some runs show `mutation_completed_without_write` or `mutation_completed_missing_required_writes`, proving workers sometimes report completion without doing required operations.
- Some successful runs include synthesized mutation artifacts after model budget exhaustion. That means the kernel can recover, but worker finalization remains weak.
- Replan appears only for semantic gaps or when a worker explicitly reports `needs_replan`; many failures are correctly kernel/worker-level and should not replan.

## External Guidance

Anthropic's "Building effective agents" distinguishes workflows from agents and recommends simple, composable patterns; autonomous agents are usually LLMs using tools based on environmental feedback in a loop. Their guidance stresses clear tool design and tool documentation.

Anthropic's tool-writing guidance says effective tools are clearly defined, use context judiciously, compose well, and are improved through evaluation-driven iteration.

Anthropic's Claude Agent SDK guidance says coding agents need a computer-like toolset: find files, edit/write files, run/lint/test, debug, and iterate. It also emphasizes the filesystem as context engineering and concrete verification loops.

OpenAI Agents SDK docs describe the core loop as: call model, if final output return, if handoff switch agent, if tool calls run tools and append results, repeat until max turns. OpenAI also emphasizes guardrails around each tool invocation and full tracing of model generations, tool calls, handoffs, guardrails, and custom events.

Context7 docs for OpenAI Agents SDK reinforce manager/sub-agent and handoff patterns, retries with backoff, and grouped traces. Context7 docs for Anthropic SDK show the same manual tool loop and tool-runner max-iteration pattern. Context7 Anthropic Skills docs reinforce progressive context loading, memory files, and delegating fanout across independent items.

## Interpretation

We are not stuck because the worker model is too weak. We are stuck because our worker harness asks the model to behave inside an artificial custom protocol while also navigating too many runtime abstractions.

The current design is safe-ish and observable, but not yet ergonomic for the model. Production agent harnesses usually make the easy path obvious:

1. Inspect current state.
2. Call exactly named tools with strict schemas.
3. See structured observations.
4. Act again or finish.
5. Verify with concrete feedback.

Our easy path is buried under planner artifact names, mutation_scope variants, retry metadata, expected artifact ids, custom JSON envelopes, group artifacts, and synthesized fallbacks.

## Recommended Direction

Do not merge decompressor and planner right now. Keep the pipeline, but simplify the worker runtime hard.

Recommended architecture:

1. Create one first-class `AgentRunLoop` object for worker execution. It should own turns, model calls, tool calls, max turns, observations, final output, and local repair.
2. Replace custom JSON tool-call emulation with provider-native tool calls where available. Keep JSON-decision mode only as a fallback adapter.
3. Split `agentic.py` into small modules: model adapter, run loop, group runner, artifact validator, prompt/context builder, fallback synthesizer.
4. Split `runtime.py` into kernel orchestration plus separate services: step executor, retry manager, replan manager, artifact store, result reconciler.
5. Promote typed artifact contracts for the important artifacts: mutation_scope, change_design, rollback_plan, verification_plan, change_summary, patch_diff, rollback_patch, verification_results, final_report.
6. Make tool outputs more "agent-native": high-signal summaries, explicit next allowed actions, and machine-readable errors that the same model turn can repair.
7. Let workers keep a short scratchpad/trajectory memory for the current step, but keep long-term memory kernel-owned and compact.
8. Reduce planner prompt burden. Planner should choose phases/workers/artifact names and semantic constraints; worker compiler should produce exact runtime contracts.
9. Treat verification as an evaluator loop, not just another worker. It should feed concrete failed assertions/commands back to mutation once before terminal failure when the failure is implementation-level.
10. Build a small eval suite from the scenarios that keep failing: file cleanup manifest, file archive handoff, greenfield API dependency config, webhook idempotency, payment retry.

## Migration Plan

Phase 1: Refactor without semantic behavior change.
- Extract `AgentRunLoop`.
- Extract `ArtifactStore`, `StepExecutor`, `RetryManager`, `ReplanManager`.
- Keep tests green and probes unchanged.

Phase 2: Native/strict tool adapter.
- Add OpenAI/Anthropic-style tool adapter interface.
- Keep OpenRouter JSON fallback, but isolate normalization in one adapter.
- Classify malformed tool output as model behavior before consuming full worker retries.

Phase 3: Typed artifact contracts.
- Add schemas and validators for top worker artifacts.
- Repair artifacts before retrying whole worker when only finalization is bad.

Phase 4: Verification feedback path.
- For failed verification after mutation, route one correction attempt back to the mutation step with failed command evidence and current diff.
- Do not call planner replan unless verification proves plan/user intent drift.

Phase 5: Eval discipline.
- Convert probe scenarios into repeatable eval cases with graded outcomes: filesystem state, test returncode, artifact quality, trace labels, and runtime cost.

## Bottom Line

Current decompressor and planner are good enough to move forward. The worker runtime is not production-grade yet. The fix is not "more prompts" or "more retries"; it is a cleaner agent harness with native tool calls, typed artifacts, one obvious agent loop, and verification feedback that repairs implementation mistakes locally.

## References

- Context7: `/openai/openai-agents-python`, agent loop, handoffs, retries, tracing.
- Context7: `/anthropics/anthropic-sdk-typescript`, tool use loop and tool choice.
- Context7: `/anthropics/skills`, progressive disclosure, memory, sub-agent fanout.
- Anthropic: Building Effective AI Agents, https://www.anthropic.com/engineering/building-effective-agents
- Anthropic: Writing Effective Tools for AI Agents, https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic: Building Agents with the Claude Agent SDK, https://claude.com/blog/building-agents-with-the-claude-agent-sdk
- Anthropic: Demystifying Evals for AI Agents, https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- OpenAI: Agents SDK overview, https://platform.openai.com/docs/guides/agents-sdk/
- OpenAI Agents SDK: Running agents, https://openai.github.io/openai-agents-python/running_agents/
- OpenAI Agents SDK: Guardrails, https://openai.github.io/openai-agents-python/guardrails/
- OpenAI Agents SDK: Tracing, https://openai.github.io/openai-agents-python/tracing/
- OpenAI: Trace grading, https://platform.openai.com/docs/guides/trace-grading
