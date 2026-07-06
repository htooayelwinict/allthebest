# appv24 Live-Draft Gmail Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit env-gated live-draft transport registry so `/workflow-run-live-draft` can create an unsent Gmail draft through `gmail.create_draft` when enabled, while remaining blocked by default.

**Architecture:** Keep live connector code behind `workflows/live_transports.py`. The registry returns an empty map unless live-draft is explicitly enabled and a Gmail OAuth access token is present. CLI/TUI live-draft commands use the registry; live-production remains disabled.

**Tech Stack:** Python stdlib `email.message.EmailMessage`, `base64.urlsafe_b64encode`, `httpx`, existing `BrokerRequest`/`BrokerResult`/`LiveWorkflowBroker`, pytest with fake HTTP clients.

---

## Files

Modify:

- `appV2.4/appv24/workflows/live_transports.py`
- `appV2.4/appv24/workflows/__init__.py`
- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- `appV2.4/tests/test_workflow_live_transports.py`
- `appV2.4/tests/test_cli.py`
- `appV2.4/tests/test_tui.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify red-zone/kernel files.

## Task 1: Transport Tests

- [x] **Step 1: Write failing transport tests**

Add tests for disabled-by-default live-draft registry, enabled registry, Gmail request shaping/MIME encoding/output slimming, and missing draft field blocking.

- [x] **Step 2: Verify transport tests fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

Expected: import or assertion failures because live-draft transport APIs do not exist yet.

## Task 2: Transport Implementation

- [x] **Step 1: Implement live-draft registry and Gmail transport**

Add `build_live_draft_transports_from_env` and `GmailCreateDraftTransport`.

- [x] **Step 2: Export registry API**

Export the new names from `appV2.4/appv24/workflows/__init__.py`.

- [x] **Step 3: Verify transport tests pass**

Run the transport test command again.

## Task 3: CLI/TUI Wiring

- [x] **Step 1: Write failing CLI/TUI tests**

Add tests proving `/workflow-run-live-draft` uses `build_live_draft_transports_from_env` and can pass with a fake transport. Existing disabled-registry tests must remain unchanged.

- [x] **Step 2: Verify command tests fail**

Run the two new tests directly. Expected: command still uses an empty transport map and blocks.

- [x] **Step 3: Wire CLI/TUI live-draft commands**

Import `build_live_draft_transports_from_env` and use it only for live-draft broker construction.

- [x] **Step 4: Verify command tests pass**

Run the two new tests directly.

## Task 4: Docs and Verification

- [x] **Step 1: Document live-draft transport enablement**

Update `docs/appv24/workflow-run-live-path.md` with the env gate, token variable, draft input contract, and no-send/no-secrets guarantee.

- [x] **Step 2: Run focused checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_transports.py \
  appV2.4/tests/test_workflow_live_runner.py \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_run_live_draft_uses_env_transport_registry \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_run_live_draft_uses_env_transport_registry \
  -q
```

- [x] **Step 3: Run broad checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py appV2.4/tests/test_workflow_live_transports.py appV2.4/tests/test_workflow_live_runner.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git status --short | sed 's/^...//' | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
git status --short | sed 's/^...//' | rg -v '^(appV2\.4/|docs/appv24/|docs/superpowers/|examples/appv24/)' || true
```
