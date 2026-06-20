# Phase 156: Pi TUI Alt+Delete Delete Word Forward

## Goal

Port Pi's Alt+Delete editor keybinding into appv22's `Input` component so both default `tui.editor.deleteWordForward` bindings are available: Alt+D and Alt+Delete.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/keybindings.ts`
  - `tui.editor.deleteWordForward` default keys include `alt+d` and `alt+delete`
- `pi/packages/tui/src/keys.ts`
  - parses modified functional CSI sequences such as `\x1b[3;3~`
  - accepts optional Kitty event suffixes such as `\x1b[3;3:1~`
- `appV2.2/appv22/tui/component.py`
  - appv22 already handled Alt+D through `_delete_word_forward()`

## Red Test

Added `test_input_ports_pi_alt_delete_delete_word_forward_keybinding`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_delete_delete_word_forward_keybinding -q
```

Result: failed as expected because appv22 inserted the raw CSI tail into the input, producing `[3;3~hello world`.

## Implementation

- Routed `\x1b[3;3~` and event-suffixed `\x1b[3;3:<event>~` forms to `_delete_word_forward()`.
- Reused the existing forward-word delete and kill-ring behavior from Alt+D.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_delete_delete_word_forward_keybinding -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `63 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall -q appV2.2/appv22` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `319 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, richer editor behavior, and full undo coverage for every input edit path.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
