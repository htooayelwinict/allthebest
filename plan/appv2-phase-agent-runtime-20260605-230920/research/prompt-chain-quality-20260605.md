# AppV2 Prompt Chain Quality Research

## Question

How should AppV2 prompt contracts be shaped so the decomposer and planner chains are production-grade, and the worker loop reliably consumes runtime feedback without bloating context?

## Summary

The current AppV2 architecture is directionally right: prompt chaining with deterministic gates for decomposer/planner, and one worker loop where the LLM proposes while the runtime disposes. The missing piece was prompt specificity. Generic role prompts made every chain stage feel similar, which increases schema drift, over-planning, weak artifact contracts, and repeated worker errors.

The fix is to make prompts stage-specific contracts:

- decomposer stages must preserve exact literals and avoid planner/worker leakage;
- planner stages must separate phase skeleton, artifact contracts, full plan assembly, repair, and replan;
- worker turns must define the exact WorkerDecision union, feedback repair behavior, proof requirements, and planner replan boundaries.

## Key Findings

- OpenAI structured-output guidance says JSON mode only guarantees valid JSON, not schema correctness; schema matching still needs structured outputs or validation/retry. AppV2 already validates, so prompts should repeatedly state "JSON only" and name schema-level repair rules.
- OpenAI reasoning-model guidance favors straightforward prompts with clear delimiters, specific constraints, and no chain-of-thought prompting. AppV2 now uses JSON payload sections and concise evidence fields instead of asking for hidden reasoning.
- OpenAI Agents SDK guidance describes the agent loop as model call -> tool call -> tool result feedback -> next model call, with max-turn control and output guardrails. AppV2's worker prompt now mirrors that loop and treats gate/tool failures as observations.
- Anthropic's agent guidance recommends simple workflows first, prompt chaining for well-defined subtasks, ground-truth tool feedback at each step, and explicit stopping conditions. AppV2 keeps decomposer/planner as gated prompt chains and the worker as one bounded loop.
- Anthropic's tool-design guidance says agent tools need clear boundaries, high-signal responses, token efficiency, and evaluation against realistic tasks. The worker prompt now tells the model to prefer targeted tools, consume repair hints, avoid repeated failed calls, and finish only with proof-backed artifacts.

## Recommendation Applied

1. Centralize production prompt contracts in `appV2/prompts.py`.
2. Add per-stage decomposer contracts for extraction, file/code enrichment, and repair.
3. Add per-stage planner contracts for phase skeleton, artifact contracts, full phase plan, repair, and planner-quality replan.
4. Add worker decision protocol, turn algorithm, feedback protocol, artifact quality bar, and budget policy.
5. Pass compact schema summaries and prompt contracts into actual runtime prompt payloads.
6. Keep deterministic validators and gates as the source of truth; prompts reduce errors but do not replace validation.

## References

- OpenAI Help: Function Calling in the OpenAI API
  https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api
- OpenAI API docs: Function calling
  https://developers.openai.com/api/docs/guides/function-calling
- OpenAI API docs: Reasoning best practices
  https://developers.openai.com/api/docs/guides/reasoning-best-practices
- OpenAI Help: Best practices for prompt engineering
  https://help.openai.com/en/articles/6654000-best-practices-for-prompting
- OpenAI Agents SDK docs via Context7: agent loop, max turns, guardrails, tool guardrails
  `/openai/openai-agents-python`
- Anthropic: Building effective agents
  https://www.anthropic.com/engineering/building-effective-agents
- Anthropic: Writing effective tools for AI agents
  https://www.anthropic.com/engineering/writing-tools-for-agents
