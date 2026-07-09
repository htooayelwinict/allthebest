# appv24 Workflow Extension MVP Design

Date: 2026-07-09
Status: approved direction from Lewis
Scope: appV2.4 / appv24 workflow profile green-zone

## Summary

appv24 workflow validation, catalog display, and quality gates must grow from
connector and middleware contracts instead of hardcoded workflow semantics. The
workflow runtime remains an orchestrator. Connectors and middleware own their
interfaces. The coding profile may help author a missing connector or middleware
at build time, but production workflow execution must only consume pinned,
validated artifacts.

## MVP Goal

Make the workflow profile extension-ready for test users:

- The user sees a catalog before asking for a workflow.
- Catalog entries can come from built-ins and strict package extension specs.
  Connector actions and middleware interfaces are listed separately so
  middleware remains package-level behavior, not a workflow step action.
- Quality rules that depend on connector behavior are declared as extension
  policy, not embedded as Telegram-specific workflow logic.
- Missing connector or middleware needs can be converted into a PRD prompt and
  QA contract for the coding profile.
- A generated connector or middleware is usable only after its interface,
  tests, manifest, scopes, outputs, and safety policy pass validation.
- QA-required generated connector specs may appear in catalogs and handoff
  prompts before QA passes, but they must not become executable live connector
  contracts until `appv24.extension_qa/v1` evidence validates, including
  `qa.adapter_contract: passed`, `contract.required_scopes`, and
  `contract.allowed_outputs`.
- User-owned connector configuration accepts package or catalog connector IDs,
  so custom connectors do not require new workflow-profile branches just to
  store credential refs, scopes, and connector-specific config.

## Boundary

This MVP does not make the workflow runtime call the coding profile. The coding
profile is a build-time authoring tool only:

```text
user workflow need
-> catalog miss detected
-> extension PRD and coder prompt
-> coding profile creates implementation artifact and tests
-> workflow validator checks extension contract and QA evidence
-> user may include the extension in this package or add it to catalog later
-> workflow runtime executes the pinned interface only
```

No red-zone kernel files are needed. Yellow-zone TUI/CLI work should stay as a
thin command layer after the workflow APIs are stable.

## Architecture

Use Ports and Adapters plus Open/Closed extension metadata:

```text
Workflow package
  connectors.yaml / middlewares.yaml
      -> WorkflowExtensionSpec
          -> capability catalog entries
          -> semantic/quality validation profile
          -> extension PRD/coder prompt

Workflow runtime
  -> validates package
  -> compiles graph
  -> evaluates extension-driven quality rules
  -> calls broker/adapter by action ref
```

The extension layer is declarative. It parses metadata and exposes immutable
spec objects. It does not import generated connector code, call provider APIs,
run shell commands, or launch the coding profile.

## Extension Spec Shape

Package-owned connector actions may declare safe user and validation hints under
`workflow_hints`:

```yaml
workflow_hints:
  catalog_group: Read data
  catalog_label: Lookup CRM contact
  catalog_description: Find a customer record by email.
  input_fields: [email, customer_id]
  required_input_any:
    - [email, customer_id]
  transit_constraints:
    - from: crm.list_subscribers
      to: crm.broadcast_message
      allowed_intermediates:
        - llm.summarize
```

Built-ins can provide the same hints from code so legacy packages do not need to
repeat them. The quality gate consumes generic transit constraints. It does not
know about Telegram registered users directly.

## Coder Profile Authoring Contract

When the user asks for a connector or middleware that is outside the catalog,
the workflow profile emits a build request with:

- kind: `connector` or `middleware`
- business goal
- action or middleware id
- input schema
- output schema
- scopes and env/config requirements
- side-effect class and approval class
- idempotency expectations
- dry-run fixture requirements
- forbidden behavior such as unapproved network, raw secrets, or runtime agent
  calls
- QA commands and expected evidence
- adapter-contract evidence proving the generated connector transport or
  middleware executor satisfies the appv24 runtime interface

