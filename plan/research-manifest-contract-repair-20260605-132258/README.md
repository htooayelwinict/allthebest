# Manifest Contract Repair Research

## Question

How should the worker runtime prevent exact user JSON/report schemas from drifting into generic runtime artifact names during file-management mutations?

## Sources Checked

- Context7: `/openai/openai-agents-python`, focused on typed tools, guardrails, sessions, and tracing.
- Context7: `/websites/platform_claude_en_api`, focused on Claude tool-use loops and strict tool schemas.
- Web: OpenAI Structured Outputs, OpenAI Agents SDK guardrails/running agents, Anthropic tool-use docs.
- Open Bridge second-pass review of the proposed implementation.

## Findings

- Agent tools should be treated as typed contracts. The model proposes structured tool calls; runtime code executes and validates them.
- Tool-level guardrails are the right place for file/write validation and manifest write validation.
- Exact final JSON/report shape should come from the user/request literal contract, not from a generic artifact name.
- Tool results should be high-signal and compact. Returning generic aliases like `moved_json_artifacts` when the user asked for `moved_evidence` creates downstream schema drift.
- Long-running retries should receive contract-specific feedback. Retrying against an old generic artifact contract wastes budget after the filesystem work is already mostly correct.

## Recommendation Chosen

Implement a narrow dynamic contract layer:

- Keep legacy artifact contracts as fallback.
- When `Task.metadata.required_json_keys` exists, make `moved_items_record`, `manifest_file`, and `manifest_update_record` validate against those exact keys.
- Make `write_json_manifest` infer `total_key` from exact keys such as `total_moved`.
- Make `write_json_manifest` count moved-item arrays and exclude held/skipped/ignored/preserved/excluded arrays from totals by default.
- Prompt code/filesystem/research workers to pass `required_keys`, `total_key`, and moved-only `count_keys` when writing manifests.

## Quality Gate

This is better than adding more planner prompt text because it fixes the runtime boundary:

- deterministic,
- backward-compatible,
- testable without live LLM calls,
- directly addresses the live probe failure,
- keeps planner/decompressor schemas stable.

## References

- https://openai.github.io/openai-agents-python/guardrails/
- https://openai.github.io/openai-agents-python/running_agents/
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context
