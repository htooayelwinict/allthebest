# Phase 3 - One Agent Loop

## Goal

Make one `AgentRunLoop` responsible for all worker model/tool/final behavior.

## Steps

- Move V2 prompt/tool repair rules into `AgentRunLoop`.
- Execute worker templates through the same loop.
- Remove artifact validation from group runner.
- Make final artifact validation part of the loop outcome.
- Keep worker templates as prompt/tool definitions only.

## Verification

- Tool denial produces observation and local repair.
- Malformed model output gets one local repair turn.
- Mutation cannot complete without write evidence.
- Repo/discovery workers cannot use invented tool names.

