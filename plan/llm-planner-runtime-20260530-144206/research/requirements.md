# Requirements Research: LLM Planner Runtime

## Question

How should the planner consume the decompressor envelope and emit a safe, foolproof-enough LLM-generated plan for the worker runtime?

## User Intent

- Do not implement yet.
- Research and create a durable implementation plan.
- Focus on the decompressor envelope and planner runtime.
- Treat the current planner as a skeleton that may be simplified or partially removed.
- Planner should be LLM-powered, not primarily static rule selection.
- Worker runtime may improve later, but the planner design should work with today's worker boundary.

## Current Envelope Example

Complex prompt used for validation:

```text
do we have lighthouse sdk if we do, use it as async function to connect all transation apis and fix lagging issues
```

Recent decompressor envelope:

```json
{
  "normalized_input": "Check if the Lighthouse SDK is available. If so, integrate it using async functions to connect all transaction APIs and resolve performance lag.",
  "user_goal": "Determine Lighthouse SDK availability, integrate it asynchronously with transaction APIs, and fix performance lag.",
  "input_type": "sdk_async_performance_refactor_request",
  "intents": ["sdk.integration", "code.fix", "performance.investigate", "research.lookup"],
  "domains": ["code", "research"],
  "risks": ["performance_cause_unknown", "ambiguous_scope", "needs_verification", "mutation_requested"],
  "artifacts": [
    {"name": "Lighthouse SDK", "type": "sdk"},
    {"name": "transaction APIs", "type": "api"},
    {"name": "async function", "type": "code_pattern"}
  ],
  "context_needed": ["dependency_manifest", "repo_tree", "performance_evidence", "target_file"],
  "constraints": [
    "target_locations_must_be_identified_before_mutation",
    "performance_claims_require_evidence",
    "mutation_requires_verification"
  ],
  "complexity_hint": "high",
  "confidence": 0.6,
  "ambiguity": [
    "Identity and package name of 'lighthouse sdk' is unspecified.",
    "Specific transaction APIs to connect are not listed.",
    "Nature and root cause of 'lagging issues' are undefined.",
    "Target files or modules for the async refactor are unknown."
  ],
  "assumptions": [
    "User refers to a specific internal or third-party SDK named Lighthouse.",
    "Transaction APIs currently exist in the codebase and are experiencing latency.",
    "Async execution is a viable pattern for the target language and framework."
  ]
}
```

## Required Planner Behavior

The planner must convert semantic envelope fields into a worker-safe plan:

- Use `normalized_input` and `user_goal` as objective inputs.
- Use `intents` and `domains` to choose worker types and sequencing.
- Use `risks`, `context_needed`, `constraints`, and `ambiguity` as hard planning constraints.
- Use `artifacts` as search seeds and target-discovery hints, never as proven file paths unless typed as explicit paths.
- Use `complexity_hint` to scale budget and number of steps.
- Use `confidence` to decide whether mutation is allowed immediately or must be observe-only.
- Preserve `assumptions` as metadata or instruction caveats, not facts.
- Treat decompressor `metadata` as diagnostics rather than plan semantics.

## Non-Goals

- Do not change graph topology.
- Do not implement planner code in this planning pass.
- Do not make worker runtime smarter yet.
- Do not make the decompressor emit planner fields.
- Do not add deterministic fallback envelope behavior.

## Acceptance Criteria For Future Implementation

- Planner runtime can call an LLM to emit a `Plan`-shaped JSON object.
- Planner prompt includes the envelope, worker catalog, schema, permission rules, artifact rules, budget rules, and safety policies.
- Planner output is validated before reaching `WorkerKernelRuntime`.
- Invalid planner output can be repaired once through the LLM or rejected safely.
- Plans with mutation include an observation step before write permissions when targets are unknown.
- Plans with mutation include verification after write permissions.
- Plans reference only previously produced input artifacts.
- Plans use only registered worker types.
- Plans have budgets covering all step-level limits.
- Existing deterministic tests can be preserved with fake planner clients.

## Research Gap

No external documentation was required for this plan. Repo-local contracts are sufficient because the architecture is internal and currently schema-driven.
