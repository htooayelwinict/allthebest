# References

## Internal references

- Source research: `plan/research-llm-heavy-promptchain-decompressor-20260529-011000/README.md`
- Existing architecture plan: `plan/langgraph-runtime-architecture-20260528-233454/plan.md`
- Earlier prompt-chain note: `plan/langgraph-runtime-architecture-20260528-233454/research/suggest-prompt-chain-decompressor.md`

## Pydantic APIs already identified by source research

- `BaseModel.model_validate(...)` for validating dictionary-like objects.
- `BaseModel.model_validate_json(...)` for validating JSON strings returned by fake/model clients.
- `BaseModel.model_json_schema()` for providing stage schemas to prompts or structured-output APIs.

## Allowed labels from current code/research

Input types:

- `question`
- `mutation_request`
- `ambiguous_request`
- `request`

Budget hints:

- `low`
- `medium`
- `high`

Planner hints:

- `direct_planner`
- `code_planner`
- `research_planner`
- `infra_planner`
- `fallback_planner`

Observed intents:

- `question.answer`
- `code.fix`
- `fix.ambiguous`
- `observe_first`
- `research.lookup`
- `infra.debug`

Observed domains:

- `general`
- `code`
- `infra`
- `research`

Observed risks:

- `mutation_requested`
- `file_mutation`
- `needs_verification`
- `ambiguous_scope`
- `ambiguous_mutation`
- `observation_context_needed`

Observed execution hints:

- `inspect_target_file_before_patch`
- `verify_after_patch`
- `observe_first_required`
- `do_not_patch_before_observation`
