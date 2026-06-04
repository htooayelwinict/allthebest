# Worker Runtime Contract Design Research

## Question

Are the current worker-runtime contracts production-grade enough for safe
multi-instance workers, and is the kernel-emitted replan artifact sufficient for
planner recovery?

## Summary

The architecture is directionally strong: planner-visible worker groups,
kernel-owned retries/budgets/replan, scoped permissions, runtime matrix logging,
and completed/partial/failed artifact separation are the right foundations.

It is not production-grade yet. The weak points are mostly at the worker
contract boundary, not the decompressor or planner:

- Tool calls are still simulated through JSON-in-text decisions instead of a
  native provider tool-call protocol with strict schemas.
- Worker expected-output artifacts are validated only by artifact id presence,
  not by per-artifact typed content shape.
- Mutation scope is deterministic for simple file writes, but underspecified for
  filesystem operations such as moves, deletes, manifests, and directory-level
  scaffolding.
- Replan payload shape is broad and mostly correct, but failed workers often
  emit too little recovery-grade evidence, so the planner can only make generic
  replacement plans.

## Probe Evidence

### File workspace cleanup

File:
`plan/live-worker-qwen-qwen3-7-max-20260604-113232.json`

Result:
`failed`, no replan, two local retries.

Observed issue:
`filesystem_worker` attempted `move_file` for
`artifacts/tmp/error_dump.json`, but kernel denied it because the resolved write
scope did not include the move source path.

Interpretation:
This is a mutation/filesystem contract gap. The `mutation_scope` artifact
contained `moves` and `creations`, but the strict write scope only resolved
`target_paths`-style paths. For move tools, both source and destination must be
explicitly represented in the write authorization model.

### Payment retry with kat-coder

File:
`plan/live-worker-kwaipilot-kat-coder-pro-v2-20260604-114059.json`

Result:
`failed`, no replan, two local retries.

Observed issue:
The plan and `mutation_scope` were good. The code worker had approved target
paths, but the model/tool-call adapter produced malformed or empty
`read_many_files` tool calls. Later, the parsed tool name became a corrupted
string containing parts of the arguments.

Interpretation:
This is not planner failure. It is a tool-call protocol robustness failure. The
runtime needs either native provider tool calls or a strict repair/reject loop
for malformed tool-call envelopes before counting the worker instance attempt as
real work.

### Greenfield calculator with kat-coder

File:
`plan/live-worker-kwaipilot-kat-coder-pro-v2-20260604-114317.json`

Result:
`needs_replan`.

Observed issue:
The first ANALYZE step missed expected artifacts and triggered internal replan.
The replacement plan repaired ANALYZE, but DESIGN then missed
`mutation_scope`, `rollback_plan`, `verification_plan`, and `change_design`.
Replan was deferred because the depth cap had already been used.

Interpretation:
The replan path works, but worker output generation is not reliable enough.
For no-tool `plan_only` workers, a single model call has no repair loop or
schema-specific artifact shaping.

## Local Contract Findings

### Strong points

- `PermissionSet` has explicit read/write/command/web gates and normalizes
  missing booleans to false.
- `TaskCompiler` passes scoped plan and envelope context into task metadata
  without full conversation history.
- `TaskCompiler` rejects missing input artifacts instead of silently dropping
  them.
- Kernel separates completed, partial, and failed-step artifacts.
- Kernel keeps replan internal and emits full replacement-plan requests.
- Runtime matrix logs stages, attempts, worker instances, model calls, tool
  calls, retries, and replan events.
- Verification fallback can synthesize verification artifacts from command
  observations when model finalization fails.

### Weak points

- Tool specs are descriptive dictionaries, not strict JSON Schema with required
  properties and no additional properties.
- Model output uses a custom JSON decision envelope, so third-party models can
  return malformed tool calls that pass through normalization poorly.
- Tool-call repair is not distinct from worker retry; malformed call syntax is
  treated like a failed worker attempt.
- Expected artifacts are only checked by id. Their content is not typed or
  phase-specific.
- `MutationScope` only has `target_paths`, `test_paths`, `forbidden_paths`, and
  globs. It lacks structured operations for `move`, `delete`, `create`,
  directory scopes, manifest scopes, and preimage requirements.
- Replan requests include good containers, but failure observations can be too
  generic when the failed step produced no artifacts.
- Worker templates are single-instance for most groups. They describe roles but
  do not yet encode per-instance output schemas or tool-call strategy.
- Retry behavior increases budgets, but does not always change the worker's
  operating strategy enough to avoid repeated identical failure.

## External Research

Official OpenAI function-calling guidance says JSON mode only guarantees valid
JSON, not schema conformance. Structured Outputs or schema validation plus
retries are needed when exact shape matters.

Source:
https://help.openai.com/en/articles/8555517-function-calling-in-the-

Anthropic's tool-use docs describe tool use as a contract: the application
defines available operations and input/output shapes, the model emits structured
calls, the app executes them, and returns results. They explicitly call out that
regex-parsing model text to recover structured intent is a smell; the structure
belongs in the tool schema.

Source:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works

Anthropic's strict tool-use docs recommend strict JSON Schema conformance for
agentic workflows and complex tools. Without strict mode, malformed or missing
tool parameters become runtime errors.

Source:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use

Anthropic's tool-definition guidance emphasizes detailed tool descriptions,
input examples for complex/nested tools, fewer more capable tools to reduce
selection ambiguity, namespacing, and high-signal tool outputs.

Source:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools

