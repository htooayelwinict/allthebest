# Phase 4: Complex Envelope Coverage

## Status

- ✅ Completed (generic high-complexity multi-intent envelope behavior and safety coverage added in tests).

## Goal

Prove the LLM planner consumes all meaningful envelope fields and emits safe plans for complex mixed-intent requests.

## Scope

- Add fake-client tests using high-complexity multi-intent envelopes.
- Add tests for lower-complexity direct/research/code/ambiguous envelopes.
- Verify planner validation catches bad complex plans.

## Complex Envelope Expected Shape

For high-complexity mutation requests with dependency + evidence requirements, expected plan properties:

- Includes repo discovery before mutation.
- Includes dependency or SDK discovery before mutation.
- Includes performance evidence collection before performance fix claims.
- Includes research step when `research.lookup` is present.
- Includes code patch step only after discovery/research artifacts.
- Includes verification after patch.
- Uses envelope artifacts as search hints without hardcoding any specific product name.
- Includes ambiguity/assumptions as caveats, not facts.

## Additional Scenarios

- Direct question: one `direct_worker` step, no tools, no writes.
- Vague app fix: observe-only or discovery-first, no write permissions.
- File-specific code fix: observe -> patch -> verify.
- Infra error: infra/repo diagnosis before any command-heavy action.
- Research-only request: research worker, no write permissions.

## Tests

- Complex fake LLM plan passes validation.
- Complex fake LLM plan missing research step can still pass if repo discovery covers SDK lookup, but must include dependency discovery before mutation.
- Complex fake LLM plan with write-first fails validation.
- Complex fake LLM plan without verify fails validation.
- Complex fake LLM plan using envelope artifacts as fake file paths fails or is repaired if unsupported by prior discovery.

## Verification

```bash
uv run pytest tests/test_planner.py -q
uv run pytest -q
```

## Exit Criteria

- Generic complex-envelope behavior has explicit regression coverage.
- Planner behavior is tested by safety properties, not brittle exact wording.
