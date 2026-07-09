# appv24 Workflow Extension Build Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a build-time workflow extension executor so operators can automate coding-profile connector handoff from the TUI/CLI.

**Architecture:** Add a green-zone workflow module that reads handoff manifests, builds package-local connector artifacts from the connector interface contract, captures redacted logs, applies returned QA with the existing validator, and reports final package validation status. Add narrow yellow-zone CLI/TUI command wiring for `/workflow-extension-build`. The coding-profile subprocess runner remains available as an optional runner, but the default MVP path is deterministic contract scaffolding because live testing showed the subprocess can hang or write malformed partial files without returned QA.

**Update:** The executor now also accepts a parent-applied stdout artifact bundle. The coding child prints JSON between appv24 markers; the workflow parent validates the bundle and writes package-local artifacts before applying returned QA. This is the preferred path because real TUI testing showed child free-writes can exit 0 without valid QA.

**Tech Stack:** Python dataclasses, pathlib, subprocess-compatible runner injection, existing appv24 workflow validation and extension handoff APIs, pytest.

---

### Task 1: Build Executor Core

**Files:**
- Create: `appV2.4/appv24/workflows/extension_builder.py`
- Test: `appV2.4/tests/test_workflow_extension_builder.py`

- [ ] **Step 1: Write failing test for successful build/apply**

```python
def test_extension_builder_runs_queued_request_applies_returned_qa_and_revalidates(tmp_path: Path) -> None:
    package = _write_generated_crm_extension_package_without_qa(tmp_path)
    documents = load_package_documents(package)
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)

    def fake_runner(invocation: WorkflowExtensionBuildInvocation) -> WorkflowExtensionBuildCommandResult:
        assert invocation.argv[:3] == (sys.executable, "-m", "appv24.cli")
        assert invocation.prompt_path.is_file()
        qa_path = invocation.qa_return_manifest_path
        qa_path.parent.mkdir(parents=True, exist_ok=True)
        qa_path.write_text(yaml.safe_dump(_passed_crm_extension_qa(), sort_keys=False), encoding="utf-8")
        return WorkflowExtensionBuildCommandResult(returncode=0, stdout="built ok", stderr="")

    report = build_workflow_extension_handoff(package, runner=fake_runner)

    assert report.status == "applied"
    assert report.validation_ok is True
    assert report.built_extensions == ("crm.lookup_contact",)
```

- [ ] **Step 2: Run test and confirm import/function failure**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py -q`

Expected: fail because `appv24.workflows.extension_builder` does not exist.

- [ ] **Step 3: Implement minimal executor**

Implement:

```python
@dataclass(frozen=True)
class WorkflowExtensionBuildInvocation:
    extension_id: str
    package_path: Path
    prompt_path: Path
    qa_return_manifest_path: Path
    argv: tuple[str, ...]

@dataclass(frozen=True)
class WorkflowExtensionBuildCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

def build_workflow_extension_handoff(package_or_manifest, *, runner=None) -> WorkflowExtensionBuildReport:
    resolved = resolve_workflow_extension_handoff(package_or_manifest)
    records = read_workflow_extension_build_records(resolved.manifest_path)
    command_runner = runner or run_workflow_extension_build_invocation
    build_results = run_workflow_extension_build_records(resolved, records, command_runner)
    apply_report = apply_returned_extension_handoff_results(resolved.package_path)
    validation = validate_workflow_package(resolved.package_path)
    return workflow_extension_build_report_from_results(resolved, build_results, apply_report, validation)
```

Resolve the handoff manifest, read dispatch records, build argv from prompt file content without shell, run queued requests, write redacted logs, apply returned QA, revalidate the package.

- [ ] **Step 4: Run test and confirm pass**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py -q`

Expected: pass.

### Task 2: Failure Reporting

**Files:**
- Modify: `appV2.4/appv24/workflows/extension_builder.py`
- Test: `appV2.4/tests/test_workflow_extension_builder.py`

- [ ] **Step 1: Write failing tests for missing QA and failed runner**

```python
def test_extension_builder_blocks_when_runner_does_not_return_qa(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(returncode=0, stdout="no qa", stderr=""),
    )
    assert report.status == "blocked"
    assert any("returned QA manifest was not created" in issue for issue in report.issues)

def test_extension_builder_blocks_on_nonzero_runner_result(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(returncode=2, stdout="", stderr="failed"),
    )
    assert report.status == "blocked"
    assert any("coding profile build failed" in issue for issue in report.issues)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py -q`

Expected: fail until missing QA and nonzero return handling exists.

- [ ] **Step 3: Implement failure statuses and formatter**

Add `format_workflow_extension_build_report(report)` with status, package, handoff manifest, built extensions, applied QA text, validation result, next command, and issues.

- [ ] **Step 4: Run tests and confirm pass**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py -q`

Expected: pass.

### Task 3: CLI/TUI Command Wiring

**Files:**
- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_cli.py`
- Test: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Write failing CLI/TUI command tests**

Add tests that invoke `/workflow-extension-build <package>` with a monkeypatched builder returning a report. Assert the command prints/renders `Workflow extension build: applied`.

- [ ] **Step 2: Run targeted tests and confirm failure**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -k workflow_extension_build -q`

Expected: fail because command is not recognized.

- [ ] **Step 3: Implement command handlers**

Add command detection, help/autocomplete entries, CLI handler, and TUI handler. Resolve the argument as a package or handoff manifest path and call `build_workflow_extension_handoff()`.

- [ ] **Step 4: Run targeted tests and confirm pass**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -k workflow_extension_build -q`

