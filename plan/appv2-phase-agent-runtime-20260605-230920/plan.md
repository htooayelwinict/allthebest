# AppV2 Phase Agent Runtime Implementation Plan

## Goal

Build a new `appV2/` package that keeps the three-runtime pipeline but replaces worker-type planning and multi-instance worker execution with a phase plan and one production-grade file/code-management agent loop.

## Target Architecture

```text
appV2.graph
  user_input
    -> appV2.decomposer.DecomposerRuntime
       output: Envelope
    -> appV2.planner.PhasePlannerRuntime
       output: PhasePlan
    -> appV2.worker.WorkerRuntime
       output: RuntimeResult
```

The new system should be built beside V1. V1 remains untouched and testable.

## AppV2 Package Shape

Proposed files:

```text
appV2/
  __init__.py
  schemas.py
  validator.py
  model_client.py
  env_config.py
  runtime_matrix.py
  graph.py
  decomposer/
    __init__.py
    contracts.py
    prompt_chain.py
    runtime.py
    redaction.py
    canonicalize.py
  planner/
    __init__.py
    contracts.py
    prompt_chain.py
    runtime.py
  worker/
    __init__.py
    runtime.py
    agent_loop.py
    context.py
    ledgers.py
    tools.py
    policy_gate.py
    verification_gate.py
    result_reconciler.py
```

`appV2.validator.AppV2Validator` is shared by all runtimes.

## Core Contracts

### Envelope

Keep the spirit of V1 `Envelope`, but make it AppV2-owned:

- `request_id`
- `raw_input`
- `normalized_input`
- `user_goal`
- `input_type`
- `intents`
- `domains`
- `risks`
- `artifacts`
- `context_needed`
- `constraints`
- `complexity_hint`
- `confidence`
- `ambiguity`
- `assumptions`
- `literal_contract`
- `metadata`

Compatibility note: keep field names close to V1 so planner tests and live output remain easy to compare.

### PhasePlan

Replace V1 `Plan`/`PlanStep.worker_type` with phase-level contracts:

```text
PhasePlan
  plan_id
  request_id
  objective
  strategy
  phases: list[PhaseStep]
  budgets
  global_invariants
  success_criteria
  artifact_contracts
  metadata
```

```text
PhaseStep
  phase_id
  phase: DISCOVER | ANALYZE | RESEARCH | DESIGN | MUTATE | VERIFY | FINALIZE
  goal
  instructions
  input_artifacts
  output_artifacts
  allowed_tool_groups
  policy
  mutation_policy
  verification_policy
  acceptance_checks
  max_tool_calls
  max_model_calls
```

No `worker_type`. No worker handoff fields.

### Artifact

Use one artifact type across all runtimes:

```text
ArtifactRecord
  id
  kind
  content
  producer
  phase_id
  trust_level
  lifecycle
  metadata
```

The artifact ledger is append-only. "Completed", "partial", "failed", "derived", and "observation" are filtered views, not separate stores.

### Worker Decision

LLM output must be a strict union:

```text
WorkerDecision
  tool_calls?: list[ToolCallProposal]
  mutation?: MutationProposal
  final_phase_output?: PhaseOutputProposal
  planner_replan_signal?: PlannerReplanSignal
```

Exactly one branch is allowed per turn.

### Tool And Mutation Proposals

The LLM proposes. Runtime disposes.

```text
ToolCallProposal
  call_id
  tool_name
  arguments
  purpose
```

```text
MutationProposal
  operation_batch_id
  operations
  reason
  expected_artifacts
```

The runtime validates every proposal before execution.

## Decomposer Runtime

### Design

Port the current decompressor runtime into `appV2.decomposer`, renamed `DecomposerRuntime`.

Use a gated prompt-chain:

1. `decompose_request`
   - One structured call that produces an `EnvelopeDraft`.
   - It must not plan execution.
2. `extract_contracts`
   - Deterministic literal extraction first.
   - LLM enrichment only for complex file/code tasks or when the draft misses concrete artifacts.
3. `validate_envelope`
   - `AppV2Validator.validate_envelope`.
4. `repair_envelope`
   - One repair call when validation fails.

This follows prompt chaining with programmatic gates. It keeps latency low for simple prompts while improving hard file/code prompts.

