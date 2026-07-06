# appv24 Live Run Explain Approval Provenance Design

## Intent

Make live-production run explanations trace a successful production action back to the prior approved local run. The run manifest already records `approval_run_id`, but `format_workflow_run_explanation()` does not expose it through provenance.

## Boundary

Diagnostics only:

- No live execution behavior change.
- No provider calls.
- No secret values or broker outputs added to explanations.
- No CLI/TUI command routing changes.
- No appv24 red-zone files.

## Design

Update `appV2.4/appv24/workflows/explain.py` so `_provenance()` emits:

```text
approval run: <approval_run_id>
```

when `run.json` contains an approval run id.

The existing explanation still shows mode, fixture, package, approval mode, connector allowlist, activation id, and trigger when present. Brokered action output stays compact and does not render provider outputs.

## Testing

Add a live-production run explanation test that:

- creates a local approval run.
- records approval.
- runs a fake `telegram.send_message` live-production transport.
- explains the live run.
- asserts provenance includes `mode: live-production`, `approval run: <id>`, and allowlist.
- asserts filtered provider output such as `secret` is not rendered.