Expected: pass.

### Task 4: Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run extension tests**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py appV2.4/tests/test_workflow_extension_planner.py appV2.4/tests/test_workflow_guided_run.py -q`

- [ ] **Step 2: Run CLI/TUI targeted tests**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -k 'workflow_extension_build or workflow_extension_apply or workflow_extension_dispatch' -q`

- [ ] **Step 3: Run full Python QA**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests`

- [ ] **Step 4: Run whitespace check**

Run: `git diff --check`

### Task 5: Parent-Applied Artifact Bundle

**Files:**
- Modify: `appV2.4/appv24/workflows/extension_builder.py`
- Modify: `appV2.4/appv24/workflows/extension_planner.py`
- Test: `appV2.4/tests/test_workflow_extension_builder.py`
- Test: `appV2.4/tests/test_workflow_extension_planner.py`

- [x] **Step 1: Write failing bundle tests**

Added tests for noisy stdout bundle acceptance, parent-path rejection, extension-root rejection, and invalid Python rejection.

- [x] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py -q`

Observed before implementation: four bundle tests failed because the builder ignored stdout and waited only for returned QA.

- [x] **Step 3: Implement parent-side bundle parser and validator**

Implemented marker extraction, JSON parsing, bundle version/kind/id checks, package-local path checks, extension-root allowlist checks, Python syntax checks, same-content overwrite protection, parent-owned file writes, and QA manifest write to the expected returned-QA path.

- [x] **Step 4: Update PRD prompt**

Added a preferred structured artifact bundle return section with marker names and JSON shape. Direct returned-QA writing remains a fallback.

- [x] **Step 5: Run focused tests**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py appV2.4/tests/test_workflow_extension_planner.py appV2.4/tests/test_workflow_extension_qa.py appV2.4/tests/test_workflow_extension_catalog.py appV2.4/tests/test_workflow_guided_run.py appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -k 'workflow_extension_build or workflow_extension_apply or workflow_extension_dispatch or connector_extension_planner or extension_builder or artifact_bundle or guided_run' -q`

Observed: `29 passed, 439 deselected`.

### Task 6: Bounded TUI Build Progress

**Files:**
- Modify: `appV2.4/appv24/workflows/extension_builder.py`
- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_workflow_extension_builder.py`
- Test: `appV2.4/tests/test_cli.py`
- Test: `appV2.4/tests/test_tui.py`

- [x] **Step 1: Write failing timeout/progress tests**

Added builder, subprocess-runner, CLI, and TUI tests proving timeout status, visible progress events, redacted log path reporting, and blocked validation.

- [x] **Step 2: Confirm failure before implementation**

Observed import/signature failures before adding `WorkflowExtensionBuildEvent`, timeout fields, and progress parameters.

- [x] **Step 3: Implement bounded build execution**

Added `APPV24_WORKFLOW_EXTENSION_BUILD_TIMEOUT_SECONDS` with a 180 second default, subprocess timeout handling, `timed-out` build results, progress events, and timeout lines in the formatter/log output.

- [x] **Step 4: Render CLI/TUI progress**

`/workflow-extension-build` now prints/renders build start, timeout, per-extension progress, log paths, and final blocked/applied report.

- [x] **Step 5: Run focused verification**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py appV2.4/tests/test_cli.py::test_cli_plain_workflow_extension_build_command_invokes_builder appV2.4/tests/test_tui.py::test_interactive_mode_workflow_extension_build_command_invokes_builder appV2.4/tests/test_tui.py::test_interactive_mode_workflow_extension_build_command_renders_progress_timeout -q`

Observed: `13 passed`.

### Task 7: Package-Artifact Builder Correction

**Files:**
- Modify: `appV2.4/appv24/workflows/extension_builder.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_workflow_extension_builder.py`

- [x] **Step 1: Reproduce real TUI failure**

Live TUI `/workflow-extension-build daily-trial-user-digest` with a 240s timeout launched the coding-profile child, but `clearbit.enrich_companies` timed out and `salesforce.query_trial_users` failed after interruption. Disk inspection showed malformed direct-written source files and no returned QA, so the workflow correctly stayed blocked.

- [x] **Step 2: Add regression tests for real artifacts**

Added tests proving the default builder creates package-local connector source, connector tests, returned QA, attached QA, and passes package validation. Added coverage for replacing stale malformed package-local artifacts.

- [x] **Step 3: Implement deterministic contract scaffold builder**

`/workflow-extension-build` now defaults to a green-zone contract scaffold builder that reads `connectors.yaml`, writes connector artifacts under `connectors/<connector>/`, runs local tests, writes real fingerprinted `appv24.extension_qa/v1`, applies returned QA, and revalidates the package. The optional coding-profile subprocess runner remains available for injected/advanced use.

- [x] **Step 4: Demonstrate in actual TUI**

Ran `/workflow-extension-build daily-trial-user-digest` in the app TUI. It generated QA evidence for `clearbit.enrich_companies` and `salesforce.query_trial_users`, applied returned QA, and printed `validation: passed`.

- [x] **Step 5: Verify focused tests**

Run: `PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_extension_builder.py -q`

Observed: `12 passed`.
