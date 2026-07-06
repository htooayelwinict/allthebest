# appv24 Live Transport Readiness Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make live-read and live-draft preflight block when the requested live connector action does not have a configured env-gated live transport.

**Architecture:** Keep provider-free preflight logic in `workflows/live_preflight.py` by accepting an optional configured action set. CLI and TUI preflight commands build transport registries from environment variables and pass only the action names into preflight. Transport objects are never invoked during preflight.

**Tech Stack:** Python collections, existing workflow preflight functions, existing live transport registry builders, pytest.

---

## Files

Modify:

- `appV2.4/appv24/workflows/live_preflight.py`
- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- `appV2.4/tests/test_workflow_live_preflight.py`
- `appV2.4/tests/test_cli.py`
- `appV2.4/tests/test_tui.py`
- `docs/appv24/workflow-run-live-path.md`

Do not modify red-zone/kernel files.

## Task 1: Unit-Level Preflight Readiness

- [x] **Step 1: Write failing live preflight tests**

Add tests in `appV2.4/tests/test_workflow_live_preflight.py`:

```python
def test_live_read_preflight_blocks_missing_configured_transport(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
        available_transports=(),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("github.list_dependabot_alerts",)
    assert report.issues == ("live-read transport is not configured for github.list_dependabot_alerts",)
```

```python
def test_live_draft_preflight_blocks_missing_configured_transport(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
        available_transports=(),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("gmail.create_draft",)
    assert report.issues == ("live-draft transport is not configured for gmail.create_draft",)
```

- [x] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_preflight.py::test_live_read_preflight_blocks_missing_configured_transport \
  appV2.4/tests/test_workflow_live_preflight.py::test_live_draft_preflight_blocks_missing_configured_transport \
  -q
```

Expected: failure because `available_transports` is not accepted.

- [x] **Step 3: Implement optional transport readiness checks**

In `live_preflight.py`:

- import `Collection` from `collections.abc`
- add `available_transports: Collection[str] | None = None` to both public preflight functions
- pass it into `_preflight_live_read_steps` and `_preflight_steps`
- after existing gate checks pass, if `available_transports is not None` and the action is absent, return the relevant missing transport issue

- [x] **Step 4: Verify unit tests pass**

Run the same two tests again.

## Task 2: CLI/TUI Preflight Wiring

- [x] **Step 1: Update command tests to prove env transport readiness**

Update CLI/TUI preflight pass tests so they monkeypatch the relevant registry builder or set live env variables. Add blocked tests for missing transport registry if coverage is not already explicit.

- [x] **Step 2: Verify command tests fail**

Run the updated/new command tests. Expected: pass tests fail until CLI/TUI pass `available_transports`.

- [x] **Step 3: Wire command preflight to env-built registry keys**

In `cli.py` and `interactive_mode.py`:

```python
available_transports=build_live_draft_transports_from_env(os.environ).keys()
available_transports=build_live_read_transports_from_env(os.environ).keys()
```

- [x] **Step 4: Verify command tests pass**

Run the command tests again.

## Task 3: Docs and Verification

- [x] **Step 1: Update live path docs**

Document that preflight now checks configured transport presence but does not call external providers.

- [x] **Step 2: Run focused checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_live_preflight.py \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_live_read_preflight_passes_without_model_or_run \
  appV2.4/tests/test_cli.py::test_cli_plain_workflow_live_draft_preflight_passes_with_env_credentials \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_live_read_preflight_passes_without_run \
  appV2.4/tests/test_tui.py::test_interactive_mode_workflow_live_draft_preflight_passes_with_env_credentials \
  -q
```

- [x] **Step 3: Run broad checks**

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py appV2.4/tests/test_workflow_live_preflight.py appV2.4/tests/test_workflow_live_transports.py appV2.4/tests/test_workflow_live_runner.py -q
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
git diff --check
git status --short | sed 's/^...//' | rg '^(appV2\.3/|appV2\.4/appv24/(agent/|ai/stream\.py|ai/types\.py|ai/validation\.py|compaction/|coding_agent/session_store\.py|app\.py))' || true
git status --short | sed 's/^...//' | rg -v '^(appV2\.4/|docs/appv24/|docs/superpowers/|examples/appv24/)' || true
```
