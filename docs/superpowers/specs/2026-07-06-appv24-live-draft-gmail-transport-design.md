# appv24 Live-Draft Gmail Transport Design

## Intent

Make `/workflow-run-live-draft` capable of the first real draft-only external action: creating an unsent Gmail draft after validation, policy, credential preflight, approval ledger, idempotency, catalog eligibility, and connector allowlist gates pass.

## Boundary

This is a live-draft v0 slice only.

- Enabled only when `APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true`.
- Starts with `gmail.create_draft`, already modeled as a draft-eligible mutating action.
- Creates Gmail drafts only.
- Does not send Gmail messages.
- Does not add live-production transports.
- Does not change workflow schemas, agent loop, provider streaming, compaction, session storage, or app kernel files.

Google's current Gmail API docs define draft creation as `POST https://gmail.googleapis.com/gmail/v1/users/me/drafts` with request body `{"message": {"raw": "<base64url RFC 2822 MIME>"}}`. The response includes the draft `id` and message metadata such as `threadId`.

## Operator Configuration

Required for live-draft transport registration:

```text
APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true
APPV24_GMAIL_ACCESS_TOKEN=<oauth-access-token>
```

Optional for tests or controlled gateways:

```text
APPV24_GMAIL_API_BASE_URL=<base-url>
```

Existing credential preflight remains separate and still requires:

```text
APPV24_GMAIL_CREDENTIAL_REF=<opaque-reference>
APPV24_GMAIL_SCOPES=gmail.compose
```

The OAuth token is read only inside the transport registry. It is never included in run metadata, events, traces, reports, errors, or terminal output.

## Workflow Input Contract

For `gmail.create_draft`, the transport resolves draft fields from `steps[].with` first, then from the selected fixture's `input` map.

Required:

```text
to or recipient
subject
body
```

Optional:

```text
from
cc
bcc
thread_id
```

The transport builds the RFC 2822 MIME message with Python's standard `email.message.EmailMessage`, base64URL encodes it, and sends it to Gmail `drafts.create`.

## Components

Extend `appV2.4/appv24/workflows/live_transports.py`.

Responsibilities:

- Add `build_live_draft_transports_from_env`.
- Keep disabled-by-default behavior.
- Provide `GmailCreateDraftTransport`.
- Return only safe broker outputs:
  - `draft_id`
  - `thread_id`
- Return `BrokerResult(status="blocked")` for missing fields, HTTP failures, malformed JSON, or unexpected response shape.

Update CLI/TUI live-draft command adapters only:

- Replace `LiveWorkflowBroker({}, mode="live-draft")` with `LiveWorkflowBroker(build_live_draft_transports_from_env(os.environ), mode="live-draft")`.
- Leave live-production disabled.

## Testing

Automated tests must not call Gmail.

Tests cover:

- registry disabled by default
- registry enabled only with flag and access token
- Gmail transport URL, headers, JSON body, MIME encoding, and output slimming through a fake HTTP client
- blocked missing required draft fields without HTTP invocation
- CLI live-draft command uses the registry and can pass with a fake transport
- TUI live-draft command uses the registry and can pass with a fake transport
- existing no-transport tests still pass when the enable flag is absent

## Zone Impact

Green-zone:

- `appV2.4/appv24/workflows/live_transports.py`
- workflow tests
- docs/specs/plans

Yellow-zone:

- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`

Red-zone:

- No changes.