Anthropic's parallel-tool docs say same-turn tool calls should be treated as
unordered and independent; dependent tool calls should happen across turns.
This supports our current sequential action loop for dependent file operations,
but suggests we can use independent batch tools for read-heavy discovery.

Source:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use

OpenAI Agents SDK docs distinguish model-side parallel tool emission from
runtime-side local tool concurrency, and name malformed JSON/tool behavior as a
model behavior error class. That maps well to our need for a tool-call repair
stage before consuming a worker retry.

Source:
https://openai.github.io/openai-agents-python/running_agents/

OpenAI's agent design guide says production agents need model, tools, and
instructions under clear guardrails, with tool safeguards based on read/write
risk and escalation after failure thresholds.

Source:
https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/

LangGraph persistence docs reinforce that durable execution needs checkpointed
state for fault tolerance, human review, and resume. Our decision to keep worker
replan internal is fine, but the event log/replan payload must be durable enough
to resume or inspect.

Source:
https://docs.langchain.com/oss/python/langgraph/persistence

## Replan Payload Assessment

Current `ReplanRequest` fields are broadly correct:

- request/run/plan identity
- failed step id and failed step payload
- reason
- worker result
- completed artifacts
- carryover artifacts
- completed step ids
- remaining budget
- recommended action
- issues
- partial artifacts
- failed-step artifacts
- failure observation

The greenfield run proves the payload can repair a failed ANALYZE step: carryover
artifacts let the replacement plan start from completed DISCOVER outputs.

However, the payload is not enough by itself when the failed step produces no
expected artifacts and no structured diagnostic artifact. The deferred DESIGN
replan had good completed context but only a generic missing-artifacts reason.
For recovery, planner needs:

- failure class: output_contract_miss, malformed_tool_call, scope_contract_gap,
  evidence_gap, dependency_gap, verification_gap
- expected vs produced artifact table with id and required shape
- worker prompt/input summary or task contract hash
- allowed tools and denied attempted tools
- last valid model decision shape, if safe to include
- per-attempt deltas showing what changed between retries
- suggested recovery strategy from kernel, not just model summary

## Production-Grade Recommendation

The current runtime is a strong prototype/control-plane foundation, but not yet
production-grade worker execution.

Priority fixes before expanding worker complexity:

1. Native strict tool protocol

Use provider-native tool calls when available. For non-native OpenRouter models,
add a strict adapter layer that validates `WorkerLLMDecision` and repairs or
rejects malformed tool calls before executing tools. Tool-call syntax failures
should be `model_behavior_error` or `tool_call_contract_error`, not a normal
worker plan failure.

2. Typed artifact contracts

Create per-artifact schemas for important outputs:
`mutation_scope`, `rollback_plan`, `verification_plan`, `change_design`,
`change_summary`, `patch_diff`, `rollback_patch`, `verification_results`,
`test_results`, and `final_summary`.

3. Richer mutation operation contract

Extend `MutationScope` into an operation-aware scope:
`target_paths`, `create_paths`, `update_paths`, `delete_paths`, `move_pairs`,
`directory_paths`, `manifest_paths`, `test_paths`, `forbidden_paths`,
`max_files`, and `preimage_required`.

For `move_file`, authorize both source and destination explicitly. For
greenfield scaffolds, authorize file list creation. For directory-level work,
support bounded directory scopes with max file count.

4. Worker output repair loop

Before `needs_replan`, give the same worker instance one constrained finalization
repair turn when artifacts are missing but the task is otherwise valid. The
repair prompt should include only expected artifact ids, required schemas, and
the worker's previous output.

5. Better replan diagnostics

Keep the current schema but enrich `failure_observation` and issue metadata with
expected/produced artifact diff, output schema names, attempted tool calls,
denied tools, and kernel classification.

6. Stage-specific worker groups

Keep planner-visible worker groups, but split internal instances by purpose:
repo discovery, focused reader, scope designer, mutation operator,
verification runner, finalizer. Each instance should have its own allowed tools
and required output schema.

7. Deterministic kernel-owned artifacts

Kernel should own `patch_diff`, changed paths, rollback/preimage snapshots, scope
audit, and command evidence. Workers can summarize and interpret, but not be the
only source of these operational artifacts.

8. Retry strategy changes

Retries should adjust more than budgets. For repeated malformed tool calls, force
single-tool mode or native tool format. For repeated missing artifacts, force
final_result-only repair. For repeated scope denial, stop early as a scope
contract issue rather than spending all retries.

## Bottom Line

Keep the architecture. Do not combine everything back into one runtime. The
kernel/planner/replan separation is proving useful.

But before calling workers production-grade, harden the worker contract boundary:
strict tool calls, typed artifacts, operation-aware mutation scope, repair-before-
replan, and richer replan failure diagnostics.

## Implementation Pass 2026-06-04

Completed a focused hardening slice:

- Mutation scope now keeps explicit create/update/delete/manifest/directory paths
  and move pairs while preserving `target_paths` compatibility.
- Move scopes authorize both source and destination paths.
- Malformed embedded tool-call strings are normalized when possible before tool
  execution.
- Missing expected artifacts from a worker group are now classified as a
  retryable worker output-contract miss instead of immediate planner replan.
- Local retry instructions now change strategy after output-contract misses or
  malformed tool-call envelopes.
- Replan failure observations now include expected, produced, and missing
  artifact details plus compact worker-result diagnostics.

Verification:

- `uv run pytest tests/test_worker_agentic.py tests/test_worker_kernel.py -q`
  passed with 90 tests.
- `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_graph.py tests/test_worker_kernel.py tests/test_worker_agentic.py -q`
  passed with 175 tests.
