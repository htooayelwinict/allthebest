# appv24 Workflow Designer Lite Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a passive built-in `workflow-designer-lite` profile to appv24 that turns natural-language automation requests into safe workflow-as-code designs in the final response only.

**Architecture:** Extend the existing built-in profile registry without changing runtime control flow. The new profile uses the same read-only tool policy as other lite profiles and appends focused workflow-design instructions through the existing profile prompt path. It does not execute workflows, call external APIs, write package files, request secrets, or introduce LangGraph/n8n/Pipedream runners.

**Tech Stack:** Existing appv24 built-in profile dataclass, profile resolver, argparse CLI, pytest, Markdown docs.

---

## Source Basis

- `rules.md` says new use cases should live in profiles, apps, plugins, examples, docs, or tests instead of the kernel.
- `docs/architecture/appv24-built-in-profiles.md` says v1 profiles are passive startup configuration and do not execute code or change agent loop, provider streaming, compaction, session store, or tool schemas.
- `appV2.4/appv24/profiles/builtins.py` already defines static built-in lite profiles with read-only tools.
- `appV2.4/appv24/profiles/resolver.py` already validates tool references and converts profiles into `CodingApp` kwargs.
- `appV2.4/appv24/cli.py` already supports `--profile` and `--list-profiles`.
- The approved product direction is: profile designs workflows, future apps validate packages, future runners execute approved packages.

## Scope Boundaries

Allowed changes:

- Modify: `appV2.4/appv24/profiles/builtins.py`
- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/tests/test_profiles.py`
- Modify: `appV2.4/tests/test_cli.py`
- Modify: `docs/architecture/appv24-built-in-profiles.md`

Explicitly out of scope:

- Do not modify `appV2.4/appv24/agent/agent.py`.
- Do not modify `appV2.4/appv24/agent/agent_loop.py`.
- Do not modify `appV2.4/appv24/agent/types.py`.
- Do not modify `appV2.4/appv24/ai/types.py`.
- Do not modify `appV2.4/appv24/ai/stream.py`.
- Do not modify `appV2.4/appv24/ai/validation.py`.
- Do not modify `appV2.4/appv24/compaction/`.
- Do not modify `appV2.4/appv24/coding_agent/session_store.py`.
- Do not modify `appV2.4/appv24/app.py`.
- Do not add workflow package files, schema validators, package writers, LangGraph runners, n8n exporters, Pipedream exporters, MCP connector code, direct API connector code, or IaC adapters.
- Do not give the new profile `bash`, `edit`, or `write`.
- Do not run live API calls in tests.
- Do not publish npm or build GHCR.

If implementation appears to require a red-zone or yellow-zone runtime change beyond the files listed above, stop and ask Lewis for explicit approval before continuing.

## File Responsibilities

- `appV2.4/appv24/profiles/builtins.py`: add the `workflow-designer-lite` prompt appendix and profile definition.
- `appV2.4/appv24/cli.py`: include `workflow-designer-lite` in the profile help text.
- `appV2.4/tests/test_profiles.py`: lock the profile contract, tool restrictions, prompt contract, and name normalization.
- `appV2.4/tests/test_cli.py`: lock profile listing and CLI pass-through behavior for the new profile.
- `docs/architecture/appv24-built-in-profiles.md`: document the new built-in profile and its passive boundary.

## Behavioral Contract

The new profile id is:

```text
workflow-designer-lite
```

It must:

- Use only `read`, `grep`, `find`, and `ls`.
- Exclude `bash`, `edit`, and `write`.
- Append system prompt text that makes the agent a passive workflow designer.
- Produce workflow designs in the final response, not files.
- Describe future runner targets such as MCP, direct API, OpenAPI, LangGraph, n8n/Pipedream export, and IaC only as design targets.
- Refuse to execute workflows, call external APIs, request raw secrets, send messages, publish, provision cloud resources, or mutate files.
- Preserve `coding` as the default profile.

## Task 1: Add Profile Contract Tests

**Files:**

- Modify: `appV2.4/tests/test_profiles.py`

- [ ] **Step 1: Extend built-in profile id coverage**

Edit `test_builtin_profile_names_are_unique_and_default_is_coding` so the expected set includes `workflow-designer-lite`:

```python
def test_builtin_profile_names_are_unique_and_default_is_coding() -> None:
    profiles = list_profiles()
    ids = [profile.id for profile in profiles]

    assert DEFAULT_PROFILE_ID == "coding"
    assert ids[0] == "coding"
    assert len(ids) == len(set(ids))
    assert {
        "coding",
        "read-only",
        "planning",
        "research-lite",
        "sre-lite",
        "security-lab-lite",
        "workflow-designer-lite",
    } <= set(ids)
