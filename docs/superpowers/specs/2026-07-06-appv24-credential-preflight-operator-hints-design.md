# appv24 Credential Preflight Operator Hints Design

## Intent

Make live workflow credential failures actionable without exposing secret material. Operators should see which appv24 credential-reference environment variables and scopes are required before a live-read, live-draft, or live-production run can proceed.

## Boundary

Diagnostics only:

- No provider calls.
- No raw credential values.
- No token validation.
- No new connectors.
- No CLI/TUI command changes.
- No appv24 red-zone files.

## Design

Keep `CredentialRefResolver` unchanged. Update `preflight_credential_refs()` in `appV2.4/appv24/workflows/credential_refs.py` so its issue strings include safe hints:

```text
missing credential ref for telegram; required env: APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES=telegram:send
credential for telegram does not grant telegram:send; required env: APPV24_TELEGRAM_SCOPES=telegram:send
wildcard credential scope is not allowed for ticket; configure APPV24_TICKET_SCOPES without *
```

Scope hints come from the package's `permissions.yaml`. Connector ids keep the existing `credential_env_name()` mapping.

## Testing

Tests prove:

- missing connector credential reports the credential ref env name and requested scopes.
- missing scope reports the scopes env name and required scope.
- wildcard scope reports the scopes env name and never asks for `*`.
- resolver still does not expose raw credential values.

