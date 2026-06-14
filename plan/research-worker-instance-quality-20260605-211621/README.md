# Worker Instance Quality Research

## Question

Given the current worker runtime loop should not be changed, what is the highest
leverage way to improve existing worker instances: prompt-level fixes, tool-level
fixes, algorithm-level fixes, or replacement of some worker templates?

## Summary

The current weak point is not the kernel loop. The file-management probe showed
that the control plane can dispatch, mutate, verify, and finalize without retry or
replan churn, but worker outputs can still be semantically wrong. The strongest
fix is tool-level plus worker-algorithm-level hardening: give workers higher-level,
provenance-rich tools and worker-specific output guardrails so final artifacts must
be grounded in tool observations. Prompt edits are still useful, but prompt-only
repair will keep failing on realistic file-management and verification tasks.

## Current Worker Findings

- `repo_worker` is still doing too much semantic classification in free-form LLM
  output. In the recent file-management probe it classified some user-requested
  markdown and CSV files as held/unclear even though the task contract implied
  they were eligible.
- `filesystem_worker` is currently the healthiest worker for this class of task.
  Its prompt and tools steer toward `apply_file_operations` and
  `write_json_manifest`, and the actual mutation path worked.
- `verify_worker` can still fabricate verification evidence. Its prompt tells it
  to run a command, but `verification_results` and `test_results` contracts only
  require `status`, so a model-authored "passed" artifact can satisfy shape checks
  without a matching `run_project_tests` observation.
- The tool surface is decent, but too many tasks still require the model to infer
  classifications, count manifest categories, and prove verification from low-level
  primitives. That is fragile.
- Artifact contracts are stronger than before for manifests, but verification
  contracts are still shallow. They validate fields, not provenance.

## External Guidance

- Anthropic's agent guidance says effective agents need ground truth from tool
  results during execution, and that simple composable workflows should be preferred
  until complexity is justified.
- Anthropic's tool-design guidance says agent tools are contracts between
  deterministic systems and non-deterministic agents. It recommends realistic evals,
  distinct tool purposes, high-signal compact tool results, and tool descriptions
  with examples and edge cases.
- Anthropic specifically notes that teams often spend more time optimizing tools
  than prompts, and that changing tool formats can remove repeated model mistakes.
- OpenAI structured-output guidance distinguishes function calling for connecting
  models to tools from structured response formats for final answers. This supports
  using strict tool schemas plus app-side validation, not trusting final JSON alone.
- OpenAI Agents SDK docs include function tools, guardrails, and tracing. The key
  mapped lesson here is to apply tool/output guardrails at the worker boundary, not
  only generic final artifact validation.

## Recommendation

Focus improvements in this order:

1. Tool-level: add higher-level worker tools that produce proof-ready artifacts.
   Examples: `classify_file_management_candidates`, `validate_file_operation_plan`,
   `verify_file_state_against_manifest`, and `run_required_verification`.

2. Worker-algorithm-level: make certain worker templates deterministic around
   common domains. `repo_worker` should enumerate and classify candidates with a
   tool-first algorithm, then use the LLM only for ambiguous rule interpretation.
   `verify_worker` should derive `test_results` and `verification_results` from
   command/file-state observations, not author them freely.

3. Worker-specific output guardrails: before accepting final artifacts from
   `verify_worker`, reject any passing verification artifact that lacks matching
   command or file-state observation IDs. Before accepting file-management
   artifacts, reject candidate manifests that omit required prompt/test categories
   when the repo snapshot contains matching files.

4. Prompt-level: keep prompts concise but sharpen tool choice and provenance rules.
   This is supporting work, not the main fix. The model already had many prompt
   warnings and still fabricated a passing verification.

5. Replacement or split templates only where it simplifies the job:
   - Split `repo_worker` file-management work into `repo_locator`,
     `file_rule_extractor`, and `file_candidate_classifier`.
   - Keep `filesystem_worker` as the executor.
   - Treat `verify_worker` as a verifier with mandatory evidence collection first
     and synthesis second.

## Concrete Next Implementation Candidates

- Add `classify_file_management_candidates`:
  - Input: repo path, optional explicit file type rules, destination hints,
    excluded markers.
  - Output: move candidates, held items, unknown items, evidence per decision,
    manifest key hints.
  - Benefit: avoids leaving markdown/CSV category reasoning entirely to the LLM.

- Add `verify_file_state_against_manifest`:
  - Input: manifest path, expected categories, move pairs or candidate report.
  - Output: missing files, extra files, bad counts, files still at source,
    manifest mismatch, pass/fail.
  - Benefit: catches the exact failure from the probe before finalization.

- Harden `verify_worker` final artifacts:
  - Passing `test_results` must cite a command observation from `run_project_tests`,
    `run_focused_tests`, or `run_readonly_command`.
  - Passing file-state verification must cite a deterministic file-state tool result.
  - If no proof observation exists, the worker result should be `failed` or
    `blocked`, not `completed`.

- Improve worker evals:
  - Measure candidate classification recall/precision, fabricated evidence count,
    verification command coverage, manifest schema accuracy, tool calls, model
    calls, and runtime.
  - Use realistic repos with ambiguous files, keep markers, CSVs, JSON artifacts,
    logs, docs, nested directories, and tests.

## Decision

Do not replace the whole worker runtime or agent loop for this issue. The best next
move is to replace some generic worker reasoning with deterministic worker tools and
worker-specific provenance checks. This should make the current loop feel smarter
without adding more retries or planner complexity.

## References

- Anthropic, Building Effective Agents: https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, Writing Effective Tools for Agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, Demystifying Evals for AI Agents: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- OpenAI Agents SDK Tools: https://openai.github.io/openai-agents-python/tools/
- OpenAI Agents SDK Guardrails: https://openai.github.io/openai-agents-python/guardrails/
- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Context7: `/openai/openai-agents-python`, `/openai/openai-python`, `/anthropics/anthropic-sdk-python`

## Saved Path

`plan/research-worker-instance-quality-20260605-211621/README.md`