```

- [ ] **Step 2: Add a workflow-designer-lite contract test**

Append this test to `appV2.4/tests/test_profiles.py`:

```python
def test_workflow_designer_lite_profile_is_passive_and_read_only() -> None:
    profile = get_profile("workflow-designer-lite")
    kwargs = profile_coding_app_kwargs(profile)

    assert profile.display_name == "Workflow Designer Lite"
    assert kwargs["allowed_tool_names"] == ["read", "grep", "find", "ls"]
    assert kwargs["excluded_tool_names"] == ["bash", "edit", "write"]
    assert kwargs["max_iterations"] == 60
    assert kwargs["tool_loop_guardrails"] == {"hard_stop_enabled": True}

    prompt = kwargs["append_system_prompt"]
    assert "Profile: workflow-designer-lite." in prompt
    assert "workflow-as-code designs" in prompt
    assert "final response only" in prompt
    assert "Do not run workflows" in prompt
    assert "Do not call external APIs" in prompt
    assert "Do not request raw secrets" in prompt
    assert "Do not edit, write, delete, move, or create files." in prompt
```

- [ ] **Step 3: Extend normalization coverage**

Edit `test_profile_lookup_normalizes_case_and_underscores` to include the new id:

```python
def test_profile_lookup_normalizes_case_and_underscores() -> None:
    assert get_profile("Read_Only").id == "read-only"
    assert get_profile("SECURITY_LAB_LITE").id == "security-lab-lite"
    assert get_profile("WORKFLOW_DESIGNER_LITE").id == "workflow-designer-lite"
```

- [ ] **Step 4: Run the focused profile tests and confirm failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py -q
```

Expected before implementation:

```text
FAILED appV2.4/tests/test_profiles.py::test_builtin_profile_names_are_unique_and_default_is_coding
FAILED appV2.4/tests/test_profiles.py::test_workflow_designer_lite_profile_is_passive_and_read_only
FAILED appV2.4/tests/test_profiles.py::test_profile_lookup_normalizes_case_and_underscores
```

The failure should show that `workflow-designer-lite` is unknown or missing from the built-ins.

## Task 2: Add the Built-In Profile

**Files:**

- Modify: `appV2.4/appv24/profiles/builtins.py`

- [ ] **Step 1: Add the workflow designer prompt appendix**

Insert this constant after `_SECURITY_LAB_LITE_APPEND`:

```python
_WORKFLOW_DESIGNER_LITE_APPEND = (
    "Profile: workflow-designer-lite.\n"
    "Design safe, reviewable workflow-as-code designs from natural language. "
    "Treat every workflow as a passive design artifact, not executable work. "
    "Use local repository files only as evidence when needed. "
    "Produce the workflow design in the final response only. "
    "Do not edit, write, delete, move, or create files. "
    "Do not run shell commands. "
    "Do not run workflows, call external APIs, request raw secrets, send messages, "
    "publish, provision cloud resources, or mutate external systems. "
    "When useful, structure the response with workflow summary, assumptions, "
    "workflow.yaml draft, connectors.yaml draft, permissions.yaml draft, approval gates, "
    "dry-run fixtures, test plan, rollback plan, risk review, and future runner target. "
    "Connector targets may include MCP, direct API, OpenAPI, LangGraph, n8n or Pipedream export, "
    "and IaC, but only as design targets."
)
```

- [ ] **Step 2: Register the built-in profile**

Insert this `AppProfile` after `security-lab-lite` in `BUILTIN_PROFILES`:

