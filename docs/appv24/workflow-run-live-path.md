# appv24 Workflow Run-Live Path

appv24 workflow execution moves through staged modes:

1. `preview`
2. `local`
3. `staged`
4. `credential-preflight`
5. `live-read`
6. `live-draft`
7. `live-production`

Live production is disabled by default.

Before any live mutation, appv24 requires:

- package validation
- compile success
- policy success
- exact capability catalog match
- package fingerprint lock
- credential reference preflight
- explicit approval for current package fingerprint
- idempotency key material
- rollback coverage
- connector action allowlist
- prior dry-run or local run for the same package fingerprint

Normal CI uses fake transports only. Real provider checks are manual-gated.
