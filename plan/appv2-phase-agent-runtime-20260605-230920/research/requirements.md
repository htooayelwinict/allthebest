# Requirements

## User Goal

Build a clean `appV2/` implementation path for three runtimes:

1. Prompt decomposition runtime, renamed `decomposer`.
2. Phase planner runtime.
3. Single-loop worker runtime for file and code management.

The plan must be saved before implementation. No code should be changed in this planning turn.

## Functional Requirements

- Create a new `appV2/` directory during implementation.
- Port/copy the current decompressor runtime idea into an `appV2.decomposer` runtime.
- Improve decomposer prompt chaining using gated, validated prompt-chain stages.
- Decomposer must output an envelope for the planner.
- Create a planner runtime similar in responsibility to V1, but not similar in worker-type output.
- Planner must output a phase plan, not a worker handoff plan.
- Preserve seven-stage planning logic:
  - `DISCOVER`
  - `ANALYZE`
  - `RESEARCH`
  - `DESIGN`
  - `MUTATE`
  - `VERIFY`
  - `FINALIZE`
- Phase plan must describe phase goals, artifact contracts, gates, and budgets.
- Artifact handoff must be phase-level, not worker-type-level.
- Planner prompt chain must have validation and repair/replan behavior when validation fails.
- One validator must validate all runtime contracts and artifacts.
- Worker runtime must have one agent loop with tools.
- Worker runtime must own:
  - state controller
  - artifact ledger
  - mutation ledger
  - policy gate
  - verification gate
  - budgets
  - compact retry memory
  - final result reconciliation
- Worker scope for the first version is file and code management.
- LLM output must be structured and validated.
- LLM may propose actions, but runtime disposes them.
- LLM must not mutate files directly.
- Context must not bloat exponentially across turns.

## Non-Goals

- Do not delete or rewrite V1 in this pass.
- Do not build a multi-worker or multi-instance worker runtime in `appV2`.
- Do not make planner choose worker types.
- Do not pass full conversation history through every runtime.
- Do not trust model-authored verification without tool evidence.
- Do not let mutation scope become a hard pre-dispatch bottleneck again.
- Do not add raw shell access to workers.

## Acceptance Tests

- Fake LLM tests prove decomposer emits valid envelopes.
- Fake LLM tests prove planner emits valid `PhasePlan` objects.
- Validator rejects:
  - phase order regressions
  - missing artifact producers
  - mutation phases without mutation policy
  - verify phases without verification policy
  - final results without required artifacts
- Worker loop tests prove:
  - model action proposals are schema-validated
  - denied tool/mutation requests become observations
  - artifact ledger stores every evidence record
  - mutation ledger records preimage, operation, result, and diff
  - verification gate decides pass/fail from evidence
  - planner replan is only used for planner-quality failures
- Live probe can run without changing the V1 probe until `appV2` has its own script.

## Definition Of Done For Initial AppV2

- `appV2` has its own schemas, validators, runtimes, tests, and env wiring.
- V1 tests continue to pass.
- AppV2 tests cover fake model success and repair paths.
- One live file-management probe can run end to end through AppV2 after implementation.
