# appv24 Workflow Deployment Intent

Deployment intent export is a passive checklist for turning a validated workflow
package into future deployment work.

It does not provision infrastructure, call connector APIs, send messages, read
secrets, start webhook servers, or create cloud resources.

## Command

```text
/workflow-export-intent <package>
```

The command validates, compiles, and policy-checks the package before printing:

- env requirements such as `APPV24_GMAIL_CREDENTIAL_REF`
- scope env requirements such as `APPV24_GMAIL_SCOPES`
- secret reference names
- webhook ingress ids
- schedule ids
- connector scopes
- approval policy by approval gate
- rollback requirement

## Boundary

This is still before live connector execution. The export is useful for planning
secrets, scopes, webhook ingress, scheduler configuration, and approval policy,
but the appv24 broker remains the enforcement boundary for any future live run.
