# appv24 Idempotency inputRef Resolution Design

## Problem

Workflow approval matching uses idempotency keys to prove that a previously approved broker action is the same action being executed later. Current key material is built from raw `steps[].with` values. When a key field is declared as `{"inputRef": "chat_id"}`, different fixtures can produce the same idempotency key because the unresolved reference object is identical.

## Design

Idempotency key construction should optionally accept the selected fixture. For each configured key field, if the raw `with` value is an `inputRef`, the key material should use `fixture.input[ref]` instead of the raw reference object. Non-reference values keep their existing behavior. Missing fixture values resolve to `None`, preserving deterministic behavior without logging user data.

The hash remains opaque and no resolved value is written directly to approval records or reports. Local dry-run approval creation, live preflight approval matching, and live execution broker previews must all pass the selected fixture into the same helper so their keys agree.

## Scope

Green-zone only:

- `appV2.4/appv24/workflows/idempotency.py`
- `appV2.4/appv24/workflows/runner.py`
- `appV2.4/appv24/workflows/live_runner.py`
- `appV2.4/appv24/workflows/live_preflight.py`
- focused workflow tests and docs

No red-zone files. No external side effects. No connector execution changes.

## Tests

- Unit test: two fixtures with different resolved `inputRef` values produce different idempotency keys.
- Regression test: a live-production preflight approved for `happy_path.yaml` must block when run against a different fixture with different resolved idempotency fields.
- Focused test command, then full appv24 pytest suite.
