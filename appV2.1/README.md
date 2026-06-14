# AppV2.1

Greenfield runtime-first AppV2 implementation.

This directory intentionally does not refactor or depend on the legacy `appV2/`
planner-first runtime. The target architecture is:

- Pi-style observe-act-revise loop.
- Hermes-style single runtime, tool broker, adapter edges, and dual context control.
- AppV2 typed world state, event-sourced state harness, mutation leases, artifact validators, pause/resume, and verification receipts.

Planned package layout:

```text
appV2.1/
  appv21/
    runtime/
    state/
    tools/
    extensions/
    context/
    surfaces/
```

Implemented runtime seams:

- `runtime/services.py`: composition root, matching Pi's service factory pattern.
- `runtime/event_bus.py`: lifecycle event fanout with isolated subscriber failures.
- `runtime/session_store.py`: append-only JSONL lineage for replay, branch, and compaction work.
- `extensions/runner.py`: capability-scoped extension hooks; extensions can observe and advise but cannot bypass leases.
- `context/prompt_builder.py`: layered system/agent/skills/world/tools/output contract for future model turns.
- `tools/broker.py`: Hermes-style mediated tools plus runtime-issued mutation leases.

See:

```text
docs/superpowers/plans/2026-06-15-appv2-pi-hermes-runtime-first-architecture.md
```