### Prompt Principles

- State boundary first: describe request only.
- Preserve exact paths, filenames, JSON keys, symbols, and user literals.
- Mark uncertainty instead of inventing repo facts.
- Output only structured schema.
- Keep model-facing context compact.

## Phase Planner Runtime

### Design

Create `PhasePlannerRuntime` that compiles a `PhasePlan` from an `Envelope`.

Prompt-chain stages:

1. `draft_phase_skeleton`
   - Choose ordered phases only.
   - No worker types.
2. `draft_artifact_contracts`
   - Define phase inputs/outputs and artifact shapes.
   - Identify which artifacts are evidence, mutation policy, verification proof, or final report.
3. `draft_phase_plan`
   - Assemble full `PhasePlan`.
4. `validate_phase_plan`
   - Deterministic validation.
5. `repair_phase_plan`
   - At most one repair call for draft invalidity.
6. `planner_replan`
   - Used only when worker runtime reports planner-quality semantic failure.

### Seven-Phase Logic

Use the existing phase vocabulary, but make phases the plan identity:

- `DISCOVER`: inspect repo/files/environment; produce inventory and candidate evidence.
- `ANALYZE`: identify root cause, file-management rules, dependencies, constraints.
- `RESEARCH`: optional external or internal research; no web unless policy allows.
- `DESIGN`: produce operation design, mutation policy, rollback/verification design.
- `MUTATE`: perform gated file/code operations.
- `VERIFY`: run deterministic verification gates and command/file-state checks.
- `FINALIZE`: produce final user-facing result from ledger evidence.

The planner can omit phases only when they are truly not needed. `MUTATE` must be followed by `VERIFY`.

## Worker Runtime

### Design

Create one worker runtime, not worker types:

```text
WorkerRuntime
  owns:
    - PhaseCursor
    - AgentLoop
    - ArtifactLedger
    - MutationLedger
    - ToolRegistry
    - PolicyGate
    - VerificationGate
    - BudgetGate
    - ContextController
    - ResultReconciler
```

### Agent Loop

```text
for phase in phase_plan.phases:
  build compact PhaseFrame
  while phase not complete:
    call model with:
      - objective
      - current phase goal
      - pending acceptance checks
      - compact artifact ledger view
      - mutation ledger summary
      - verification ledger summary
      - available tools
      - last observations
    parse WorkerDecision
    validate decision
    if tool_calls:
      run through ToolRegistry and PolicyGate
      append ToolObservation artifacts
    if mutation:
      run through PolicyGate
      apply using controlled file tools only
      append MutationRecord artifacts
    if final_phase_output:
      validate against phase output contracts
      promote phase artifacts
      break
    if planner_replan_signal:
      classify as planner-quality or reject locally
```

### Context Control

Every model turn gets a compact view:

- current objective
- current phase only
- needed artifact summaries
- exact unresolved obligations
- available tool schemas
- last N observations
- ledger counters and relevant refs

Do not pass full raw history or entire artifact content unless the phase requires it.

### Tool Scope

First AppV2 worker objective is file/code management. Tool groups:

- `repo_read`
  - `repo_snapshot`
  - `list_dir`
  - `read_file`
  - `read_many_files`
  - `file_search`
  - `text_search`
  - `json_query`
  - `git_status`
  - `git_diff`
- `file_write`
  - `write_file`
  - `write_many_files`
  - `replace_in_file`
  - `apply_file_operations`
  - `move_file`
  - `delete_file`
  - `write_json_manifest`
- `verify`
  - `run_readonly_command`
  - `run_project_tests`
  - `run_focused_tests`
  - `verify_file_state`
  - `scope_audit`
- `research_read`
  - optional later; not required for first file/code version

No raw shell. `run_readonly_command` must stay allowlisted.

### Policy Gate

PolicyGate validates:

- root containment
- allowed tool group
- allowed write paths or advisory policy
- forbidden paths/globs
- batch size
- step blast radius
- destructive operation rules
- generated file expectations

Repairable denials become observations. Non-repairable denials become blocked runtime results.

### Mutation Ledger

Each mutation records:

