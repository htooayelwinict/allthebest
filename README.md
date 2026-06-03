# <div align="center">ALLTHEBEST</div>

<div align="center">

![Banner](https://capsule-render.vercel.app/api?type=waving&height=280&color=0:0f172a,35:0ea5e9,70:22c55e,100:f59e0b&text=ALLTHEBEST&fontSize=64&fontAlignY=38&desc=LLM-Native%20Decompressor%20%E2%86%92%20Planner%20%E2%86%92%20Worker%20Kernel&descAlignY=58&animation=fadeIn)

[![Python](https://img.shields.io/badge/Python-3.13+-1f2937?style=for-the-badge&logo=python&logoColor=white)](#)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.6+-0b3b2e?style=for-the-badge)](#)
[![Pydantic](https://img.shields.io/badge/Pydantic-2.x-0d9488?style=for-the-badge)](#)
[![Tests](https://img.shields.io/badge/Tested_with-pytest-334155?style=for-the-badge)](#)

**A sharp runtime pipeline that turns messy requests into structured execution plans.**

</div>

---

## Why This Exists

`allthebest` is a runtime architecture playground with a strict boundary:

- `DecompressorRuntime` converts user intent into a validated `Envelope`
- `PlannerRuntime` chooses strategy and emits a `Plan`
- `WorkerKernelRuntime` executes through bounded workers and returns `Result`

The graph topology stays intentionally simple and explicit:

`START -> decompressor_node -> planner_node -> worker_kernel_node -> END`

---

## Core Features

- LLM-driven decompression with schema validation and repair path
- Canonicalized `Envelope` boundary (planner fields are stripped out)
- Deterministic planner selection across direct/code/research/infra/fallback planners
- Worker-kernel compilation and dispatch with explicit permissions/budgets
- Focused tests for decompressor, planner, graph, and worker kernel behavior

---

## Architecture Snapshot

```text
app/
  decompressor/
    runtime.py        # LLM-only decompressor entrypoint
    prompt_chain.py   # Coalesced model call + validation/repair
    contracts.py      # Pydantic contracts for decompressed envelope
    canonicalize.py   # Final boundary canonicalization
    model_client.py   # OpenAI-compatible chat-completions client
    env_config.py     # .env wiring for model client config
  planner/
    selector.py       # Deterministic planner routing
    planners/         # direct, code, research, infra, fallback
  worker_kernel/
    runtime.py        # Plan -> task execution runtime
  graph.py            # LangGraph assembly and node wiring
```

---

## Quickstart

### 1) Environment

```bash
cp .env.example .env
```

Set required values in `.env`:

- `DECOMPRESSOR_LLM_ENABLED=true`
- `DECOMPRESSOR_LLM_API_KEY=...`
- `DECOMPRESSOR_LLM_MODEL=...`

Optional:

- `DECOMPRESSOR_LLM_BASE_URL=...`
- `DECOMPRESSOR_LLM_PROVIDER_SORT=latency`
- `DECOMPRESSOR_LLM_MAX_TOKENS=700`

### 2) Install

```bash
uv sync
```

### 3) Run Tests

```bash
uv run pytest -q
```

### 4) Smoke Test Live Envelopes

```bash
uv run python scripts/smoke_test_envelopes.py
```

Or pass your own prompts:

```bash
uv run python scripts/smoke_test_envelopes.py "who am i" "today date"
```

### Live Worker Runtime Probe

For full live QA of `decompressor -> planner -> worker kernel`, see
[docs/live-worker-runtime-probe.md](docs/live-worker-runtime-probe.md).

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario payment_retry \
  --matrix-poll-interval 1 \
  --out-dir plan
```

---

## Example Runtime Invocation

```python
from app.decompressor.runtime import DecompressorRuntime

runtime = DecompressorRuntime.from_env(".env")
envelope = runtime.run("fix network_sniffer.py")
print(envelope.model_dump_json(indent=2))
```

---

## Design Notes

- The decompressor is intentionally **descriptive**, not a planner.
- The planner is intentionally **deterministic**, not an LLM free-for-all.
- The worker kernel is intentionally **bounded** (permissions, budgets, explicit step types).
- If model outputs drift, schema validation and repair guardrails catch it early.

---

## Status

Actively iterated architecture with heavy emphasis on:

- boundary correctness
- prompt-chain reliability under live providers
- safety constraints before mutation-capable planning

---

<div align="center">
  <sub>Built for fast iteration, clear boundaries, and zero hand-wavy runtime behavior.</sub>
</div>
