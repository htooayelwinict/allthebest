# appv24 Workflow Live Readiness Implementation Plan

**Goal:** Add a no-side-effect live readiness report that helps operators prepare actual workflow runs without changing existing preflight/run semantics.

**Risk:** Low to medium. Workflow module changes are green-zone. CLI/TUI wiring is yellow-zone and must stay narrow with tests.

## Task 1: Failing Tests

Files:

- Add: `appV2.4/tests/test_workflow_live_readiness.py`
- Modify: `appV2.4/tests/test_cli.py`
- Modify: `appV2.4/tests/test_tui.py`

- [x] Test live-read readiness reports missing credential and transport env hints.
- [x] Test live-production readiness reports missing approval-run and Telegram transport hints.
- [x] Test live-read readiness passes with configured env transport and credential reference.
- [x] Test CLI command renders readiness without running the model.
- [x] Test TUI command renders readiness without creating run artifacts.

## Task 2: Readiness Module

Files:

- Add: `appV2.4/appv24/workflows/live_readiness.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`

- [x] Add immutable readiness dataclasses.
- [x] Resolve mode-specific request and env-gated transport registry.
- [x] Reuse existing preflight functions.
- [x] Collect safe credential and transport env hints.
- [x] Format a concise operator report.
- [x] Ensure no raw env values are printed.

## Task 3: Command Wiring

Files:

- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`

- [x] Add `/workflow-live-ready` parser.
- [x] Route CLI command before model execution.
- [x] Route TUI command before model execution.
- [x] Add command palette/help entries.

## Task 4: Docs And Verification

Files:

- Modify: `docs/appv24/workflow-run-live-path.md`
- Modify: `docs/appv24/plan-implementation-audit.md`

- [x] Document the readiness command.
- [x] Run focused readiness/CLI/TUI tests.
- [x] Run full `appV2.4/tests`.
- [x] Run `git diff --check`.
- [x] Confirm red-zone/appv23/appv231 paths are clean.
