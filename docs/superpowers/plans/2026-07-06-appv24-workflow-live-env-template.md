# appv24 Workflow Live Env Template Implementation Plan

**Goal:** Add a safe env-template command that helps operators configure live workflow credentials and transport gates.

**Risk:** Low to medium. Core work is green-zone. CLI/TUI wiring is yellow-zone and must stay narrow with tests.

## Task 1: Failing Tests

Files:

- Add: `appV2.4/tests/test_workflow_live_env_template.py`
- Modify: `appV2.4/tests/test_cli.py`
- Modify: `appV2.4/tests/test_tui.py`

- [x] Test live-read env template includes GitHub placeholders.
- [x] Test live-draft env template includes Gmail access and refresh alternatives.
- [x] Test live-production env template includes Telegram placeholders.
- [x] Test unknown allowlisted action renders a safe note.
- [x] Test CLI command does not run the model or create run artifacts.
- [x] Test TUI command does not create run artifacts.

## Task 2: Env Template Module

Files:

- Add: `appV2.4/appv24/workflows/live_env_template.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`

- [x] Add immutable template dataclasses.
- [x] Load package permissions and workflow name.
- [x] Render credential-reference variables from package permissions.
- [x] Render built-in transport variables from mode and allowlist.
- [x] Render unknown-action notes without inventing env names.
- [x] Ensure placeholders only, no raw env values.

## Task 3: Command Wiring

Files:

- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`

- [x] Add `/workflow-live-env-template` parser.
- [x] Route CLI command before model execution.
- [x] Route TUI command before model execution.
- [x] Add help/autocomplete entries.

## Task 4: Docs And Verification

Files:

- Modify: `docs/appv24/workflow-run-live-path.md`
- Modify: `docs/appv24/plan-implementation-audit.md`

- [x] Document the env-template command.
- [x] Run focused env-template/CLI/TUI tests.
- [x] Run full `appV2.4/tests`.
- [x] Run `git diff --check`.
- [x] Confirm red-zone/appv23/appv231 paths are clean.
