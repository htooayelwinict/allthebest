# appv24 Gmail OAuth Refresh Transport Design

## Intent

Make `gmail.create_draft` live-draft runs usable beyond a single short-lived access token by allowing the Gmail transport to refresh an access token at run time from operator-provided OAuth refresh credentials.

## Boundary

This is a runtime credential-refresh slice only.

- Supports Gmail live-draft transport only.
- Does not implement browser OAuth consent.
- Does not store tokens.
- Does not print tokens, client secrets, refresh tokens, or raw OAuth responses.
- Does not add live-production.
- Does not change workflow schemas, agent loop, provider streaming, compaction, session storage, or app kernel files.

Google's OAuth docs describe server-side apps using access tokens in `Authorization: Bearer` headers, obtaining offline access with refresh tokens, and refreshing via HTTPS token endpoints with `grant_type=refresh_token`. Google documents the token host as `oauth2.googleapis.com` and the token endpoint as `/token`.

## Operator Configuration

Existing static access-token mode remains supported:

```text
APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true
APPV24_GMAIL_ACCESS_TOKEN=<oauth-access-token>
```

New refresh-token mode registers the same `gmail.create_draft` transport when all required refresh fields are present:

```text
APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true
APPV24_GMAIL_REFRESH_TOKEN=<oauth-refresh-token>
APPV24_GMAIL_CLIENT_ID=<oauth-client-id>
APPV24_GMAIL_CLIENT_SECRET=<oauth-client-secret>
```

Optional:

```text
APPV24_GOOGLE_OAUTH_TOKEN_URL=https://oauth2.googleapis.com/token
APPV24_GMAIL_API_BASE_URL=https://gmail.googleapis.com/gmail/v1
```

If both static access token and refresh credentials are present, static access token wins. This avoids a token refresh call when an operator explicitly supplies a ready bearer token.

## Runtime Flow

At live-draft invocation time:

1. Resolve Gmail draft fields.
2. Resolve bearer token:
   - static token returns immediately
   - refresh provider posts form data to token endpoint
3. Build RFC 2822 MIME message.
4. POST Gmail `users/me/drafts`.
5. Return only `draft_id` and `thread_id`.

The refresh request uses form data:

```text
client_id
client_secret
refresh_token
grant_type=refresh_token
```

The transport treats malformed token responses, HTTP failures, and request errors as blocked broker results. Issues must identify the failure class without including secrets or raw provider payloads.

## Testing

Automated tests must not call Google.

Tests cover:

- registry registers Gmail with refresh credentials when no access token is present
- registry stays disabled when refresh credentials are incomplete
- token refresh POST shape and then Gmail draft POST shape with a fake HTTP client
- malformed refresh response blocks before Gmail draft POST
- no token, refresh token, or client secret appears in `BrokerResult.outputs` or issues

## Zone Impact

Green-zone:

- `appV2.4/appv24/workflows/live_transports.py`
- workflow transport tests
- docs/specs/plans

Yellow-zone:

- None expected.

Red-zone:

- No changes.
