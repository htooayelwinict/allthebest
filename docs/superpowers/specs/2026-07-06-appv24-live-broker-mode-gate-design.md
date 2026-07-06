# appv24 Live Broker Mode Gate Design

## Problem

Live workflow runners receive an injected `LiveWorkflowBroker`. The broker had an internal mode string, but runners did not verify that the injected broker mode matched the runner mode before invoking transports.

That left a safety gap where `run_live_production_workflow()` could be given a broker constructed for another live mode and still reach a configured transport.

## Design

Validate `LiveWorkflowBroker.mode` at construction, expose it as a read-only contract, and make each live runner verify it after credential preflight and before approval indexing or step execution:

- `run_live_read_workflow()` requires broker mode `live-read`
- `run_live_draft_workflow()` requires broker mode `live-draft`
- `run_live_production_workflow()` requires broker mode `live-production`

Unknown broker modes raise `ValueError` during broker construction. `production_enabled=True` is accepted only with `live-production` mode. These constructor checks prevent misspelled modes or leaked production intent from bypassing live-production's disabled-by-default behavior when broker code is used directly.

If the mode does not match, the run is persisted as blocked, a `broker_preflight_blocked` event is recorded, and no transport is invoked.

When a transport is invoked, its non-blocked `BrokerResult.status` must match the broker mode. A `live-production` broker must not accept a transport result labeled `live-read` or `live-draft`. Mismatches are converted to blocked broker results with no outputs.

Blocked transport results also return no outputs, even if the transport included values that match `allowed_outputs`. The broker preserves the provider id and issues, but does not persist successful-looking output material from failed live calls.

## Scope

Green-zone only:

- `appV2.4/appv24/workflows/live_broker.py`
- `appV2.4/appv24/workflows/live_runner.py`
- `appV2.4/tests/test_workflow_live_runner.py`
- appv24 live-path docs and audit docs

No red-zone files. No provider calls. No CLI/TUI changes.

## Tests

Add regression coverage that:

- `LiveWorkflowBroker` rejects unknown modes at construction.
- `LiveWorkflowBroker` rejects `production_enabled=True` outside `live-production`.
- `LiveWorkflowBroker` blocks non-blocked transport results whose status does not match the broker mode.
- `LiveWorkflowBroker` drops outputs from blocked transport results.
- Each live runner blocks before transport invocation when the injected broker mode is wrong.
