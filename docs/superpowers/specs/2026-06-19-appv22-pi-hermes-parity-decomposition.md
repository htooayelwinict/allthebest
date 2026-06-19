# appv22 → pi + hermes Parity: Decomposition Spec

Date: 2026-06-19
Status: Approved (decomposition + approach)
Owner: appv22 alignment effort

## Progress

All six sub-project parity packages are ported and tested, plus an end-to-end
capstone integration. **252 tests green** (188 legacy + 64 new parity/integration).
Zero `pi`/`hermes`/`appv21` runtime imports anywhere under `appV2.2/`.

| # | Sub-project | appv22 package | Status |
|---|---|---|---|
| 1 | ai-parity | `appv22/ai/` | DONE (25 tests) |
| 2 | agent-loop-core | `appv22/agent/` | DONE (6 tests) |
| 3 | coding-agent | `appv22/coding_agent/` | DONE (10 tests) |
| 4 | hermes dual-pass | `appv22/compaction/compressor.py` | DONE (6 tests) |
| 5 | hermes timing | `appv22/compaction/timing.py` | DONE (6 tests) |
| 6 | ui-rendering | `appv22/tui/` | DONE (9 tests) |
| – | capstone integration | `appv22/app.py` (`CodingApp`) | DONE (2 tests) |

## Goal

Make `appV2.2/appv22` (the Python "appv22" agent core) and `appV2.2/appv22_ui`
(its UI) **structurally and behaviorally match** the reference designs:

- **pi** (TypeScript monorepo, `pi/packages`): 3 engine packages — `ai`,
  `agent` (agent loop core), `coding-agent` — plus `tui` (rendering).
- **hermes-agent** (Python, `hermes-agent`): the **dual-pass** compaction design
  and the **timing compaction** (trigger-matrix + session-rotation) design.

"Match" was settled with the user as:

- **Refactor-to-match, delete divergent code.** Rewrite each appv22 module to
  mirror the pi/hermes structure, names, data flow, and event/message protocols
  (Python idiom). Delete appv22 logic that has no pi/hermes counterpart. Keep
  behavior covered by tests.
- **Structural + behavioral parity.** Same module boundaries, type/class/function
  names, and event/message protocols — ported to Python (not literal TS syntax),
  with the same observable behavior.
- **Actual porting, not importing.** UI and rendering are ported into appv22, not
  called from `pi`/`hermes` source modules. No runtime import of `pi`, `hermes`,
  or `hermes-agent`.
- **Remove appv21 entirely; port from a fresh start.** appv22 currently reaches
  into a sibling `appV2.1/` package (dynamic `import_module("appv21...")` +
  `sys.path` discovery, confined to `appv22/providers/appv2_env.py`). All appv21
  coupling is removed and the small pieces appv22 used (env `.env` loader,
  env→model-config resolution, null-provider behavior) are ported as fresh appv22
  code. No `appv21` / `appV2.1` reference remains under `appV2.2/`.

## Current state (scan summary)

appv22 is already a self-contained Python reimplementation (~6,400 LOC core +
~2,200 LOC UI + a Node `pi_tui/` frontend) that *cites* "Pi-style"/"Hermes-style"
but diverges from the references in concrete ways:

- **vs pi `ai`**: no provider/model abstraction and no streaming
  `AssistantMessageEvent` protocol. Uses a one-shot `complete_json()` decision
  call (`providers/appv2_env.py`) and dynamically imports `appv21`.
- **vs pi `agent`**: the run loop (`runtime/agent_loop.py:_run_state`) is
  decision-routed (`tool_call`/`finalize`/`pause`/`compact`), not pi's
  `AgentMessage` + `convertToLlm` + streaming `AgentEvent`
  (`turn_start`/`message_update`/`tool_execution_*`) loop. No `Agent`/`AgentHarness`.
- **vs pi `coding-agent`**: one `file_management` extension with 14 bespoke tools
  + heavy heuristics, vs pi's `read/bash/edit/write/grep/find/ls` `ToolDefinition`
  pattern with `renderCall`/`renderResult`.
- **vs hermes dual-pass**: no literal two-pass compaction. hermes =
  deterministic prune-pass (dedup tool outputs / summarize old tool results /
  strip images / truncate huge tool-call args) **then** LLM structured-summary
  pass (iterative-update vs from-scratch), with protected head + token-budgeted
  tail, and an anti-thrash guard (skip after two <10%-effective passes).
- **vs hermes timing compaction**: no 4-phase trigger matrix (preflight /
  post-response real-tokens / overflow-recovery / manual `force`), no session
  rotation with `parent_session_id` lineage, no cooldowns.
- **vs pi `tui`**: ad-hoc renderer, not a port of pi's `Component`/`Container`/`TUI`
  differential renderer.

## Decomposition (6 sub-projects)

Each sub-project gets its own design spec → implementation plan → implement cycle.
Dependency order (each layer builds on the previous):

1. **ai-parity** — port pi `ai`: provider/model abstraction, message types,
   streaming `AssistantMessageEvent` protocol, tool-call format. **Remove appv21**
   and build a fresh, self-contained provider with **real SSE streaming** (httpx).
   **[DONE — `appv22/ai/`, 10 tasks, 213 tests green, zero appv21/pi/hermes imports]**
