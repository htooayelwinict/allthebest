# appv24 Live-Production Telegram V0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first explicit live-production path for `telegram.send_message` only.

**Architecture:** Keep the production surface narrow. Add an env-gated Telegram transport, an explicit production broker flag, catalog production eligibility for one action, production preflight, production runner, and CLI/TUI commands. Reuse existing approval, idempotency, credential, policy, output filtering, and run-store contracts.

**Tech Stack:** Python, pytest, httpx-compatible injected clients, existing appv24 workflow modules.

---

## Files

Modify:

- `appV2.4/appv24/workflows/live_transports.py`
- `appV2.4/appv24/workflows/live_broker.py`
- `appV2.4/appv24/workflows/connector_packs.py`
- `appV2.4/appv24/workflows/live_preflight.py`
- `appV2.4/appv24/workflows/live_runner.py`
- `appV2.4/appv24/workflows/__init__.py`
- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- `appV2.4/tests/test_workflow_live_transports.py`
- `appV2.4/tests/test_workflow_live_broker.py`
- `appV2.4/tests/test_workflow_connector_catalog.py`
- `appV2.4/tests/test_workflow_live_preflight.py`
- `appV2.4/tests/test_workflow_live_runner.py`
- `appV2.4/tests/test_cli.py`
- `appV2.4/tests/test_tui.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify red-zone/kernel files.

## Task 1: Telegram Production Transport

- [x] **Step 1: Write failing transport tests**

Add tests proving:

- `build_live_production_transports_from_env({APPV24_TELEGRAM_BOT_TOKEN: ...}) == {}`
- env gate plus token registers `telegram.send_message`
- `TelegramSendMessageTransport` posts JSON to `/bot<TOKEN>/sendMessage`
- missing fields block without HTTP
- HTTP/malformed responses are sanitized

- [x] **Step 2: Run transport tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

Expected: imports fail for missing production registry/transport.

- [x] **Step 3: Implement transport and env registry**

Add:

```python
LIVE_PRODUCTION_ENABLED_ENV = "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED"
TELEGRAM_BOT_TOKEN_ENV = "APPV24_TELEGRAM_BOT_TOKEN"
TELEGRAM_API_BASE_URL_ENV = "APPV24_TELEGRAM_API_BASE_URL"
DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"
```

Add `build_live_production_transports_from_env` and `TelegramSendMessageTransport`.

- [x] **Step 4: Run transport tests and verify pass**

Run the same transport test command.

## Task 2: Broker and Catalog Production Gates

- [x] **Step 1: Write failing broker/catalog tests**

Tests:

- default production broker still blocks
- production broker routes only with explicit flag
- `telegram.send_message` is production enabled
- `github.create_issue` and `gmail.create_draft` are not production enabled

- [x] **Step 2: Run broker/catalog tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_broker.py appV2.4/tests/test_workflow_connector_catalog.py -q
```

- [x] **Step 3: Implement broker flag and catalog update**

Add `production_enabled: bool = False` to `LiveWorkflowBroker`.

Set only `telegram.send_message` to `live_production_enabled=True`.

- [x] **Step 4: Run broker/catalog tests and verify pass**

Run the same broker/catalog test command.

## Task 3: Live-Production Preflight

- [x] **Step 1: Write failing preflight tests**

Tests:

- approved Telegram send passes when transport is configured
- missing transport blocks before any live call
- missing approval blocks
- non-production-enabled mutation blocks

- [x] **Step 2: Run preflight tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_preflight.py -q
```

- [x] **Step 3: Implement production preflight**

Add `preflight_live_production_workflow` and `format_live_production_preflight_report`.

Reuse the existing approval-index/idempotency logic shape from live-draft, but report `live-production` in messages.

- [x] **Step 4: Run preflight tests and verify pass**

Run the same preflight test command.

## Task 4: Live-Production Runner

- [x] **Step 1: Write failing runner tests**

Tests:

- approved Telegram send executes fake production transport and writes run artifacts
- missing approval blocks before transport invocation
- production-disabled action blocks before transport invocation

- [x] **Step 2: Run runner tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_runner.py -q
```

- [x] **Step 3: Implement production runner**

Add `run_live_production_workflow`.

Use mode `WorkflowRunMode.LIVE_PRODUCTION`, approval ledger, credential preflight, allowlist, catalog production flag, idempotency, and production-enabled broker.

- [x] **Step 4: Export production APIs**

Export:

- `build_live_production_transports_from_env`
- `run_live_production_workflow`
- `preflight_live_production_workflow`
- `format_live_production_preflight_report`

- [x] **Step 5: Run runner tests and verify pass**

Run the same runner test command.

## Task 5: CLI and TUI Commands

- [x] **Step 1: Write failing CLI/TUI tests**

Tests:

- `/workflow-live-production-preflight ...` does not invoke the model
- `/workflow-run-live-production ...` does not invoke the model
- output shows passed/blocked status and `telegram.send_message`

- [x] **Step 2: Run command tests and verify failure**

Run the new focused CLI/TUI tests.

- [x] **Step 3: Wire command handlers**

Add command parsing beside live-read/live-draft commands.

Construct production broker as:

```python
LiveWorkflowBroker(
    build_live_production_transports_from_env(os.environ),
    mode="live-production",
    production_enabled=True,
)
```

The registry itself remains empty unless env gates are set.

- [x] **Step 4: Run command tests and verify pass**

Run the same focused CLI/TUI tests.

## Task 6: Docs and Verification

- [x] **Step 1: Update live path docs**

Document env gates and the two new commands.

- [x] **Step 2: Run focused workflow checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_transports.py \
  appV2.4/tests/test_workflow_live_broker.py \
  appV2.4/tests/test_workflow_connector_catalog.py \
  appV2.4/tests/test_workflow_live_preflight.py \
  appV2.4/tests/test_workflow_live_runner.py \
  -q
```

- [x] **Step 3: Run CLI/TUI checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -q
```

- [x] **Step 4: Run full suite and guards**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git status --short | sed 's/^...//' | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
git status --short | sed 's/^...//' | rg -v '^(appV2\.4/|docs/appv24/|docs/superpowers/|examples/appv24/)' || true
```

- [x] **Step 5: Inspect final diff**

Confirm:

- no red-zone files changed
- appv23/appv231 untouched
- production remains one-action scoped
- no secrets printed
- live-production is still opt-in via env and command
