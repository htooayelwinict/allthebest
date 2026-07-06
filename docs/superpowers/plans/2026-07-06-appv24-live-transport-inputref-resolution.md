# appv24 Live Transport inputRef Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make live GitHub, Gmail, and Telegram transports resolve arbitrary `steps[].with` `inputRef` values from the selected fixture input.

**Architecture:** Add shared resolver helpers in `live_transports.py` and route existing GitHub/Gmail/Telegram field extraction through them. Keep existing literal `with` precedence and same-name fixture fallback.

**Tech Stack:** Python, appv24 workflow live transports, pytest fake HTTP clients.

---

### Task 1: Add Failing Transport Tests

**Files:**
- Modify: `appV2.4/tests/test_workflow_live_transports.py`

- [x] Add a GitHub transport test with `with.repository`, `with.state`, `with.severity`, and `with.per_page` as `inputRef` objects pointing at fixture keys with different names.
- [x] Add a Gmail transport test with `with.to`, `with.subject`, and `with.body` as `inputRef` objects pointing at fixture keys with different names.
- [x] Add a Telegram transport test with `with.chat_id`, `with.text`, and `with.disable_notification` as `inputRef` objects pointing at fixture keys with different names.
- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_transports.py::test_gmail_create_draft_transport_resolves_input_refs_from_with_fields \
  appV2.4/tests/test_workflow_live_transports.py::test_telegram_send_message_transport_resolves_input_refs_from_with_fields -q
```

Expected: both tests fail before implementation because raw `inputRef` mappings are not resolved.

### Task 2: Implement Shared Resolution

**Files:**
- Modify: `appV2.4/appv24/workflows/live_transports.py`

- [x] Add `_resolve_input_ref(value, fixture_input)` to resolve `{"inputRef": "<name>"}`.
- [x] Update GitHub repository and query-param extraction to resolve `inputRef` values.
- [x] Update `_first_string()` so each primary/fallback value is resolved before string coercion.
- [x] Update `_first_bool()` so literal booleans and boolean `inputRef` values work.
- [x] Keep missing or non-string values as missing.

### Task 3: Document The Live Payload Rule

**Files:**
- Modify: `docs/appv24/workflow-run-live-path.md`

- [x] Add one sentence to GitHub, Gmail, and Telegram sections stating that `steps[].with` supports literal values or `{"inputRef": "<fixture-input-name>"}`.

### Task 4: Verify

**Files:**
- No code edits.

- [x] Run the focused tests from Task 1 and verify pass.
- [x] Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

- [x] Run the full suite:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

- [x] Run:

```bash
git diff --check
```

- [x] Confirm no appv24 red-zone, appv23, or appv231 paths changed.
