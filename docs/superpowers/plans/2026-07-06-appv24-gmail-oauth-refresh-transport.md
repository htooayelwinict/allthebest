# appv24 Gmail OAuth Refresh Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `gmail.create_draft` live-draft runs to obtain a fresh Gmail access token from OAuth refresh credentials at run time.

**Architecture:** Keep credential refresh inside `workflows/live_transports.py`. The live-draft registry returns an empty map unless live-draft is enabled and either a static Gmail access token or a complete refresh credential set is present. The Gmail draft transport receives a bearer-token provider and never persists or prints token material.

**Tech Stack:** Python callables, `httpx`, existing `BrokerRequest`/`BrokerResult`, fake HTTP clients in pytest.

---

## Files

Modify:

- `appV2.4/appv24/workflows/live_transports.py`
- `appV2.4/tests/test_workflow_live_transports.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify red-zone/kernel files.

## Task 1: Refresh Registry Tests

- [x] **Step 1: Add failing registry tests**

Add tests in `appV2.4/tests/test_workflow_live_transports.py` proving:

```python
def test_live_draft_transport_registry_registers_gmail_with_refresh_credentials() -> None:
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED": "true",
            "APPV24_GMAIL_REFRESH_TOKEN": "refresh-token",
            "APPV24_GMAIL_CLIENT_ID": "client-id",
            "APPV24_GMAIL_CLIENT_SECRET": "client-secret",
        }
    )

    assert tuple(transports) == ("gmail.create_draft",)
    assert isinstance(transports["gmail.create_draft"], GmailCreateDraftTransport)
```

```python
def test_live_draft_transport_registry_blocks_incomplete_refresh_credentials() -> None:
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED": "true",
            "APPV24_GMAIL_REFRESH_TOKEN": "refresh-token",
            "APPV24_GMAIL_CLIENT_ID": "client-id",
        }
    )

    assert transports == {}
```

- [x] **Step 2: Run tests and verify failure**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_transports.py::test_live_draft_transport_registry_registers_gmail_with_refresh_credentials \
  appV2.4/tests/test_workflow_live_transports.py::test_live_draft_transport_registry_blocks_incomplete_refresh_credentials \
  -q
```

Expected: first test fails because refresh credentials do not register a transport yet.

## Task 2: Refresh Invocation Tests

- [x] **Step 1: Add failing invocation tests**

Add fake-client tests proving:

- token endpoint receives `client_id`, `client_secret`, `refresh_token`, `grant_type=refresh_token`
- Gmail draft request receives `Authorization: Bearer refreshed-token`
- outputs contain only `draft_id` and `thread_id`
- malformed token response blocks before Gmail draft POST

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

Expected: failures because token refresh provider does not exist.

## Task 3: Implement Refresh Provider

- [x] **Step 1: Extend live transport env constants**

Add:

```python
GMAIL_REFRESH_TOKEN_ENV = "APPV24_GMAIL_REFRESH_TOKEN"
GMAIL_CLIENT_ID_ENV = "APPV24_GMAIL_CLIENT_ID"
GMAIL_CLIENT_SECRET_ENV = "APPV24_GMAIL_CLIENT_SECRET"
GOOGLE_OAUTH_TOKEN_URL_ENV = "APPV24_GOOGLE_OAUTH_TOKEN_URL"
DEFAULT_GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
```

- [x] **Step 2: Add token provider classes**

Add:

```python
class GmailTokenError(Exception): ...
class StaticBearerTokenProvider: ...
class GoogleOAuthRefreshTokenProvider: ...
```

The refresh provider posts form data to the token URL, extracts `access_token`, and raises `GmailTokenError` with sanitized messages for HTTP, request, or malformed-response failures.

- [x] **Step 3: Update `GmailCreateDraftTransport`**

Accept either `token` or `token_provider`. Resolve the token inside `invoke`, catch `GmailTokenError`, and return a blocked `BrokerResult`.

- [x] **Step 4: Update `build_live_draft_transports_from_env`**

Prefer static access token. If absent, require complete refresh credentials. Register `gmail.create_draft` only when one token mode is complete.

- [x] **Step 5: Verify transport tests pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

## Task 4: Docs and Verification

- [x] **Step 1: Update live path docs**

Document static access-token mode and refresh-token mode, including that refresh is performed only during live-draft run invocation, not preflight.

- [x] **Step 2: Run focused/broad checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_transports.py \
  appV2.4/tests/test_workflow_live_preflight.py \
  appV2.4/tests/test_workflow_live_runner.py \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_live_draft_preflight_passes_with_env_credentials \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_live_draft_preflight_passes_with_env_credentials \
  -q
```

- [x] **Step 3: Run full verification and guards**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git status --short | sed 's/^...//' | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
git status --short | sed 's/^...//' | rg -v '^(appV2\.4/|docs/appv24/|docs/superpowers/|examples/appv24/)' || true
```