```python
    AppProfile(
        id="workflow-designer-lite",
        display_name="Workflow Designer Lite",
        description="Passive natural-language workflow package designer.",
        allowed_tool_names=READ_ONLY_TOOLS,
        excluded_tool_names=MUTATING_TOOLS,
        append_system_prompt=_WORKFLOW_DESIGNER_LITE_APPEND,
        max_iterations=60,
        tool_loop_guardrails={"hard_stop_enabled": True},
    ),
```

- [ ] **Step 3: Run profile tests and confirm pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py -q
```

Expected:

```text
7 passed
```

If the count differs because additional tests already exist, expected result is all tests in `appV2.4/tests/test_profiles.py` passing.

- [ ] **Step 4: Commit the profile registry and tests**

Run:

```bash
git add appV2.4/appv24/profiles/builtins.py appV2.4/tests/test_profiles.py
git commit -m "feat(appv24): add workflow designer lite profile"
```

Expected:

```text
[profile/appv24-built-in-profiles <sha>] feat(appv24): add workflow designer lite profile
```

## Task 3: Update CLI Coverage and Help Text

**Files:**

- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Extend list-profile CLI test**

Edit `test_cli_list_profiles_prints_builtins_without_starting_app` so it also asserts the new profile is listed:

```python
def test_cli_list_profiles_prints_builtins_without_starting_app(monkeypatch, capsys) -> None:
    def fail_startup(*args, **kwargs):
        raise AssertionError("profile listing should not start providers, models, or app")

    monkeypatch.setattr(cli, "register_builtin_providers", fail_startup)
    monkeypatch.setattr(cli, "CodingApp", fail_startup)

    exit_code = cli.main(["--list-profiles"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "coding" in captured.out
    assert "read-only" in captured.out
    assert "security-lab-lite" in captured.out
    assert "workflow-designer-lite" in captured.out
    assert captured.err == ""
```

- [ ] **Step 2: Add CLI pass-through test for workflow-designer-lite**

Append this test near the existing profile CLI tests:

```python
def test_cli_workflow_designer_lite_profile_passes_profile_options_to_coding_app(monkeypatch, tmp_path) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self.messages = []

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    exit_code = cli.main(
        ["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain", "design workflow"]
    )

    kwargs = created["kwargs"]
    assert exit_code == 0
    assert created["prompt"] == "design workflow"
    assert kwargs["allowed_tool_names"] == ["read", "grep", "find", "ls"]
    assert kwargs["excluded_tool_names"] == ["bash", "edit", "write"]
    assert kwargs["max_iterations"] == 60
    assert kwargs["tool_loop_guardrails"] == {"hard_stop_enabled": True}
    assert "workflow-as-code designs" in kwargs["append_system_prompt"]
    assert "final response only" in kwargs["append_system_prompt"]
```

- [ ] **Step 3: Run the focused CLI tests and confirm failure before help update if Task 2 was not done**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_cli.py::test_cli_list_profiles_prints_builtins_without_starting_app \
  appV2.4/tests/test_cli.py::test_cli_workflow_designer_lite_profile_passes_profile_options_to_coding_app \
  -q
```

Expected after Task 2:

```text
2 passed
```

If Task 2 was skipped, expected result is failure because the profile is unknown.

- [ ] **Step 4: Update the CLI help string**

In `appV2.4/appv24/cli.py`, change the `--profile` help text to:

```python
        help=(
            "Built-in app profile to use: coding, read-only, planning, research-lite, "
            "sre-lite, security-lab-lite, workflow-designer-lite"
        ),
```

- [ ] **Step 5: Run CLI profile tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_cli.py::test_cli_list_profiles_prints_builtins_without_starting_app \
  appV2.4/tests/test_cli.py::test_cli_workflow_designer_lite_profile_passes_profile_options_to_coding_app \
  appV2.4/tests/test_cli.py::test_cli_unknown_profile_stops_before_provider_startup \
  -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Commit CLI changes**

Run:

```bash
git add appV2.4/appv24/cli.py appV2.4/tests/test_cli.py
git commit -m "test(appv24): cover workflow designer profile cli"
```

Expected:

```text
[profile/appv24-built-in-profiles <sha>] test(appv24): cover workflow designer profile cli
```

## Task 4: Update Built-In Profile Documentation

**Files:**

- Modify: `docs/architecture/appv24-built-in-profiles.md`

- [ ] **Step 1: Add the profile to the table**

Add this row to the profile table:

```markdown
| `workflow-designer-lite` | Passive workflow-as-code design from natural language | `read`, `grep`, `find`, `ls` |
```

- [ ] **Step 2: Add the CLI example**

Add this command to the CLI examples:

```bash
appv24 --profile workflow-designer-lite --cwd .
```

- [ ] **Step 3: Add a workflow-designer-lite boundary section**

Append this section before `## Override Rules`:

```markdown
## Workflow Designer Lite

`workflow-designer-lite` is a passive compiler-style profile. It designs
workflow packages in the final response, but does not write package files or
execute workflows.

Expected output may include:

```text
workflow summary
assumptions
workflow.yaml draft
connectors.yaml draft
permissions.yaml draft
approval gates
dry-run fixtures
test plan
rollback plan
risk review
future runner target
```

The profile may discuss future adapter targets such as MCP, direct APIs,
OpenAPI, LangGraph, n8n/Pipedream export, and IaC. These are design targets
only in v1.

It must not:

```text
call external APIs
request raw secrets
send messages
publish
provision cloud resources
run workflow steps
create workflow package files
```
```

- [ ] **Step 4: Commit docs**

Run:

```bash
git add docs/architecture/appv24-built-in-profiles.md
git commit -m "docs(appv24): document workflow designer lite profile"
```

Expected:

```text
[profile/appv24-built-in-profiles <sha>] docs(appv24): document workflow designer lite profile
```

## Task 5: Final Verification

**Files:**

- Verify only; no file edits expected.

- [ ] **Step 1: Run focused profile and CLI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_profiles.py \
  appV2.4/tests/test_cli.py::test_cli_list_profiles_prints_builtins_without_starting_app \
  appV2.4/tests/test_cli.py::test_cli_workflow_designer_lite_profile_passes_profile_options_to_coding_app \
  appV2.4/tests/test_cli.py::test_cli_unknown_profile_stops_before_provider_startup \
  -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run the full appv24 Python suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Inspect git diff for boundary compliance**

Run:

```bash
git diff --stat HEAD
git diff -- appV2.4/appv24/profiles/builtins.py appV2.4/appv24/cli.py appV2.4/tests/test_profiles.py appV2.4/tests/test_cli.py docs/architecture/appv24-built-in-profiles.md
```

Expected:

```text
Only the planned profile registry, CLI help, tests, and docs are changed.
No agent loop, provider, compaction, session store, app.py, tool schema, package, npm, or Docker files are changed.
```

- [ ] **Step 4: Commit any final verification-only fix if needed**

If the final verification required a small correction, stage only the intended files:

```bash
git add appV2.4/appv24/profiles/builtins.py appV2.4/appv24/cli.py appV2.4/tests/test_profiles.py appV2.4/tests/test_cli.py docs/architecture/appv24-built-in-profiles.md
git commit -m "fix(appv24): tighten workflow designer profile contract"
```

Expected when a correction was needed:

```text
[profile/appv24-built-in-profiles <sha>] fix(appv24): tighten workflow designer profile contract
```

If no correction was needed, do not create an empty commit.

## Self-Review

- Spec coverage: The plan covers the approved passive profile design, read-only tool boundary, response-only workflow output, connector target language, tests, docs, and CLI discoverability.
- Placeholder scan: The plan contains no placeholder markers, incomplete sections, or generic unexpanded test instructions.
- Type consistency: The profile id is consistently `workflow-designer-lite`; the display name is consistently `Workflow Designer Lite`; the only tools are `read`, `grep`, `find`, and `ls`; mutating tools are `bash`, `edit`, and `write`.
- Boundary check: No red-zone runtime file is planned. No LangGraph, MCP, direct API, OpenAPI adapter, package writer, runner, cloud, npm, or GHCR implementation is planned.
