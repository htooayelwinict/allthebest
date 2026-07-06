# appv24 Workflow Approval Prep Command Implementation Plan

**Goal:** Add `/workflow-run-approval` so operators can create pending approval requests from CLI/TUI before live-draft or live-production.

**Risk:** Medium-low. Uses existing green-zone runner behavior. Yellow-zone command wiring requires tests.

## Task 1: Failing Tests

Files:

- Modify: `appV2.4/tests/test_cli.py`
- Modify: `appV2.4/tests/test_tui.py`

- [x] Test CLI `/workflow-run-approval <package> [fixture]` creates one pending approval request without running the model.
- [x] Test TUI `/workflow-run-approval <package> [fixture]` creates one pending approval request without running the model.
- [x] Verify existing `/workflow-run-local` auto-approved behavior still passes.

## Task 2: Command Wiring

Files:

- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`

- [x] Add parser for `/workflow-run-approval <package> [fixture]`.
- [x] Route CLI command before model execution.
- [x] Route TUI command before model execution.
- [x] Render report label as `Workflow approval prep run`.
- [x] Add help/autocomplete entries.

## Task 3: Docs And Verification

Files:

- Modify: `docs/appv24/workflow-run-live-path.md`
- Modify: `docs/appv24/plan-implementation-audit.md`

- [x] Document the approval prep command in the live path.
- [x] Run focused CLI/TUI tests.
- [x] Run full `appV2.4/tests`.
- [x] Run `git diff --check`.
- [x] Confirm red-zone/appv23/appv231 paths are clean.