This request is suitable to send to the coding profile. The current slice only
generates the PRD/coder prompt artifact; it does not spawn or automate coding
profile sessions.

## Acceptance For This Slice

- Built-in and custom connector workflow hints render in the user capability
  catalog.
- External connector actions without hints still validate, but render with safe
  fallback labels and groups.
- The registered-user Telegram quality gate uses a generic extension transit
  constraint instead of a hardcoded allowed-intermediate constant.
- A package can declare a custom transit constraint in `connectors.yaml`, and
  the quality gate enforces it.
- A build-time PRD planner can generate a concise connector or middleware coder
  prompt without executing the coding profile.
- Extension handoff artifacts include coding-profile command templates and the
  exact QA manifest fields the generated artifact must return. They also record
  `handoff_mode`, `dispatch_status`, and per-extension `qa_return_target`
  metadata so the build-time coding-profile handoff can be tracked without
  letting the workflow runtime call the coding profile.
- Extension handoff artifacts also include a `coding-profile.dispatch.jsonl`
  outbox with queued coding-profile dispatch records. The outbox is a build-time
  handoff queue for operators or future dispatchers; it is not consumed by the
  workflow runtime. The `/workflow-extension-dispatch <handoff-manifest>
  [--mark-dispatched]` command inspects that queue and records operator dispatch
  status without executing the coding profile.
- Each handoff PRD prompt, manifest request, dispatch summary, and dispatch
  outbox record includes the exact `returned-qa/<extension-id>.qa.yaml` path
  where the coding profile must write the `appv24.extension_qa/v1` result.
- Each handoff also writes a build-time `coding-profile.dispatch.sh` helper
  script containing the coding-profile commands. It is explicitly outside the
  workflow runtime boundary and must not be executed by production workflow
  runs.
- A returned coding-profile QA manifest can be applied through the handoff
  manifest only when the package fingerprint still matches, the extension id is
  listed in the handoff, the QA return target exists, and the QA contract
  validates against the declared connector or middleware interface. The
  `/workflow-extension-apply <handoff-manifest> <qa-manifest>` command exposes
  this apply step without letting the workflow runtime call the coding profile.
  On success, the apply report includes the user-facing
  `/workflow-approve <package> happy_path.yaml` command to retry guided
  automation for the package, plus optional commands to promote the QA-passed
  interface to a reusable extension catalog and load that catalog for future
  user prompts.
- Returned coding-profile QA manifests can also be placed in the standard
  `<package>/.workflow-extension-handoff/returned-qa/` inbox. The user-facing
  `/workflow-approve <package> [fixture]` path applies those manifests through
  the handoff before retrying guided preparation or production continuation.
- Coder-built connectors and middleware can attach an `appv24.extension_qa/v1`
  manifest; generated extensions marked `qa_required` must have passing QA
  evidence before semantic validation accepts them as ready. Passing QA must
  include `qa.adapter_contract: passed`, proving the artifact satisfies the
  connector transport or middleware executor interface expected by appv24. For
  connector extensions, passing QA must also echo the exact
  `contract.required_scopes` and `contract.allowed_outputs` from the connector
  action declaration, so live execution cannot trust an implementation tested
  against a different interface.
- Live preflight/readiness uses the same merged built-in plus extension
  connector contracts as the live runner, so a QA-passed production connector
  can be approved and executed after a live-read prefix without adding
  preflight-specific connector code.
- Live planning and user-facing next-command reports classify actions from the
  same merged connector contracts. Built-in actions without shipped transports
  still show transport gaps, while QA-passed extension connectors proceed to
  readiness checks instead of being labeled local-only.
- Connector registry inspection uses the same merged connector contracts, so
  QA-passed extension connectors expose live modes and interface metadata in
  operator-facing reports without adding built-in-specific registry code.
- The user-facing `/workflow-approve <package> [fixture]` path can prepare a
  guided live-read-to-approval run when no pending approval exists yet, then
  use the same command again to approve and continue production. Lower-level
  `/workflow-run-guided` remains available as a debug command.
- Existing validation, quality gate, and CLI tests remain green.
