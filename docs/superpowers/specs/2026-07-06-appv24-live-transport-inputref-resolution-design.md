# appv24 Live Transport inputRef Resolution Design

## Problem

Workflow packages naturally use `steps[].with` values such as `{"inputRef": "customer_email"}` to bind fixture or runtime inputs to connector fields. The live transport docs say live transports resolve fields from `steps[].with` first and then from fixture input. Current Gmail and Telegram live transports only accept literal strings in `steps[].with`; they do not resolve arbitrary `inputRef` objects there.

This can block valid workflow packages during live-draft or live-production even after validation, approval, credential, allowlist, and idempotency gates pass.

## Design

Add a shared value resolver inside `appV2.4/appv24/workflows/live_transports.py`:

- Literal strings in `steps[].with` remain first priority.
- `{"inputRef": "<name>"}` in `steps[].with` resolves to `request.fixture.input[<name>]`.
- If the resolved value is not a string, required string fields remain missing.
- Boolean fields support literal booleans and boolean `inputRef` values.
- GitHub `per_page` supports literal numeric values and numeric/string `inputRef` values.
- Existing fallback to same-name fixture fields remains for packages that omit `steps[].with`.

This keeps the behavior local to live transport payload construction. It does not change package schema, policy, approval records, credential handling, broker filtering, or red/yellow-zone files.

## Scope

Green-zone only:

- `appV2.4/appv24/workflows/live_transports.py`
- `appV2.4/tests/test_workflow_live_transports.py`
- `docs/appv24/workflow-run-live-path.md`

No red-zone files. No TUI/CLI changes. No external provider calls in tests.

## Tests

- GitHub live-read transport resolves repository and query filters from arbitrary `inputRef` names and calls the fake client with the expected URL and params.
- Gmail live-draft transport resolves `to`, `subject`, and `body` from arbitrary `inputRef` names and posts the expected encoded MIME payload to the fake client.
- Telegram live-production transport resolves `chat_id`, `text`, and optional boolean flags from arbitrary `inputRef` names and posts the expected fake-provider payload.
- Existing transport tests continue to pass.