2. **agent-loop-core-parity** — port pi `agent`: `AgentMessage`/`convertToLlm`,
   the run loop (`runLoop`/`streamAssistantResponse`/`executeToolCalls`), the
   `AgentEvent` protocol, `Agent` class, and the harness seam. Switch the loop to
   consume `stream_simple`; delete the old `decide()` decision provider.
3. **coding-agent-parity** — port pi `coding-agent`: `read/bash/edit/write/grep/find/ls`
   `ToolDefinition` pattern (`promptSnippet`/`renderCall`/`renderResult`),
   `build_system_prompt`, session manager, `AgentSession` composition root.
4. **hermes-dual-pass-compaction** — port hermes `ContextCompressor.compress`:
   prune-pass + LLM-summary-pass, structured template, iterative update,
   anti-thrash, protected head + token-budgeted tail.
5. **hermes-timing-compaction** — port the 4-phase trigger matrix, session
   rotation / `parent_session_id` lineage, cooldowns, token thresholds.
6. **ui-rendering-parity** — port pi `tui` (`Component`/`Container`/`TUI` diff
   renderer + key/util pieces) and the coding-agent interactive components, into
   appv22 (no external imports).

## Cross-cutting rules

- **No mid-migration breakage.** Each sub-project keeps the app runnable. Divergent
  code that a later sub-project replaces is kept behind a clearly marked
  transitional shim with an explicit deletion checkpoint in that sub-project.
- **Tests are the safety net.** `appV2.2/tests` (106 + 78 + 4 tests) must keep
  passing or be migrated deliberately when a behavior is intentionally replaced to
  match the reference. New parity behavior gets new tests (TDD where practical).
- **No runtime import of pi/hermes.** Verified by grep gate in CI/tests.
- **No appv21 coupling.** Grep gate: zero `appv21` / `appV2.1` references under
  `appV2.2/` after sub-project 1.
- **Naming parity.** Python snake_case equivalents of pi camelCase names
  (`streamSimple` → `stream_simple`, `AssistantMessageEvent` kept as a class/union,
  event `type` string literals kept identical: `"text_delta"`, `"toolcall_end"`, …).

## Out of scope (YAGNI)

- Porting all 12 pi `ai` providers. appv22 ports only the public `ai` surface it
  needs plus one concrete provider (appv21-backed SSE).
- pi features with no appv22 need (image generation, OAuth device flows, Bedrock
  SigV4, Kitty image protocol) unless a later sub-project requires them.

## Verification per sub-project

- Reference parity check: a short mapping table (pi/hermes symbol → appv22 symbol).
- `python -m pytest appV2.2/tests` green (or migrated with rationale).
- Grep gate: no `import pi` / `import hermes` in appv22 runtime code.

## Sub-project specs

- 1: `2026-06-19-appv22-ai-parity-design.md`
- 2: `2026-06-19-appv22-agent-loop-core-design.md`
- 3: `2026-06-19-appv22-coding-agent-design.md`
- 4/5: `2026-06-19-appv22-hermes-compaction-design.md`
- 6: `2026-06-19-appv22-ui-rendering-design.md`

## Removal manifest (divergent legacy code — superseded, pending explicit go-ahead)

The pi+hermes-compliant stack (`appv22/ai`, `appv22/agent`, `appv22/coding_agent`,
`appv22/compaction`, `appv22/tui`, `appv22/app.py`) now supersedes the original
divergent implementation. These legacy modules do not match pi/hermes design and
are slated for deletion:

- `appv22/runtime/` — decision-routed loop (`agent_loop.py:_run_state`,
  `decisions.py`, `reducer.py`, `services.py`); superseded by `appv22/agent` +
  `appv22/app.py`. (`provider_errors.py` already aliases `appv22/ai/overflow.py`.)
- `appv22/context/` — bespoke harness/compressor/summary heuristics; superseded by
  `appv22/compaction`.
- `appv22/extensions/file_management/` (+ `extensions/`) — 14 bespoke tools +
  heuristics; superseded by `appv22/coding_agent/tools` (read/bash/edit/write/
  grep/find/ls).
- `appv22/state/`, `appv22/tools/`, `appv22/prompts/` — superseded by
  `appv22/ai/types`, `appv22/agent/types`, `appv22/coding_agent`.
- `appv22/providers/` (legacy `decide()` shim + `json_client`) — superseded by
  `appv22/ai/providers`.
- `appv22_ui/` (+ `pi_tui/` node frontend) — superseded by `appv22/tui`.

**Why not auto-deleted in this pass:** removing them also retires the 184 legacy
tests (`test_runtime_protection.py`, `test_tui_app.py`, `test_live_ui_event_sink.py`)
that encode the divergent behavior, plus the `scripts/` TUI bridge. Per the repo
rule "always ask before removing functionality that appears intentional," this
destructive deletion is gated on an explicit go-ahead. On approval: delete the
modules above + their tests, repoint `scripts/` and `appV2.2/package.json` to
`appv22/app.py`, and run `python -m pytest` to confirm the new-stack suite stays
green.
