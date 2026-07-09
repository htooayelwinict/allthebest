# appv24 Workflow Extension Build Executor Design

## Goal

Let an operator continue from a generated workflow package that is blocked on custom connector or middleware QA without manually copying the coding-profile prompt. The workflow profile remains responsible for package validation and live execution; the extension builder remains a build-time tool that returns a pinned `appv24.extension_qa/v1` manifest.

## Architecture

The executor is a workflow green-zone helper that consumes `.workflow-extension-handoff/extension-handoff.manifest.json` and `coding-profile.dispatch.jsonl`. It does not execute the stored shell command string. Its default MVP path reads the connector interface contract, writes package-local connector scaffold files, runs local scaffold tests, writes the expected returned-QA file, applies the QA with the existing `apply_returned_extension_handoff_results()` path, then validates the package again. The coding-profile subprocess runner remains available as an optional implementation path, but it is not the default because live testing showed it can hang or write malformed partial files without returned QA.

The workflow runtime does not import or call coding-profile internals. Runtime only sees package documents, connector contracts, extension QA evidence, approval state, and live transport readiness.

The preferred child-to-parent return channel is a structured stdout artifact bundle. The coding profile prints one JSON payload between `APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_BEGIN` and `APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_END`. The workflow parent validates the bundle version, extension id, kind, package-local file paths, extension-root allowlist, Python syntax, and QA manifest ownership before it writes any artifact. Direct returned-QA files remain a fallback, but generated source files should be applied by the workflow parent from the bundle, not free-written by the child.

When the optional coding-profile subprocess runner is used, the parent runs each build with a bounded timeout. The default is 180 seconds and can be adjusted with `APPV24_WORKFLOW_EXTENSION_BUILD_TIMEOUT_SECONDS`. The executor emits progress events for start, timeout, failure, bundle rejection, missing QA, built, and finished states so CLI/TUI users can see that build-time work is active. Timeout and failure states write redacted logs, keep the package blocked, and never promote partial artifacts.

## Interfaces

- `build_workflow_extension_handoff(package_or_manifest, runner=..., timeout_seconds=..., progress=...)`
  - Resolves either a package path or handoff manifest path.
  - Reads dispatch records and prompt files.
  - Runs each queued extension request through the default contract scaffold builder or an injectable runner.
  - Emits optional progress events.
  - Writes redacted build output under `.workflow-extension-handoff/build-logs/`.
  - Applies returned QA manifests from `.workflow-extension-handoff/returned-qa/`.
  - Revalidates the workflow package.
- `/workflow-extension-build <package-or-handoff-manifest>`
  - CLI and TUI operator command.
  - Prints a concise report with built requests, applied QA, validation status, and next command.

## Invariants

- No shell execution of manifest command strings.
- No runtime call into the coding profile.
- Package fingerprint must still match before returned QA is applied.
- Returned QA must pass existing schema, side-effect, runtime-boundary, adapter-contract, scope, output, and fingerprint checks.
- Logs must redact likely secret values and avoid printing raw environment contents.
- Timeout must fail closed: no partial artifact promotion, no runtime unblock, and validation remains blocked until QA evidence is present.
- The command can be tested with a fake runner that writes a QA manifest; tests do not call a live model.
- Artifact bundles must fail closed on missing markers, invalid JSON, absolute paths, parent traversal, writes outside the extension artifact root, invalid Python syntax, or QA manifest mismatch.

## MVP Behavior

When the TUI-generated package stops with `extension_qa_missing`, the operator can run:

```text
/workflow-extension-build <package>
```

The default MVP builder writes package-local connector scaffold artifacts, runs their local tests, writes returned QA, attaches it, and the package moves to normal validation, approval, and live execution. If an optional coding-profile runner returns a valid artifact bundle, the executor writes the package-local artifacts, writes returned QA, and uses the same apply path. If the build times out, fails, or neither valid bundle nor returned QA exists, the executor reports the issue, shows the log path, and leaves the workflow blocked.

## Out Of Scope

- Background daemon or watcher.
- Runtime-generated code.
- Reusable catalog promotion by default.
- npm, GHCR, or release packaging.
