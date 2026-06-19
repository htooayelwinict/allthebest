# Sub-project 2: agent-loop-core Design

Date: 2026-06-19
Status: Design → Implementing
Parent: `2026-06-19-appv22-pi-hermes-parity-decomposition.md`
Reference: `pi/packages/agent/src` (`types.ts`, `agent-loop.ts`, `agent.ts`)

## Goal

Port pi's `agent` package core into `appV2.2/appv22/agent/`: the `AgentMessage` +
`convert_to_llm` boundary, the functional run loop (`run_loop` /
`stream_assistant_response` / `execute_tool_calls`), the `AgentEvent` protocol,
and the stateful `Agent` wrapper. The loop drives `appv22.ai.stream_simple`.

## Scope (mirrors pi/packages/agent/src)

| appv22 file | pi source | Contents |
|---|---|---|
| `agent/types.py` | `types.ts` | `AgentMessage`, `AgentTool`, `AgentToolResult`, `AgentContext`, `AgentLoopConfig`, `AgentEvent` union (`agent_start`/`agent_end`/`turn_start`/`turn_end`/`message_start`/`message_update`/`message_end`/`tool_execution_*`), hook contexts (`BeforeToolCallContext`, `AfterToolCallContext`, `ShouldStopAfterTurnContext`, `AgentLoopTurnUpdate`), `AbortSignal`, `ToolExecutionMode`, `QueueMode`. |
| `agent/agent_loop.py` | `agent-loop.ts` | `agent_loop`, `agent_loop_continue`, `run_agent_loop`, `run_agent_loop_continue`, `run_loop`, `stream_assistant_response`, `execute_tool_calls` (sequential + parallel), `prepare_tool_call`, `execute_prepared_tool_call`, `finalize_executed_tool_call`, `AgentEventStream`, `AgentEventSink`. |
| `agent/agent.py` | `agent.ts` | `Agent` class: state, `subscribe`, `prompt`, `continue_`, `steer`, `follow_up`, `abort`, `reset`, `wait_for_idle`, event reduction (`process_events`), `PendingMessageQueue`. |
| `ai/validation.py` (add) | `utils/validation.ts` | `validate_tool_arguments(tool, tool_call)` — JSON-schema validation, returns parsed args or raises. |

## Parity notes

- Event `type` string literals identical to pi. Loop control flow mirrors pi
  `run_loop` exactly (outer follow-up loop, inner tool/steering loop, first-turn
  handling, `prepare_next_turn`, `should_stop_after_turn`, steering drain).
- Python is synchronous: `emit` is a sync callable (`AgentEventSink`); the loop
  iterates the `AssistantMessageEventStream` synchronously (`for event in response`)
  and reads `response.result_sync()`. `agent_loop` runs `run_agent_loop` in a
  worker thread feeding an `AgentEventStream`.
- Tool execution: `parallel` (default) uses a `ThreadPoolExecutor`; preparation is
  sequential, execution concurrent, `tool_execution_end` in completion order,
  result messages in assistant source order — matching pi. `sequential` mode and
  per-tool `execution_mode="sequential"` honored.
- `convert_to_llm: (list[AgentMessage]) -> list[Message]` is the only place
  AgentMessage collapses to ai `Message`. UI-only messages are filtered there.
- Hooks are contractually no-throw (errors become safe fallbacks), as in pi.

## Integration (this sub-project)

- New package is standalone + tested with the faux provider. The existing
  decision-routed `AppV22AgentRuntime` stays for now (its replacement + the
  `decide()` shim deletion happen during coding-agent integration in sub-project 3,
  to avoid breaking the 106 runtime-protection tests prematurely).

## Testing (faux provider, no network)

1. Single text turn: `agent_loop` emits `agent_start → turn_start → message_start
   → message_update* → message_end → turn_end → agent_end`; returns the assistant
   message.
2. Tool call turn: assistant emits a toolCall → `tool_execution_start/end` +
   toolResult message → loop continues → second turn finalizes.
3. `should_stop_after_turn` stops the loop after one turn.
4. Steering + follow-up message injection.
5. `before_tool_call` block → error tool result; unknown tool → error result.
6. `terminate` on every result stops the batch.
7. `Agent.prompt` reduces events into state (`messages` appended on `message_end`,
   `pending_tool_calls`, `error_message`).
