# appv24 Workflow Draft Validation Design

Date: 2026-07-03
Status: proposed design
Scope: appV2.4 / appv24 only

## Summary

The next appv24 workflow phase should connect the existing offline workflow package validator to the live `workflow-designer-lite` authoring experience.

Today the profile can draft workflow package text, and `appv24.workflows.validate_workflow_package(path)` can validate package files on disk. The missing bridge is deterministic validation of the LLM draft before the user treats it as usable.

The target flow is:

```text
user asks for workflow
  -> workflow-designer-lite drafts labeled package blocks
  -> appv24 extracts workflow package blocks from the assistant text
  -> appv24 parses YAML safely in memory
  -> appv24 validates schema and semantics in memory
  -> TUI/plain CLI renders validation status
  -> user may explicitly save the last valid draft to disk
```

This is authoring and validation only. It does not run workflows, call APIs, send messages, provision cloud resources, request secrets, or mutate external systems.

## Source Basis

`rules.md` says new use cases belong in profiles, apps, docs, examples, tests, and packages rather than the runtime kernel.

`workflow-designer-lite` already uses read-only tools and explicitly says designs are passive artifacts in the final response only.

`appV2.4/appv24/workflows/` already contains package loading, JSON Schema validation, semantic validation, issue types, and a public path-based validator.

Context7 docs confirmed the current library approach:

- PyYAML `safe_load` is appropriate for untrusted YAML because it constructs simple Python objects through `SafeLoader`.
- `jsonschema.Draft202012Validator.check_schema()` validates schemas against the Draft 2020-12 meta-schema.
- `Draft202012Validator(...).iter_errors(instance)` collects validation errors and exposes instance paths through `ValidationError.path`.

Open Bridge architecture review agreed with keeping draft validation as a passive compiler/validation chain outside agent loop, session store, provider streaming, and compaction.

## Non-Goals

Do not implement workflow execution.

Do not implement automatic API connectors.

Do not implement cloud runners, IaC apply, Terraform/Pulumi preview, or scheduling.

Do not add workflow semantics to `agent_loop.py`, `AgentSession`, provider streaming, compaction, or `app.py`.

Do not let the model write package files by tool call. Saving must be an explicit user command handled by appv24 after validation.

Do not auto-repair in this first live-validation phase. Invalid drafts should produce clear validation issues and a user-visible repair prompt suggestion. A later phase can add a one-pass repair loop if the validation contract proves stable.

## Design

### 1. Prompt Contract

Update the `workflow-designer-lite` profile prompt so generated package artifacts are easy to extract.

The profile should ask for labeled fenced blocks:

````markdown
```yaml workflow.yaml
...
```

```yaml connectors.yaml
...
```

```yaml permissions.yaml
...
```

```yaml dry_run_fixtures/happy_path.yaml
...
```

```yaml dry_run_fixtures/rejection_path.yaml
...
```

```markdown rollback.md
...
```
````

The profile still says final response only and no file mutation.

### 2. Draft Extraction

Add `appV2.4/appv24/workflows/drafts.py`.

Responsibilities:

- Extract fenced code blocks from assistant text.
- Recognize package filenames from the fence info string, nearby heading, or first comment line.
- Keep only supported package files:
  - `workflow.yaml`
  - `connectors.yaml`
  - `permissions.yaml`
  - `dry_run_fixtures/*.yaml`
  - `rollback.md`
- Reject duplicate required blocks with a deterministic issue.
- Parse YAML blocks with `yaml.safe_load`.
- Require mapping documents for YAML package files.
- Preserve `rollback.md` as plain text.
- Return a structured draft object and extraction issues without touching the filesystem.

This module should not call the model, run workflows, or write files.

### 3. In-Memory Validation

Extend `appV2.4/appv24/workflows/validator.py` with a document-based API:

```python
def validate_workflow_documents(documents: PackageDocuments) -> WorkflowValidationResult:
    ...
```

