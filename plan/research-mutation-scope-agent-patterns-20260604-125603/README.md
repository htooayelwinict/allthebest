# Mutation Scope Agent Patterns Research

## Question

Does a production-grade coding/file-management agent normally require the model to emit a deterministic mutation-scope artifact before it can edit files, or should mutation be controlled primarily by runtime tool gates?

## Summary

Most mature agent systems do not expose a separate model-generated mutation-scope artifact as the hard gate for editing. They constrain mutation through tool permissions, workspace/sandbox policy, approval prompts, diff previews, exact edit/apply-patch tools, hooks, rollback/version control, and verification. Planning is still useful, but the final authority over whether a file can be mutated usually belongs to the runtime/editor tool, not to a prior free-form LLM artifact.

Our current runtime is stricter than these patterns: a `bounded_mutation` step can be blocked before the edit worker runs if `mutation_scope.target_paths` is missing or too broad. That is safe, but it has become a reliability bottleneck because models often produce useful design intent while failing the exact schema gate.

## Key Findings

- OpenAI Agents SDK emphasizes tool guardrails and tool input/output validation around each function tool call. The official apply-patch tool pattern lets the model emit structured patch operations; the application harness owns actual filesystem operations and approval logic.
- Claude Code documents permission rules, path-level filesystem sandboxing, and hooks around `Read`, `Edit`, `Write`, `Bash`, and `WebFetch`. PreToolUse/PostToolUse hooks are the control points, with filesystem allow/deny settings merged into sandbox policy.
- GitHub Copilot CLI uses approval prompts and command/tool allow/deny rules. It can create/modify files in the active directory, but risky mutation is governed by tool approval, not a separate LLM-produced mutation-scope schema.
- OpenCode exposes edit, write, apply_patch, read, grep, glob, webfetch, websearch, and todowrite tools with configurable permissions. File mutation tools are controlled by the `edit` permission; paths appear inside the tool request/patch rather than in a prior planner artifact.
- Aider uses a selected edit format such as whole-file, search/replace diff, fenced diff, or unified diff. Its user-facing scope is the files added to chat plus repo map context; architect mode separates reasoning from editing by using an editor model, not a hard mutation-scope artifact.
- OpenHands file-based agents configure tools and permission modes. Delegation and confirmation policy are agent/runtime concerns.
- SWE-agent focuses on an agent-computer interface: file viewer/search/edit commands with command docs, error feedback, and optional linting. Editing is performed through specialized commands and observations, not a prior write-scope object.
- OpenClaw and Hermes public materials emphasize per-session tools, permissions, diffs, and runtime modes. The public pattern again looks like tool execution control, not deterministic predeclared write-scope schemas.

## Recommendation

Keep mutation safety, but move the hard gate closer to the edit tool:

1. Treat `mutation_scope` as advisory design context, not the only source of write authorization.
2. Let MUTATE workers propose concrete write operations or patches.
3. Have the kernel/editor tool derive touched paths from the proposed operation, validate against workspace root, deny lists, phase permissions, max blast radius, and task intent.
4. If the proposed operation is too broad, return a tool error to the same worker instance and let it narrow the patch once before blocking.
5. Use strict path gates at the tool execution boundary, plus diff/rollback generation and verification after mutation.
6. Keep planner-level replan only for semantic drift, missing required artifacts/evidence, or wrong plan structure; do not use replan for ordinary edit-scope narrowing.

## Sources

- Context7: OpenAI Agents SDK guardrails and tool validation.
- Context7: Claude Code permissions, allowed tools, and hooks.
- Context7: GitHub Copilot CLI tool approval and file mutation docs.
- OpenAI apply patch docs: https://developers.openai.com/api/docs/guides/tools-apply-patch
- OpenAI Codex CLI help docs: https://help.openai.com/en/articles/11096431
- Anthropic Claude Code settings: https://code.claude.com/docs/en/settings
- Anthropic Claude Code hooks: https://code.claude.com/docs/en/hooks
- GitHub Copilot CLI docs: https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli
- GitHub Copilot responsible use: https://docs.github.com/en/copilot/responsible-use/copilot-cli
- OpenCode agents/tools docs: https://opencode.ai/docs/agents/ and https://opencode.ai/docs/tools/
- Aider usage/edit-format docs: https://aider.chat/docs/usage.html and https://aider.chat/docs/more/edit-formats.html
- OpenHands file-based agents docs: https://docs.openhands.dev/sdk/guides/agent-file-based
- SWE-agent command docs: https://swe-agent.com/0.7/config/commands/
- OpenClaw code mode docs: https://docs.openclaw.ai/reference/code-mode
- Hermes IDE public architecture page: https://hermes-ide.com/
