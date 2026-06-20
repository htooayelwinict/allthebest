# Phase 155: Pi TUI Ctrl+B/Ctrl+F Cursor Navigation

## Goal

Port Pi's Ctrl+B and Ctrl+F editor keybindings into appv22's `Input` component so keyboard navigation matches Pi's `tui.editor.cursorLeft` and `tui.editor.cursorRight` defaults.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/keybindings.ts`
  - `tui.editor.cursorLeft` default keys include `left` and `ctrl+b`
  - `tui.editor.cursorRight` default keys include `right` and `ctrl+f`
- `appV2.2/appv22/tui/component.py`
  - appv22 already handled left/right arrow escape sequences

## Red Test

Added `test_input_ports_pi_ctrl_b_ctrl_f_cursor_navigation`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_ctrl_b_ctrl_f_cursor_navigation -q
```

Result: failed as expected because appv22 ignored Ctrl+B and left the cursor at position `5` instead of moving to `4`.

## Implementation

- Routed raw Ctrl+B (`\x02`) to single-character cursor-left movement.
- Routed raw Ctrl+F (`\x06`) to single-character cursor-right movement.
- Clamped both movements at line boundaries.
- Reset the input action chain on cursor movement, matching the existing arrow-key behavior.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_ctrl_b_ctrl_f_cursor_navigation -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `62 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall -q appV2.2/appv22` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `318 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, richer editor behavior, and full undo coverage for every input edit path.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
