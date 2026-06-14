# Production Worker Agent Patterns

## Question

What should we borrow from production-grade agent systems to improve our worker
runtime without disturbing the decompressor, planner, or kernel control loop?

## Summary

The current kernel loop is close enough to keep. The weak point is now worker
ergonomics: workers still lose exact file categories, invent artifact shapes, and
sometimes finish without satisfying the executable contract. Production agent
guidance points to clearer tools, stricter output guardrails, realistic evals, and
short feedback loops rather than more nested orchestration.

## Evidence

- Anthropic's agent guidance favors simple, composable agent patterns and warns
  that agent complexity trades latency and cost for better task performance.
- Anthropic's tool guidance says tool quality should be improved through realistic
  evals, with metrics for tool calls, runtime, token use, and tool errors. It also
  recommends realistic tasks that may require many tool calls instead of shallow
  sandbox prompts.
- OpenAI Agents SDK docs describe a direct loop: model call, tool calls, append
  observations, repeat until final output or max turns. They also classify malformed
  model/tool behavior distinctly from normal task failure.
- OpenAI guardrail docs distinguish input, output, and tool guardrails; tool
  guardrails are the right place for per-tool safety and validation in multi-agent
  workflows.

## Probe-Specific Findings

- The file-management probes showed that repo discovery could identify the right
  files, but research/design sometimes remapped `error_dump.json` to itself and
  narrowed the mutation scope incorrectly.
- Filesystem workers still need exact-schema discipline. A manifest key drift from
  `moved_logs` to `moved_build_logs` caused verification repair even though the
  physical file moves were mostly correct.
- Verification is correctly important, but it needs sharper classification:
  implementation failures should repair locally, while strict-scope omissions of
  required paths should be reported as planner/design mismatch.
- Synthesized mutation artifacts are useful as a recovery tool, but they must obey
  the same required fields as model-generated artifacts.

## Recommendation

Keep the current kernel loop and focus this pass on worker-level quality:

1. Add artifact contracts for the artifacts seen in realistic file-management
   flows, especially manifest and move-record outputs.
2. Make worker prompts preserve exact user/test schema names and file categories.
3. Make verification outputs always carry typed pass/fail status.
4. Keep denied tool operations as local repair feedback unless evidence proves a
   semantic planner/design gap.
5. Build future evals around real workspace state, executable checks, artifact
   quality, trace rows, and runtime cost.

## References

- Anthropic, Building Effective Agents: https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, Writing Effective Tools for AI Agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- OpenAI Agents SDK, Running agents: https://openai.github.io/openai-agents-python/running_agents/
- OpenAI Agents SDK, Guardrails: https://openai.github.io/openai-agents-python/guardrails/
