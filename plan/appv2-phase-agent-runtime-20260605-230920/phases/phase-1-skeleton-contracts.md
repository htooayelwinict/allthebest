# Phase 1 - Skeleton And Contracts

## Goal

Create the `appV2/` package skeleton, shared schemas, unified validator, env/model-client stubs, and first tests.

## Files

- `appV2/__init__.py`
- `appV2/schemas.py`
- `appV2/validator.py`
- `appV2/model_client.py`
- `appV2/env_config.py`
- `tests/test_appv2_validator.py`

## Tasks

- Add AppV2 schema types:
  - `Envelope`
  - `ExactLiteral`
  - `PhaseName`
  - `PhasePlan`
  - `PhaseStep`
  - `ArtifactContract`
  - `ArtifactRecord`
  - `ToolCallProposal`
  - `MutationProposal`
  - `WorkerDecision`
  - `ValidationIssue`
  - `RuntimeResult`
- Add `AppV2Validator`.
- Validate phase order, artifact dependencies, mutation requirements, verification requirements, and final-output contracts.
- Add env config names, but keep runtime disabled by default.
- Use existing OpenRouter SDK wrapper shape, but keep AppV2 client code locally owned.

## Tests

```bash
uv run pytest tests/test_appv2_validator.py -q
```

## Done When

- `PhasePlan` can be constructed without worker types.
- Validator catches missing phase artifact producers.
- Validator catches mutation phase without mutation policy.
- Validator catches verify phase without verification policy.
- V1 tests remain unaffected.

## Status

Completed 2026-06-05.
