# Workflow Guided User Flow

The workflow profile keeps developer/debug commands, but the test-user path is
short:

```text
/workflow-checkin
<natural-language workflow prompt>
/workflow-approve <generated-package> [fixture]
```

`/workflow-checkin` is the user-facing alias for `/workflow-catalog`; both
commands show the capability catalog and arm guided automation for the next
valid workflow draft. After checkin, the user describes the workflow in plain
language. After appv24 saves the generated package, the user runs the printed
`/workflow-approve` command to approve and continue. The catalog separates
executable connector actions from middleware interfaces, so middleware can be
selected as package-level behavior without pretending it is a workflow step.

Use `/workflow-checkin <extension-catalog-path>` or
`/workflow-catalog <extension-catalog-path>` when the user wants promoted
reusable connector or middleware interfaces to appear beside built-in
capabilities. In the same app session, that loaded catalog also becomes prompt
context for the next natural-language workflow generation; `/workflow-catalog`
or `/workflow-checkin` without a path returns the session to built-in
capabilities only. Showing the catalog also arms the next valid workflow draft
for guided automation: appv24
chooses a package name from `workflow.yaml` `name`, saves the package, runs
validation and the quality gate, executes live-read phases, and stops at the
external side-effect approval.

For the AI-news-to-registered-Telegram scenario, generated packages should bind
the connector interfaces with appv24 references only: `items: "{{ steps.search_ai_news.outputs.items }}"`,
`recipients: "{{ steps.list_subscribers.outputs.recipients }}"`,
`audience_hash: "{{ steps.list_subscribers.outputs.audience_hash }}"`, and
`text: "{{ steps.summarize_news.outputs.summary }}"`. Do not use GitHub-style
`${{ ... }}` references.
Use only the minimal package files for this path: `workflow.yaml`,
`connectors.yaml`, `permissions.yaml`, `dry_run_fixtures/happy_path.yaml`, and
`rollback.md`. Add extra fixtures only when the workflow models a real alternate
input, decision branch, or failure path.

After the guided automation saves a package, it prints the next approval command
with the generated package name. `/workflow-automate <package> [fixture]` remains
available as an operator override/debug command when a fixed package path is
needed.

If the generated workflow is structurally complete but requires a new
coding-profile connector or middleware, automation saves the package and writes
a build-time handoff instead of running live phases. The package remains blocked
until the generated extension returns passing `appv24.extension_qa/v1` evidence:

```text
<generated-package>/.workflow-extension-handoff/extension-handoff.manifest.json
<generated-package>/.workflow-extension-handoff/<kind>-<extension-id>.prd.md
<generated-package>/.workflow-extension-handoff/coding-profile.dispatch.jsonl
<generated-package>/.workflow-extension-handoff/coding-profile.dispatch.sh
<generated-package>/.workflow-extension-handoff/returned-qa/<extension-id>.qa.yaml
```

The user-facing build command is:

```text
/workflow-extension-build <generated-package>
```

It runs build-time extension authoring only, shows progress for each extension
request, prints the redacted build log path, writes package-local
connector scaffold files, runs local scaffold tests, writes returned QA, and
applies that QA back to the package. If an optional coding-profile subprocess
runner is used, `APPV24_WORKFLOW_EXTENSION_BUILD_TIMEOUT_SECONDS` enforces its
timeout with a 180 second default. If a build times out or returns no valid QA,
the workflow package stays blocked and no partial connector artifacts are
promoted.

`/workflow-approve <generated-package> [fixture]` picks the latest pending
guided approval for the current package fingerprint, records the approval,
checks production readiness, and continues the approved live-production phase.
If no pending guided approval exists yet, the same command prepares the guided
live-read-to-approval phase and stops at approval, so test users do not need the
debug-only `/workflow-run-guided` command for the normal path.
When guided automation pauses at approval, the report prints the exact
`/workflow-approve <generated-package> <fixture>` command to run next.
The approval ledger, credential checks, connector allowlist, idempotency, target
policy, and live transport gates still apply.

Use `/workflow-save`, `/workflow-run-guided`, `/workflow-approve <package>
<approval-run-id> <approval-request-id> --guided [fixture]`,
`/workflow-continue-guided`, and the lower-level live/readiness commands for
debugging only.

## Missing Capabilities

If the requested workflow needs a connector or middleware that is not in the
catalog, the workflow profile should produce an extension PRD for the coding
profile instead of inventing runtime behavior.

