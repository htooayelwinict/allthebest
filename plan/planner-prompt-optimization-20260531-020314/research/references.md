# References

## Local References

- `app/planner/prompt_chain.py`
- `tests/test_planner.py`
- `app/planner/validator.py`
- `app/planner/contracts.py`
- `plan/live-complexity-qa-current-model-20260531-004706.json`
- `plan/planner-instruction-context-blocks-20260531-015354/plan.md`
- `plan/planner-instruction-context-blocks-20260531-015354/research/live-two-prompt-qa-20260531.json`

## Second-Pass Advisory Summary

Open Bridge advised a conservative refactor that deduplicates prompt policy through Python-level shared blocks/templates rather than changing the semantic prompt policy. The useful recommendation kept for implementation is: preserve anchors and wording for high-risk rules while reusing shared direct-support and instruction-context-block structures across draft and repair prompts.
