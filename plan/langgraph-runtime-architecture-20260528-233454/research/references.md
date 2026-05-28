# References

## Internal references

- `AGENTS.md` — project workflow and durable planning requirements.
- `pyproject.toml` — current Python project metadata.
- `uv.lock` — current lockfile state.

## External references

External documentation was later consulted for stack requirements and API confirmation in [`stack-requirements-docs.md`](./stack-requirements-docs.md). The prompt includes sufficient architecture detail, and the external references confirm the planned APIs rather than changing the architecture.

Implementation may need to consult official LangGraph documentation if the installed package API differs from the prompt's `StateGraph`/`END` example. If doing so, load the `mcp-context7` skill before relying on external library docs.

Confirmed documentation areas:

- LangGraph `StateGraph`, explicit `add_node(...)`, `add_edge(...)`, `END`, `compile()`, and `invoke(...)`.
- Pydantic v2 `BaseModel`, `model_validate(...)`, and `model_dump()`.
- uv `[project].dependencies`, `[dependency-groups].dev`, `uv sync`, `uv lock`, and `[tool.uv] package = false`.

## Design brainstorms

- [`brainstorm-decompressor-prompt-chaining.md`](./brainstorm-decompressor-prompt-chaining.md) — evaluates whether to evolve `DecompressorRuntime` from deterministic heuristics toward LLM prompt chaining. Recommendation: keep deterministic decompression as Phase 1 baseline/fallback, add structured LLM enrichment first, and reserve heavy prompt chaining for ambiguous, multi-domain, low-confidence, or high-risk inputs.
- [`suggest-prompt-chain-decompressor.md`](./suggest-prompt-chain-decompressor.md) — synthesizes `plan/suggest.md` into reusable research notes. Recommendation: treat the suggested prompt-chain decompressor as a future hybrid enhancement that preserves the current top-level graph and decompressor/planner/kernel boundaries.
- [`brainstorm-option-1-deterministic-decompressor.md`](./brainstorm-option-1-deterministic-decompressor.md) — evaluates the near-term default path. Recommendation: keep the deterministic decompressor as the stable baseline and always-available fallback; add LLM enrichment only later behind shadow mode or a feature flag if semantic quality becomes a measurable bottleneck.