When `/workflow-run-guided` is blocked because a package-declared generated
connector or middleware is missing QA evidence, the guided report includes the
extension PRD prompt that can be sent to the coding profile. After the coding
profile returns the implementation and an `appv24.extension_qa/v1` manifest, the
same package can be validated and run again.

The same blocked guided run writes a coding-profile handoff artifact at:

```text
<package>/.workflow-extension-handoff/extension-handoff.manifest.json
```

That manifest points at PRD prompt files in the same directory, records the
package fingerprint, sets `target_profile: coding`, and keeps the workflow
runtime out of the authoring path. It also records `handoff_mode:
coding-profile-command`, `dispatch_status: pending_coding_profile`, a
`qa_return_target` for each extension so the generated QA manifest is attached
back to the correct connector action or middleware entry, and a
`qa_return_manifest_path` under `returned-qa/` for the coding profile to write.
The PRD prompt, dispatch summary, and `coding-profile.dispatch.jsonl` outbox all
carry the same returned-QA path. The handoff also writes a build-time
`coding-profile.dispatch.sh` helper script with the same coding-profile commands;
workflow runtime must not execute that script. Operators can inspect the outbox with
`/workflow-extension-dispatch <handoff-manifest>` and mark it dispatched after
sending with `/workflow-extension-dispatch <handoff-manifest> --mark-dispatched`.
The guided handoff report also prints `QA return manifest paths:` so the coding
profile can write the passed QA manifest into the exact inbox path that
`/workflow-approve` reads.
The dispatch report prints the outbox, helper script, PRD prompt path, exact
`qa_return_target`, and `qa_return_manifest_path` expected from the coding
profile.
The outbox is a build-time handoff queue; workflow execution never consumes it.
The handoff also records the required QA manifest fields:

```text
version: appv24.extension_qa/v1
status: passed
qa.schema_validation: passed
qa.side_effect_policy: passed
qa.runtime_boundary: passed
qa.adapter_contract: passed
contract.required_scopes
contract.allowed_outputs
fingerprints.spec / implementation / tests / fixtures
```

Fingerprint values must use `sha256:<64 lowercase hex characters>`. Placeholders
such as `sha256:spec` are rejected by the extension QA validator.

After the coding profile returns a package-declared connector or middleware with
passing QA evidence, appv24 can apply that QA manifest through the handoff
manifest. The apply step checks the package fingerprint, extension id,
`qa_return_target`, and QA contract before writing the QA block back into
`connectors.yaml` or `middlewares.yaml`. A successful apply report prints the
user-facing `/workflow-approve <package> happy_path.yaml` command to retry
guided automation for the package, plus optional commands to promote that
QA-passed interface into a reusable extension catalog and load the catalog for
future prompts. The catalog stores connector/middleware metadata and QA-safe
interface hints, not secret values or runtime sessions. Loading that catalog
feeds the same user-facing capability catalog as built-ins and package-local
extensions.

For the user-facing approval path, returned coding-profile QA manifests can be
placed in `<package>/.workflow-extension-handoff/returned-qa/`. When the user
runs `/workflow-approve <package> [fixture]`, appv24 applies those returned QA
manifests through the handoff manifest before retrying guided preparation or
production continuation. The returned-QA report then shows the catalog choice:
keep the extension package-only with no command, or explicitly promote it with
`/workflow-extension-promote` and reload it for later prompts with
`/workflow-checkin`.

For live execution, generated connector actions must also declare explicit
`live_modes` such as `live-read` or `live-production`. The workflow live runner
adapts QA-passed extension metadata into connector contracts, so approval,
idempotency, allowlist, output filtering, and transport gates apply the same way
as built-ins. A matching live transport still has to be supplied by operator
configuration or an installed connector plugin before the action can run.

```text
/workflow-extension-apply <handoff-manifest> <qa-manifest>
/workflow-extension-dispatch <handoff-manifest> [--mark-dispatched]
/workflow-extension-promote <package> <extension-catalog-path> [extension-id...]
/workflow-checkin <extension-catalog-path>
```

The generated extension PRD states whether the extension is:

- `package-only`: usable by this package after QA, not promoted to the reusable
  catalog.
- `reusable-catalog-candidate`: includes stable catalog label, group, source,
  and QA evidence so the user can promote it for later workflows.

Generated connectors and middleware must attach an `appv24.extension_qa/v1`
manifest with passed schema validation, tests, side-effect policy,
runtime-boundary checks, and fingerprints before the workflow validator accepts
them as ready.
