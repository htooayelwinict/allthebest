# Sub-project 3: coding-agent Design

Date: 2026-06-19
Status: Design → Implementing
Parent: `2026-06-19-appv22-pi-hermes-parity-decomposition.md`
Reference: `pi/packages/coding-agent/src`

## Goal

Port pi's coding-agent core pattern into `appV2.2/appv22/coding_agent/`: the
`ToolDefinition` pattern (`prompt_snippet`/`prompt_guidelines`/`render_call`/
`render_result` over an `AgentTool`), the built-in tools
`read/write/edit/bash/grep/find/ls`, `build_system_prompt`, and an `AgentSession`
composition root that wires `agent.Agent` with tools + system prompt + `ai`.

## Scope (mirrors pi/packages/coding-agent/src/core)

| appv22 file | pi source | Contents |
|---|---|---|
| `coding_agent/tools/types.py` | `extensions/types.ts` (ToolDefinition) + `tool-definition-wrapper.ts` | `ToolDefinition`, `ToolContext`, `wrap_tool_definition`, `create_tool_definition_from_agent_tool`. |
| `coding_agent/tools/truncate.py` | `tools/truncate.ts` | `truncate_head`, `TruncationResult`, `DEFAULT_MAX_LINES=2000`, `DEFAULT_MAX_BYTES=51200`, `format_size`. |
| `coding_agent/tools/read.py` | `tools/read.ts` | `create_read_tool_definition(cwd)`, `create_read_tool(cwd)`; offset/limit + head truncation + continuation notices. |
| `coding_agent/tools/write.py` | `tools/write.ts` | write file (creates dirs). |
| `coding_agent/tools/edit.py` | `tools/edit.ts` | string-replace edit (old_string→new_string, unique-match). |
| `coding_agent/tools/bash.py` | `tools/bash.ts` | run shell command (subprocess, timeout, output truncation). |
| `coding_agent/tools/grep.py` | `tools/grep.ts` | search text (regex) under cwd. |
| `coding_agent/tools/find.py` | `tools/find.ts` | find files by glob. |
| `coding_agent/tools/ls.py` | `tools/ls.ts` | list directory. |
| `coding_agent/tools/index.py` | `tools/index.ts` | `ToolName`, `all_tool_names`, `create_coding_tools`, `create_read_only_tools`, `create_all_tools`, `create_tool_definition`. |
| `coding_agent/system_prompt.py` | `system-prompt.ts` | `build_system_prompt(options)`. |
| `coding_agent/agent_session.py` | `core/sdk.ts` + `agent-session.ts` (subset) | `AgentSession` / `create_agent_session(cwd, tools, model, convert_to_llm)` composing `agent.Agent`. |

## Parity notes

- `ToolDefinition` adds the UI/prompt concerns to `agent.AgentTool`. `render_call`/
  `render_result` return plain strings for now; the tui `Text` component versions
  land in sub-project 6 (ui-rendering).
- `wrap_tool_definition(defn, ctx_factory)` drops UI fields and injects `ctx`
  (cwd/model) at execute time — exactly pi's bridge.
- Tools do real local filesystem / subprocess I/O (like the existing
  file_management tools), with pi's truncation semantics
  (2000 lines / 50KB head, "use offset=N to continue" notices).
- `build_system_prompt` ports pi's structure: preamble + Available tools (only
  tools with a snippet) + deduped Guidelines + `<project_context>` + date + cwd.
- `AgentSession` is the composition root: builds the `agent.Agent` with wrapped
  tools, the system prompt from active tool snippets/guidelines, and a
  `convert_to_llm`; `prompt(text)` drives one agent run.

## Integration / divergent-code note

The existing `appv22/extensions/file_management/` (1026 + 1426 LOC of bespoke
tools + heuristics) is the divergent coding-agent. It is **superseded** by this
package. It is NOT deleted in this sub-project because the 106 runtime-protection
tests + tui tests still bind to it and the runtime still uses the decision loop.
Its removal is the capstone integration (swap `AppV22AgentRuntime` to the
`agent` loop + `AgentSession`, delete `decide()` shim + file_management), tracked
in the decomposition doc as the final step.

## Testing (real fs in tmp dirs; faux provider for the session)

1. `truncate_head`: long content truncates at line/byte limits.
2. `read`: reads a tmp file; offset/limit; truncation continuation note.
3. `write`: creates file + parent dirs.
4. `edit`: replaces unique old_string; errors on missing/duplicate.
5. `bash`: runs `echo hi`; captures stdout; non-zero exit flagged.
6. `grep`/`find`/`ls`: locate content/files/entries in a tmp tree.
7. `wrap_tool_definition`: produces an `AgentTool` whose execute injects ctx.
8. `build_system_prompt`: includes snippet tools + guidelines + cwd + date.
9. `AgentSession.prompt`: with faux provider doing a `read` tool call, returns a
   final assistant message and persists messages.
