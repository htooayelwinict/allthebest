# References

## Anthropic: Building Effective Agents

Source: https://www.anthropic.com/engineering/building-effective-agents

Relevant guidance:

- Successful production implementations often use simple, composable patterns.
- Workflows are predefined code paths with LLM/tool steps.
- Agents dynamically choose tools based on feedback.
- Prompt chaining is appropriate when tasks decompose cleanly into fixed subtasks.
- Programmatic gates between prompt-chain stages are recommended.
- Tool formats should be easy for the model and should avoid unnecessary formatting overhead.

AppV2 implication:

- Use prompt chaining for decomposer and planner because they are workflow stages.
- Use one agent loop for worker runtime because execution needs dynamic tool feedback.
- Keep model-facing tool/action schemas obvious and compact.

## Anthropic: Writing Effective Tools For Agents

Source: https://www.anthropic.com/engineering/writing-tools-for-agents

Relevant guidance:

- Prototype tools quickly, then evaluate with realistic tasks.
- Strong evals should be grounded in real data and have verifiable outcomes.
- Simple while-loops alternating LLM calls and tool calls are a valid evaluation and agent pattern.
- Tools need clear purpose, high-signal outputs, and good descriptions.

AppV2 implication:

- Worker runtime should start with a small, high-quality tool set for file/code management.
- Tool outputs should be artifact-ready evidence, not raw text dumps.
- File/code probes should grade filesystem state, verification output, artifact quality, and trace cost.

## OpenAI Agents SDK: Agents Overview

Source: https://developers.openai.com/api/docs/guides/agents

Relevant guidance:

- Agent apps plan, call tools, collaborate when needed, and keep enough state for multi-step work.
- The SDK path fits when the application owns orchestration, tool execution, approvals, and state.

AppV2 implication:

- AppV2 should keep orchestration, state, approval, and tool execution in local runtime code.
- The model should be a proposer, not the owner of state transitions.

## OpenAI Agents SDK: Running Agents

Source: https://openai.github.io/openai-agents-python/running_agents/

Relevant guidance:

- The core loop is:
  - call LLM
  - if final output, stop
  - if handoff, switch current agent
  - if tool calls, execute tools, append results, and continue
  - stop at max turns
- Run config supports model/session defaults, context shaping, tracing, and tool error behavior.

AppV2 implication:

- AppV2 worker should implement one loop:
  - model decision
  - validate proposal
  - execute tool/mutation/verification gate
  - record observation
  - continue or finalize
- No worker handoffs are needed for the first AppV2 worker.

## OpenAI Agents SDK: Guardrails

Source: https://openai.github.io/openai-agents-python/guardrails/

Relevant guidance:

- Tool guardrails run on every custom function-tool invocation.
- Tool guardrails are the right place for checks around tool calls in multi-step workflows.

AppV2 implication:

- PolicyGate and VerificationGate should be tool/mutation invocation gates, not only final-output checks.
- Rejections should be model-visible observations when repairable.

## OpenAI Agents SDK: Tracing

Source: https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md

Relevant guidance:

- Useful traces include LLM generations, tool calls, handoffs, guardrails, and custom events.

AppV2 implication:

- Runtime matrix should log:
  - phase started/completed
  - model decisions
  - tool proposals
  - policy denials
  - mutations
  - verification checks
  - artifact validations
  - retries
  - replan decisions

## OpenAI Structured Outputs

Source: https://developers.openai.com/api/docs/guides/structured-outputs

Relevant guidance:

- Function calling is for connecting models to tools/functions/data.
- Structured response formats are for final structured answers.
- Structured Outputs are stronger than JSON mode for schema adherence.

AppV2 implication:

- Use tool/function-call style schemas for worker actions where provider support allows.
- Use structured outputs for decomposer envelope, planner phase plan, and worker final phase output.
- Keep JSON-schema fallback for OpenRouter/model variability, but isolate it behind an adapter.

## Design Synthesis

The strongest design is not "planner becomes the worker" and not "workers become many instances." It is:

```text
workflow chain for understanding and planning
  + one stateful agent loop for execution
  + deterministic gates for policy, mutation, and verification
  + evidence-ledger artifacts as the only runtime truth
```