- operation batch id
- proposed operations
- policy decision
- preimage snapshot
- applied operations
- touched paths
- diff
- rollback data
- verification requirements created by mutation

Patch and rollback artifacts should be derived from this ledger when possible.

### Verification Gate

VerificationGate validates:

- commands actually ran when required
- file-state checks actually ran when required
- manifest/report schema matches expected keys
- mutation stayed inside policy
- final phase claims cite ledger evidence

Model-authored "passed" does not count without evidence.

## Replan Policy

Replan remains internal:

```text
WorkerRuntime -> PhasePlannerRuntime.replan(...)
```

Only planner-quality issues can request replan:

- phase ordering impossible
- missing input artifact that no prior phase can produce
- repo/user intent drift
- mutation policy contradicts phase goal
- required evidence/source cannot exist
- verification proves plan assumption false

Runtime/tool/model/budget failures do not replan:

- malformed model output
- tool denial
- command failure caused by implementation
- model budget pressure
- invalid final artifact
- missing report content

Those are handled by local repair, retry memory, or terminal failure.

## Unified Validator

Create `AppV2Validator` with methods:

- `validate_envelope`
- `validate_phase_plan`
- `validate_phase_step`
- `validate_artifact_contracts`
- `validate_artifact_record`
- `validate_worker_decision`
- `validate_tool_call_proposal`
- `validate_mutation_proposal`
- `validate_policy_gate_result`
- `validate_verification_evidence`
- `validate_final_result`

The validator should emit structured `ValidationIssue` objects:

- `owner`: `decomposer | planner | worker | policy_gate | verification_gate | kernel`
- `severity`: `warning | repairable | blocking`
- `code`
- `message`
- `path`
- `metadata`

The validator is deterministic. It should not call LLMs.

## Files Likely To Change

Plan-only turn changes:

- `plan/appv2-phase-agent-runtime-20260605-230920/**`

Implementation turn likely changes:

- `appV2/**`
- `tests/test_appv2_*.py`
- `.env.example`
- `README.md` or `docs/appv2-runtime.md`
- optionally `scripts/live_appv2_runtime_probe.py`

Avoid modifying V1 runtime code unless a shared utility is intentionally extracted after tests prove no regression.

## Risks

- AppV2 can become another parallel runtime trap if it imports too much V1 worker logic.
- Phase plans may be too abstract if artifact contracts are weak.
- A single worker loop can still bloat context unless `ContextController` is strict.
- Too many tools can confuse the model; start with a small file/code tool set.
- Planner repair loops can become expensive if validation is too broad or vague.
- Unified validator can become oversized; keep it deterministic and composed internally.

## Rollback

Since AppV2 is additive, rollback is simple:

- delete or ignore `appV2/`
- remove AppV2 tests/scripts/docs
- V1 remains the default runtime

No V1 behavior should depend on AppV2 during the initial build.

## Verification Commands

Narrow first:

```bash
uv run pytest tests/test_appv2_decomposer.py -q
uv run pytest tests/test_appv2_phase_planner.py tests/test_appv2_validator.py -q
uv run pytest tests/test_appv2_worker_loop.py -q
uv run pytest tests/test_appv2_graph.py -q
```

Regression:

```bash
uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_agentic.py tests/test_worker_control.py tests/test_worker_kernel.py tests/test_graph.py -q
uv run pytest -q
```

Live AppV2 probe after implementation:

```bash
uv run python scripts/live_appv2_runtime_probe.py --scenario file_workspace_cleanup --worker-model <model> --matrix-poll-interval 5 --out-dir plan
uv run python scripts/live_appv2_runtime_probe.py --scenario greenfield_calculator_api --worker-model <model> --matrix-poll-interval 5 --out-dir plan
```

## Implementation Phases

1. Skeleton and contracts.
2. Decomposer runtime.
3. Phase planner runtime.
4. Worker ledgers, tools, and gates.
5. One worker agent loop.
6. Graph, tests, probes, and documentation.

## Recommended First Implementation Step

Implement `appV2/schemas.py` and `appV2/validator.py` first, with fake-object tests. Do not port the decomposer before `Envelope`, `PhasePlan`, `ArtifactRecord`, `WorkerDecision`, `MutationProposal`, and `ValidationIssue` are locked.