`validate_workflow_package(path)` should keep its current behavior and delegate to `validate_workflow_documents()` after loading files.

Add:

```python
def validate_workflow_draft_text(text: str) -> WorkflowDraftValidationResult:
    ...
```

This function should:

1. Extract blocks from assistant text.
2. Parse them into `PackageDocuments` using a virtual root such as `<workflow-draft>`.
3. Run schema validation first.
4. Run semantic validation only if schema passes.
5. Return both extraction issues and workflow validation issues.

### 4. Live Profile Integration

Validation should be profile-specific and opt-in through `workflow-designer-lite`, not global.

For non-interactive prompt mode:

```bash
appv24 --profile workflow-designer-lite "design ..."
```

After printing the assistant draft, CLI should print a short validation report:

```text
Workflow draft validation: passed
```

or:

```text
Workflow draft validation: failed
- schema workflow.yaml:steps.1.uses: ...
- missing_fixture dry_run_fixtures: ...
```

For TUI:

- After the assistant turn finishes, `InteractiveMode` should inspect the latest assistant text only when the selected profile is `workflow-designer-lite`.
- It should render a small validation component below the assistant response.
- It should not append validation output into model context.
- It should store the last parsed valid draft in `InteractiveMode` state for optional user save.

This keeps validation visible to the user without changing the kernel or replaying validation content into provider payloads.

### 5. Explicit Save

Add an explicit TUI/plain command:

```text
/workflow-save relative/path
```

Behavior:

- Only works after a valid workflow draft was produced in the current session.
- Writes only under the active `--cwd`.
- Rejects absolute paths and paths escaping `--cwd`.
- Refuses to overwrite existing files unless a later explicit overwrite command is designed.
- Writes package files using deterministic safe serialization:
  - YAML documents through `yaml.safe_dump(sort_keys=False)`
  - rollback through original text
- Re-validates the saved package from disk after writing.
- If disk validation fails, reports the issue and keeps files for inspection. It must not execute anything.

No save should happen automatically after an LLM response.

### 6. Error Handling

Validation should be non-fatal to the chat loop.

Extraction or validation failures should render as product feedback:

```text
Workflow draft validation: failed
```

The session should continue normally. If the model produces no package blocks, render:

```text
Workflow draft validation: skipped - no workflow package blocks found
```

This avoids turning profile mistakes into TUI crashes.

### 7. Testing Strategy

Add focused tests for:

- Fenced block extraction by info-string labels.
- Fenced block extraction by heading fallback.
- Duplicate block detection.
- YAML parse errors.
- Missing required blocks.
- In-memory valid draft validation.
- In-memory semantic issue reporting.
- Raw secret detection in draft text.
- Non-interactive CLI validation report for workflow profile only.
- TUI post-turn validation component for workflow profile only.
- `/workflow-save` writes a valid draft under cwd.
- `/workflow-save` rejects missing draft, invalid draft, absolute path, parent traversal, and overwrite.

Run existing workflow tests and full appv24 tests before claiming completion.

## Boundary Review

Allowed files:

```text
appV2.4/appv24/workflows/
appV2.4/appv24/profiles/builtins.py
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/
docs/
```

Do not touch:

```text
appV2.4/appv24/agent/agent.py
appV2.4/appv24/agent/agent_loop.py
appV2.4/appv24/agent/types.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/compressor.py
appV2.4/appv24/compaction/timing.py
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

`cli.py` and `tui/interactive_mode.py` are yellow-zone changes and require regression tests.

## Success Criteria

The next phase is successful when:

- A live `workflow-designer-lite` response is automatically validated after the assistant finishes.
- Validation uses the same schema and semantic rules as disk package validation.
- Failed drafts show actionable issues.
- Valid drafts can be explicitly saved by user command.
- No workflow execution, API call, shell command, cloud mutation, or raw secret handling is introduced.
- The full appv24 test suite passes.
