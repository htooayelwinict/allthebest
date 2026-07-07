# appv24 Workflow Deployment Intent

Deployment intent export is a passive checklist for turning a validated workflow
package into future deployment work.

It does not provision infrastructure, call connector APIs, send messages, read
secrets, start webhook servers, install services, or create cloud resources.

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
- local runtime command hints
- rollback requirement

Local runtime commands are hints only. They are emitted when package triggers
need the local runtime surfaces already implemented by appv24:

```text
/workflow-activation-loop <ticks> [interval-seconds]
/workflow-webhook-serve <max-requests> [host] [port]
```

They assume activation records have already been created. The deployment intent
does not create activations or start those commands.

## Local Service Export

Bundle export turns these local runtime command hints into passive service
templates when a package has `schedule`, `poll`, or `webhook` triggers:

```text
services.manifest.json
services/run-scheduler.sh
services/run-webhook.sh
services.systemd/*.service
services.launchd/*.plist
```

These files are operator handoff artifacts only. appv24 does not install them,
enable them, start them, or write secret values into them. The generated shell
scripts load `.env` by default, allow `APPV24_WORKFLOW_ENV_FILE` override, and
require `APPV24_WORKFLOW_WEBHOOK_SECRET` for webhook service templates.

## Boundary

This is still before live connector execution. The export is useful for planning
secrets, scopes, webhook ingress, scheduler configuration, and approval policy,
but the appv24 broker remains the enforcement boundary for any future live run.
