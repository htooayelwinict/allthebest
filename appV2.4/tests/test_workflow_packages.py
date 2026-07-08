from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.schema import validate_schema_documents
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.semantic import validate_semantics
from appv24.workflows import WorkflowValidationResult, validate_workflow_package


def test_workflow_validator_dependencies_are_importable() -> None:
    import yaml
    from jsonschema import Draft202012Validator

    assert yaml.safe_load("enabled: true\n") == {"enabled": True}
    assert Draft202012Validator.META_SCHEMA["$id"].endswith("/draft/2020-12/schema")


def write_minimal_package(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workflow.yaml").write_text(
        """
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved issue.
design:
  intent: Review dependency alerts and draft a human-approved issue.
  trigger: Weekly scheduled dependency alert review.
  inputs:
    - GitHub dependency alert list.
  outputs:
    - Human-reviewed issue draft.
  assumptions:
    - GitHub credentials are provided by secret reference at execution time.
  side_effects:
    - Create a GitHub issue only after explicit human approval.
  approval_gates:
    - human-review
  failure_modes:
    - GitHub API unavailable.
    - Human rejects the issue draft.
  rollback:
    - Close the drafted issue manually if it was created.
triggers:
  - id: weekly
    type: schedule
    schedule: "0 9 * * 5"
entrypoint: inspect_alerts
steps:
  - id: inspect_alerts
    uses: github.list_dependabot_alerts
    next: draft_issue
  - id: draft_issue
    uses: github.create_issue
    side_effect_class: create
    approval: human-review
    next: done
  - id: done
    type: terminal
""".lstrip(),
        encoding="utf-8",
    )
    (root / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors:
  - id: github
    adapter: openapi
    description: GitHub REST API.
    actions:
      - id: list_dependabot_alerts
        operationId: dependabot_list_alerts
        side_effect: false
        required_scopes:
          - dependabot:read
      - id: create_issue
        operationId: issues_create
        side_effect: true
        side_effect_class: create
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        idempotency:
          key_fields:
            - repo
            - alert_id
        outputs:
          allowed:
            - issue_number
            - issue_url
        required_scopes:
          - issues:write
""".lstrip(),
        encoding="utf-8",
    )
    (root / "permissions.yaml").write_text(
        """
version: appv24.permissions/v1
approvals:
  - id: human-review
    mode: explicit
    required_for:
      - draft_issue
permissions:
  - connector: github
    actions:
      - list_dependabot_alerts
      - create_issue
    scopes:
      - dependabot:read
      - issues:write
""".lstrip(),
        encoding="utf-8",
    )
    fixtures = root / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  repo: owner/project\n", encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text("approval:\n  human-review: rejected\n", encoding="utf-8")
    (root / "rollback.md").write_text("# Rollback\n\nClose the drafted issue if it was created.\n", encoding="utf-8")


def write_intelligence_package(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workflow.yaml").write_text(
        """
version: appv24.workflow/v1
name: financial-catalyst-alerts
description: Extract informational financial catalysts and draft reviewable alerts.
design:
  intent: Watch public sources, extract catalysts, block trading advice, and draft an alert.
  trigger: Scheduled public-source scan.
  inputs:
    - Public company news and filing items.
  outputs:
    - Reviewable informational alert draft with source links.
  assumptions:
    - Sources are public and timestamped.
    - Alerts are informational and not financial advice.
  side_effects:
    - Create a local notification draft only after explicit approval.
  approval_gates:
    - alert-draft-review
  failure_modes:
    - Source fetch fails.
    - LLM output violates the compact contract.
    - Policy rejects advice-like content.
  rollback:
    - Delete the local draft manually if it was created by mistake.
triggers:
  - id: market_watch
    type: schedule
    schedule: "*/30 * * * *"
entrypoint: fetch_news
steps:
  - id: fetch_news
    uses: source.fetch_items
    with:
      query: public company catalysts
      tickers:
        - NVDA
        - MSFT
    next: extract_catalysts
  - id: extract_catalysts
    uses: llm.extract_structured
    with:
      task: extract_financial_catalysts
      input_ref: fetch_news.items
      output_contract: event_alerts_v1
      safety:
        disallow:
          - personalized_financial_advice
          - buy_sell_hold_recommendation
          - price_target
      model:
        temperature: 0
        max_tokens: 2000
    side_effect_class: read
    next: check_policy
  - id: check_policy
    uses: policy.check
    with:
      input_ref: extract_catalysts.alerts
      checks:
        require_source_urls: true
        require_source_timestamps: true
        disallow_financial_advice: true
        min_confidence: medium
    side_effect_class: read
    next: create_alert_draft
  - id: create_alert_draft
    uses: notification.create_draft
    side_effect_class: create
    approval: alert-draft-review
    next: done
  - id: done
    type: terminal
""".lstrip(),
        encoding="utf-8",
    )
    (root / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors:
  - id: source
    adapter: direct_api
    description: Public source reader.
    actions:
      - id: fetch_items
        side_effect: false
        side_effect_class: read
        execution_mode: brokered
        dry_run_supported: true
        outputs:
          allowed:
            - items
            - source_count
        required_scopes:
          - source:read
  - id: llm
    adapter: llm
    description: Typed LLM transform.
    actions:
      - id: extract_structured
        side_effect: false
        side_effect_class: read
        execution_mode: brokered
        dry_run_supported: true
        outputs:
          allowed:
            - alerts
            - confidence_score
            - raw_response
  - id: policy
    adapter: manual
    description: Deterministic policy gate.
    actions:
      - id: check
        side_effect: false
        side_effect_class: read
        execution_mode: brokered
        dry_run_supported: true
        outputs:
          allowed:
            - approved
            - reason
  - id: notification
    adapter: manual
    description: Reviewable notification draft.
    actions:
      - id: create_draft
        side_effect: true
        side_effect_class: create
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        idempotency:
          key_fields:
            - alert_key
        outputs:
          allowed:
            - draft_id
            - artifact_path
        required_scopes:
          - notification:draft
""".lstrip(),
        encoding="utf-8",
    )
    (root / "permissions.yaml").write_text(
        """
version: appv24.permissions/v1
approvals:
  - id: alert-draft-review
    mode: explicit
    required_for:
      - create_alert_draft
permissions:
  - connector: source
    actions:
      - fetch_items
    scopes:
      - source:read
  - connector: llm
    actions:
      - extract_structured
  - connector: policy
    actions:
      - check
  - connector: notification
    actions:
      - create_draft
    scopes:
      - notification:draft
""".lstrip(),
        encoding="utf-8",
    )
    fixtures = root / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text(
        """
input:
  alert_key: NVDA-2026-07-06-guidance
policy:
  check_policy: approved
""".lstrip(),
        encoding="utf-8",
    )
    (fixtures / "rejection_path.yaml").write_text(
        """
input:
  alert_key: NVDA-2026-07-06-rumor
policy:
  check_policy: rejected
""".lstrip(),
        encoding="utf-8",
    )
    (root / "rollback.md").write_text(
        "# Rollback\n\nDelete the locally created notification draft if it was created by mistake.\n",
        encoding="utf-8",
    )


def replace_documents(
    documents: PackageDocuments,
    *,
    workflow: object | None = None,
    connectors: object | None = None,
    permissions: object | None = None,
) -> PackageDocuments:
    return PackageDocuments(
        root=documents.root,
        workflow=documents.workflow if workflow is None else workflow,
        connectors=documents.connectors if connectors is None else connectors,
        permissions=documents.permissions if permissions is None else permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )


def assign_path_value(document: object, path: tuple[str | int, ...], value: object) -> None:
    target = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def assert_has_issue(
    issues: list,
    *,
    file: str,
    code: str = "schema",
    path: tuple[str | int, ...] | None = None,
    location: str | None = None,
    message_substring: str | None = None,
) -> None:
    matches = [issue for issue in issues if issue.file == file and issue.code == code]
    if path is not None:
        matches = [issue for issue in matches if issue.path == path]
    if location is not None:
        matches = [issue for issue in matches if issue.location == location]
    if message_substring is not None:
        matches = [issue for issue in matches if message_substring in issue.message]
    assert matches, f"expected issue not found: file={file!r} code={code!r} path={path!r} location={location!r}"


def test_load_package_documents_reads_required_files(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)

    documents = load_package_documents(package)

    assert documents.root == package.resolve()
    assert documents.workflow["name"] == "dependency-security-review"
    assert documents.connectors["connectors"][0]["id"] == "github"
    assert documents.permissions["approvals"][0]["id"] == "human-review"
    assert sorted(documents.dry_run_fixtures) == ["happy_path.yaml", "rejection_path.yaml"]
    assert documents.rollback_text and "Rollback" in documents.rollback_text


def test_load_package_documents_rejects_missing_required_file(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "workflow.yaml").write_text("version: appv24.workflow/v1\n", encoding="utf-8")
    (package / "connectors.yaml").write_text("version: appv24.connectors/v1\n", encoding="utf-8")

    with pytest.raises(WorkflowPackageError) as excinfo:
        load_package_documents(package)

    assert "missing required file: permissions.yaml" in str(excinfo.value)


def test_load_package_documents_rejects_invalid_utf8_in_workflow(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "workflow.yaml").write_bytes(b"\xff")

    with pytest.raises(WorkflowPackageError) as excinfo:
        load_package_documents(package)

    assert "failed to parse workflow.yaml" in str(excinfo.value)


def test_load_package_documents_rejects_invalid_utf8_in_rollback(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "rollback.md").write_bytes(b"\xff")

    with pytest.raises(WorkflowPackageError) as excinfo:
        load_package_documents(package)

    assert "failed to read rollback.md" in str(excinfo.value)


def test_schema_validation_accepts_minimal_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)

    documents = load_package_documents(package)
    issues = validate_schema_documents(documents)

    assert issues == []


def test_schema_validation_rejects_missing_workflow_entrypoint(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow.pop("entrypoint")

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="schema_required",
        path=("entrypoint",),
        location="workflow.yaml:entrypoint",
        message_substring="'entrypoint' is a required property",
    )
    issue = next(issue for issue in issues if issue.file == "workflow.yaml" and issue.path == ("entrypoint",))
    assert issue.expected == "required property"
    assert issue.repair_hint == "Add workflow.yaml entrypoint."


def test_schema_validation_reports_expected_shape_and_repair_hint(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["triggers"] = "weekly"

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="schema_type",
        path=("triggers",),
        message_substring="'weekly' is not of type 'array'",
    )
    issue = next(issue for issue in issues if issue.file == "workflow.yaml" and issue.path == ("triggers",))
    assert issue.expected == "array"
    assert issue.repair_hint == "Set workflow.yaml triggers to array."


@pytest.mark.parametrize(
    ("attribute", "filename", "expected_version"),
    [
        ("workflow", "workflow.yaml", "appv24.workflow/v1"),
        ("connectors", "connectors.yaml", "appv24.connectors/v1"),
        ("permissions", "permissions.yaml", "appv24.permissions/v1"),
    ],
)
def test_schema_validation_rejects_invalid_version_literal(
    tmp_path: Path, attribute: str, filename: str, expected_version: str
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    document = deepcopy(getattr(documents, attribute))
    document["version"] = "appv24.invalid/v999"

    issues = validate_schema_documents(replace_documents(documents, **{attribute: document}))

    assert_has_issue(issues, file=filename, path=("version",))
    assert any(issue.file == filename and expected_version in issue.message for issue in issues)


def test_schema_validation_rejects_connector_action_missing_id(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][0].pop("id")

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="schema_required",
        path=("connectors", 0, "actions", 0, "id"),
        message_substring="'id' is a required property",
    )


def test_semantic_validation_requires_workflow_design_contract(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow.pop("design")

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="missing_design_contract",
        path=("design",),
        message_substring="workflow.yaml requires design metadata",
    )
    issue = next(issue for issue in issues if issue.code == "missing_design_contract")
    assert issue.expected == (
        "design with intent, trigger, inputs, outputs, assumptions, side_effects, "
        "approval_gates, failure_modes, and rollback"
    )
    assert "Add workflow.yaml design metadata" in issue.repair_hint


def test_semantic_validation_requires_workflow_design_contract_fields(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["design"].pop("failure_modes")

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="missing_design_contract",
        path=("design", "failure_modes"),
        message_substring="workflow.yaml design requires failure_modes",
    )


@pytest.mark.parametrize(
    ("attribute", "filename", "path"),
    [
        ("workflow", "workflow.yaml", ("triggers",)),
        ("workflow", "workflow.yaml", ("steps",)),
        ("connectors", "connectors.yaml", ("connectors",)),
        ("connectors", "connectors.yaml", ("connectors", 0, "actions")),
    ],
)
def test_schema_validation_rejects_empty_required_collections(
    tmp_path: Path, attribute: str, filename: str, path: tuple[str | int, ...]
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    document = deepcopy(getattr(documents, attribute))
    assign_path_value(document, path, [])

    issues = validate_schema_documents(replace_documents(documents, **{attribute: document}))

    assert_has_issue(issues, file=filename, path=path)


def test_schema_validation_accepts_permissions_without_approvals(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    permissions = deepcopy(documents.permissions)
    permissions.pop("approvals")

    issues = validate_schema_documents(replace_documents(documents, permissions=permissions))

    assert issues == []


def test_schema_validation_accepts_permission_without_scopes(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    permissions = deepcopy(documents.permissions)
    permissions["permissions"][0].pop("scopes")

    issues = validate_schema_documents(replace_documents(documents, permissions=permissions))

    assert issues == []


@pytest.mark.parametrize("path", [("name",), ("entrypoint",)])
def test_schema_validation_rejects_empty_workflow_names(
    tmp_path: Path, path: tuple[str | int, ...]
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    assign_path_value(workflow, path, "")

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=path)


def test_schema_validation_rejects_invalid_trigger_type(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["triggers"][0]["type"] = "polling"

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=("triggers", 0, "type"))


def test_schema_validation_rejects_invalid_step_type(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["type"] = "loop"

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=("steps", 0, "type"))


def test_schema_validation_rejects_non_dotted_step_uses(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["uses"] = "github"

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=("steps", 0, "uses"))


def test_schema_validation_rejects_invalid_connector_adapter(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["adapter"] = "sdk"

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(issues, file="connectors.yaml", path=("connectors", 0, "adapter"))


def test_schema_validation_rejects_invalid_action_execution_mode(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1]["execution_mode"] = "automatic"

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(issues, file="connectors.yaml", path=("connectors", 0, "actions", 1, "execution_mode"))


def test_schema_validation_rejects_invalid_action_side_effect_class(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1]["side_effect_class"] = "mutate"

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(issues, file="connectors.yaml", path=("connectors", 0, "actions", 1, "side_effect_class"))


def test_schema_validation_allows_read_only_action_side_effect_class(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][0]["side_effect_class"] = "read"

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert not [
        issue
        for issue in issues
        if issue.file == "connectors.yaml" and issue.path == ("connectors", 0, "actions", 0, "side_effect_class")
    ]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("connectors", 0, "actions", 1, "outputs", "allowed"), "issue_url"),
        (("connectors", 0, "actions", 1, "outputs", "allowed", 0), 123),
        (("connectors", 0, "actions", 1, "idempotency", "key_fields"), "repo"),
        (("connectors", 0, "actions", 1, "idempotency", "key_fields", 0), 123),
    ],
)
def test_schema_validation_rejects_invalid_broker_contract_collection_values(
    tmp_path: Path, path: tuple[str | int, ...], value: object
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    assign_path_value(connectors, path, value)

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(issues, file="connectors.yaml", code="schema_type", path=path)


def test_schema_validation_returns_issues_in_deterministic_order(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["version"] = "appv24.invalid/v999"
    workflow.pop("entrypoint")
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][0].pop("id")

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow, connectors=connectors))

    expected = sorted(
        issues,
        key=lambda issue: (issue.file, tuple(str(part) for part in issue.path), issue.code, issue.message),
    )
    assert issues == expected


def test_semantic_validation_rejects_unknown_transition_target(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("next: done", "next: missing_step", 1),
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(
        issue.code == "unknown_transition" and issue.message == "step draft_issue points to missing_step"
        for issue in issues
    )


def test_semantic_validation_requires_runtime_transition_for_action_steps(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    draft_issue = next(step for step in workflow["steps"] if step["id"] == "draft_issue")
    draft_issue.pop("next")

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="missing_step_transition",
        location="workflow.yaml:steps.draft_issue.next",
        message_substring="non-terminal step draft_issue requires a runtime transition",
    )


def test_semantic_validation_requires_decision_branches(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["entrypoint"] = "authorize_decision"
    workflow["steps"] = [
        {
            "id": "authorize_decision",
            "type": "decision",
            "condition": "check_allowlist.authorized == true",
            "next": "draft_issue",
            "reject_next": "done",
        },
        *workflow["steps"][1:],
    ]

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="missing_decision_branches",
        location="workflow.yaml:steps.authorize_decision.branches",
        message_substring="decision step authorize_decision requires branches",
    )
    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="unsupported_decision_syntax",
        location="workflow.yaml:steps.authorize_decision.reject_next",
        message_substring="reject_next is not a runtime transition",
    )


def test_semantic_validation_requires_fixture_decision_values_for_branching_steps(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["entrypoint"] = "decide_path"
    workflow["steps"] = [
        {
            "id": "decide_path",
            "type": "decision",
            "branches": {
                "approved": "draft_issue",
                "rejected": "done",
            },
        },
        *workflow["steps"][1:],
    ]

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="dry_run_fixtures/happy_path.yaml",
        code="missing_fixture_decision",
        location="dry_run_fixtures/happy_path.yaml:decision.decide_path",
        message_substring="fixture must select a branch for decision step decide_path",
    )
    assert_has_issue(
        issues,
        file="dry_run_fixtures/rejection_path.yaml",
        code="missing_fixture_decision",
        location="dry_run_fixtures/rejection_path.yaml:decision.decide_path",
        message_substring="fixture must select a branch for decision step decide_path",
    )


def test_semantic_validation_rejects_unknown_fixture_decision_branch(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["entrypoint"] = "decide_path"
    workflow["steps"] = [
        {
            "id": "decide_path",
            "type": "decision",
            "branches": {
                "approved": "draft_issue",
                "rejected": "done",
            },
        },
        *workflow["steps"][1:],
    ]
    fixtures = {name: deepcopy(fixture) for name, fixture in documents.dry_run_fixtures.items()}
    fixtures["happy_path.yaml"]["decision"] = {"decide_path": "maybe"}
    fixtures["rejection_path.yaml"]["decision"] = {"decide_path": "rejected"}

    branched_documents = PackageDocuments(
        root=documents.root,
        workflow=workflow,
        connectors=documents.connectors,
        permissions=documents.permissions,
        dry_run_fixtures=fixtures,
        rollback_text=documents.rollback_text,
    )
    issues = validate_semantics(branched_documents)

    assert_has_issue(
        issues,
        file="dry_run_fixtures/happy_path.yaml",
        code="unknown_fixture_decision_branch",
        location="dry_run_fixtures/happy_path.yaml:decision.decide_path",
        message_substring="maybe is not a branch for decision step decide_path",
    )


def test_semantic_validation_rejects_unknown_connector_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("github.create_issue", "github.close_issue"),
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "unknown_action" and "github.close_issue" in issue.message for issue in issues)


def test_semantic_validation_requires_approval_for_mutation(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("    approval: human-review\n", ""),
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "missing_approval" and "draft_issue" in issue.message for issue in issues)


def test_semantic_validation_rejects_raw_secret_values(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    connectors_path = package / "connectors.yaml"
    connectors_path.write_text(
        connectors_path.read_text(encoding="utf-8")
        + "\ncredentials:\n  api_token: sk-or-v1-this-value-must-not-be-inline\n",
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "raw_secret" and issue.file == "connectors.yaml" for issue in issues)


def test_semantic_validation_requires_rollback_and_fixtures_for_mutation(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "rollback.md").unlink()
    (package / "dry_run_fixtures" / "rejection_path.yaml").unlink()

    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "missing_rollback" for issue in issues)
    assert any(issue.code == "missing_fixture" and "rejection_path.yaml" in issue.message for issue in issues)


def test_semantic_validation_requires_approval_to_cover_mutating_step(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    permissions_path = package / "permissions.yaml"
    permissions_path.write_text(
        permissions_path.read_text(encoding="utf-8").replace("      - draft_issue", "      - other_step"),
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "missing_approval" and "draft_issue" in issue.message for issue in issues)


def test_semantic_validation_requires_permission_scopes_for_connector_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    connectors_path = package / "connectors.yaml"
    connectors_path.write_text(
        connectors_path.read_text(encoding="utf-8").replace(
            "          - issues:write\n",
            "          - issues:write\n          - contents:write\n",
        ),
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert any(
        issue.code == "permission_missing_scope" and "create_issue" in issue.message and "contents:write" in issue.message
        for issue in issues
    )


def test_semantic_validation_requires_broker_metadata_for_mutating_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1].pop("execution_mode")

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="missing_side_effect_contract",
        message_substring="github.create_issue requires execution_mode",
    )


@pytest.mark.parametrize(
    ("field", "message_substring"),
    [
        ("approval_required", "approval_required: true"),
        ("dry_run_supported", "dry_run_supported: true"),
    ],
)
def test_semantic_validation_requires_brokered_mutation_booleans(
    tmp_path: Path, field: str, message_substring: str
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1][field] = False

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="invalid_side_effect_contract",
        message_substring=message_substring,
    )


def test_semantic_validation_requires_brokered_outputs_allowed(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1].pop("outputs")

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="missing_side_effect_contract",
        message_substring="github.create_issue requires outputs.allowed",
    )


def test_semantic_validation_rejects_brokered_outputs_wildcard(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1]["outputs"]["allowed"] = ["*"]

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="invalid_side_effect_contract",
        message_substring="github.create_issue outputs.allowed must not include wildcard",
    )


def test_semantic_validation_requires_idempotency_for_brokered_create(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1].pop("idempotency")

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="missing_side_effect_contract",
        message_substring="github.create_issue requires idempotency.key_fields",
    )


def test_semantic_validation_rejects_design_only_action_used_by_workflow_step(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1]["execution_mode"] = "design_only"
    workflow = deepcopy(documents.workflow)

    issues = validate_semantics(replace_documents(documents, workflow=workflow, connectors=connectors))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="design_only_action_used",
        message_substring="draft_issue must not call design-only action github.create_issue",
    )


def test_semantic_validation_rejects_read_only_design_only_action_used_by_workflow_step(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][0]["execution_mode"] = "design_only"
    connectors["connectors"][0]["actions"][0]["side_effect_class"] = "read"

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="design_only_action_used",
        message_substring="inspect_alerts must not call design-only action github.list_dependabot_alerts",
    )


def test_semantic_validation_allows_design_only_action_in_permissions_when_unused(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"].append(
        {
            "id": "telegram",
            "adapter": "openapi",
            "actions": [
                {
                    "id": "send_message",
                    "side_effect": True,
                    "side_effect_class": "send",
                    "execution_mode": "design_only",
                    "approval_required": False,
                    "dry_run_supported": False,
                }
            ],
        }
    )
    permissions = deepcopy(documents.permissions)
    permissions["permissions"].append(
        {
            "connector": "telegram",
            "actions": ["send_message"],
            "scopes": ["send_message"],
            "design_only": True,
        }
    )

    issues = validate_semantics(replace_documents(documents, connectors=connectors, permissions=permissions))

    assert not any(issue.code in {"missing_side_effect_contract", "invalid_side_effect_contract", "design_only_action_used"} for issue in issues)


def test_semantic_validation_rejects_mutating_read_side_effect_class(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][1]["side_effect_class"] = "read"

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="invalid_side_effect_contract",
        message_substring="github.create_issue mutating actions require side_effect_class create, update, delete, send, publish, or provision",
    )


def test_semantic_validation_requires_design_only_permission_marker(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"].append(
        {
            "id": "telegram",
            "adapter": "openapi",
            "actions": [
                {
                    "id": "send_message",
                    "side_effect": True,
                    "side_effect_class": "send",
                    "execution_mode": "design_only",
                    "approval_required": False,
                    "dry_run_supported": False,
                }
            ],
        }
    )
    permissions = deepcopy(documents.permissions)
    permissions["permissions"].append(
        {
            "connector": "telegram",
            "actions": ["send_message"],
            "scopes": ["send_message"],
        }
    )

    issues = validate_semantics(replace_documents(documents, connectors=connectors, permissions=permissions))

    assert_has_issue(
        issues,
        file="permissions.yaml",
        code="invalid_side_effect_contract",
        message_substring="permissions for design-only action telegram.send_message must set design_only: true",
    )


def test_semantic_validation_allows_manual_mutating_action_declared_without_step_use(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"].append(
        {
            "id": "support",
            "adapter": "manual",
            "actions": [
                {
                    "id": "create_ticket",
                    "side_effect": True,
                    "side_effect_class": "create",
                    "execution_mode": "manual",
                    "approval_required": True,
                    "dry_run_supported": False,
                }
            ],
        }
    )

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert not any(issue.code == "invalid_side_effect_contract" and "support.create_ticket" in issue.message for issue in issues)


def test_semantic_validation_allows_token_url_metadata(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    connectors_path = package / "connectors.yaml"
    connectors_path.write_text(
        connectors_path.read_text(encoding="utf-8") + "\noauth:\n  token_url: https://example.test/oauth/token\n",
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert not any(issue.code == "raw_secret" for issue in issues)


def test_semantic_validation_allows_secret_ref_scalar_fields(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    fixture_path = package / "dry_run_fixtures" / "happy_path.yaml"
    fixture_path.write_text(
        "findings:\n"
        "  - category: credential\n"
        "    secretRef: CONNECTOR_CREDENTIAL_REF\n"
        "    checksum_before: abc111\n"
        "    checksum_after: def222\n",
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert not any(issue.code == "raw_secret" for issue in issues)


@pytest.mark.parametrize(
    "rollback_text",
    [
        "# Rollback\n\n```bash\ncurl -X DELETE https://api.example.test/resource/123\n```\n",
        "# Rollback\n\nUse the API example `DELETE /repos/owner/repo/issues/123`.\n",
        "# Rollback\n\nDo not use a GitHub token with `delete_repo` for rollback.\n",
        "# Rollback\n\n```bash\nterraform destroy -target=aws_instance.web\n```\n",
        "# Rollback\n\n```bash\nkubectl delete deployment web\n```\n",
        "# Rollback\n\n```bash\n-H \"Authorization: Bearer ${secrets.GITHUB_TOKEN}\"\n```\n",
    ],
)
def test_semantic_validation_rejects_unsafe_rollback_examples(tmp_path: Path, rollback_text: str) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "rollback.md").write_text(rollback_text, encoding="utf-8")

    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "unsafe_rollback" and issue.file == "rollback.md" for issue in issues)


def test_semantic_validation_allows_manual_rollback_notes(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "rollback.md").write_text(
        "# Rollback\n\n"
        "- Review the drafted issue in the product UI.\n"
        "- Close the drafted issue manually after human approval if it was created by mistake.\n"
        "- Record the reviewer, timestamp, and reason in the audit log.\n",
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert not any(issue.code == "unsafe_rollback" for issue in issues)


def test_semantic_validation_allows_manual_delete_word_in_rollback_notes(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "rollback.md").write_text(
        "# Rollback\n\n"
        "If a design-only Telegram draft is accidentally sent, open the chat manually.\n"
        "If the bot has delete permission, remove the message from the chat using the UI.\n",
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert not any(issue.code == "unsafe_rollback" for issue in issues)


def test_validate_workflow_package_returns_ok_result_for_valid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)

    result = validate_workflow_package(package)

    assert isinstance(result, WorkflowValidationResult)
    assert result.ok is True
    assert result.issues == ()


def test_validate_workflow_documents_returns_ok_for_valid_documents(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)

    from appv24.workflows import validate_workflow_documents

    result = validate_workflow_documents(documents)

    assert result.ok
    assert result.root == documents.root
    assert result.issues == ()


def test_validate_workflow_documents_skips_semantics_when_schema_fails(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow.pop("entrypoint")
    workflow["steps"][0]["next"] = "missing_step"
    broken = replace_documents(documents, workflow=workflow)

    from appv24.workflows import validate_workflow_documents

    result = validate_workflow_documents(broken)

    assert not result.ok
    assert_has_issue(result.issues, file="workflow.yaml", code="schema_required", path=("entrypoint",))
    assert not any(issue.code == "unknown_transition" for issue in result.issues)


def test_validate_workflow_package_skips_semantics_when_schema_issues_exist(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8")
        .replace("entrypoint: inspect_alerts\n", "")
        .replace("github.create_issue", "github.close_issue"),
        encoding="utf-8",
    )

    result = validate_workflow_package(package)

    assert result.ok is False
    assert any(issue.code == "schema_required" for issue in result.issues)
    assert not any(issue.code == "unknown_action" for issue in result.issues)
    assert all(issue.code.startswith("schema") for issue in result.issues)
    assert result.messages() == sorted(result.messages())


def test_validate_workflow_package_collects_semantic_issues_for_schema_valid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("github.create_issue", "github.close_issue"),
        encoding="utf-8",
    )

    result = validate_workflow_package(package)

    assert result.ok is False
    assert any(issue.code == "unknown_action" for issue in result.issues)
    assert result.messages() == sorted(result.messages())


def test_validate_workflow_package_rejects_declared_action_outside_builtin_catalog(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    for filename in ("workflow.yaml", "connectors.yaml", "permissions.yaml"):
        path = package / filename
        path.write_text(
            path.read_text(encoding="utf-8").replace("list_dependabot_alerts", "invented_read"),
            encoding="utf-8",
        )

    result = validate_workflow_package(package)

    assert result.ok is False
    assert_has_issue(
        result.issues,
        file="workflow.yaml",
        code="unknown_catalog_action",
        path=("steps", 0, "uses"),
        message_substring="github.invented_read is not in the built-in workflow capability catalog",
    )


def test_validate_workflow_package_accepts_intelligence_nodes_with_compact_contract(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)

    result = validate_workflow_package(package)

    assert result.ok is True, result.messages()


def test_semantic_validation_requires_llm_output_contract_or_schema(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    llm_step = next(step for step in workflow["steps"] if step["id"] == "extract_catalysts")
    llm_step["with"].pop("output_contract")

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="missing_llm_output_contract",
        path=("steps", "extract_catalysts", "with"),
        message_substring="extract_catalysts requires output_contract or output_schema",
    )


def test_semantic_validation_rejects_unknown_llm_output_contract(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    llm_step = next(step for step in workflow["steps"] if step["id"] == "extract_catalysts")
    llm_step["with"]["output_contract"] = "invented_contract_v1"

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="unknown_llm_output_contract",
        path=("steps", "extract_catalysts", "with", "output_contract"),
        message_substring="unknown LLM output contract: invented_contract_v1",
    )


def test_semantic_validation_rejects_llm_output_contract_and_schema_together(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    llm_step = next(step for step in workflow["steps"] if step["id"] == "extract_catalysts")
    llm_step["with"]["output_schema"] = {"type": "object", "required": ["items"]}

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="ambiguous_llm_output_contract",
        path=("steps", "extract_catalysts", "with"),
        message_substring="extract_catalysts must set only one of output_contract or output_schema",
    )


def test_semantic_validation_rejects_llm_contract_output_allowlist_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    llm_action = connectors["connectors"][1]["actions"][0]
    llm_action["outputs"]["allowed"] = ["items", "confidence"]

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="llm_output_contract_mismatch",
        path=("connectors", "llm", "actions", "extract_structured", "outputs", "allowed"),
        message_substring="llm.extract_structured outputs.allowed must include event_alerts_v1 fields",
    )
    matches = [
        issue
        for issue in issues
        if issue.file == "connectors.yaml"
        and issue.code == "llm_output_contract_mismatch"
        and issue.path == ("connectors", "llm", "actions", "extract_structured", "outputs", "allowed")
    ]
    assert len(matches) == 1


def test_semantic_validation_rejects_source_fetch_items_output_allowlist_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    source_action = connectors["connectors"][0]["actions"][0]
    source_action["outputs"]["allowed"] = ["sender_email", "subject", "body", "timestamp"]

    issues = validate_semantics(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        code="builtin_output_contract_mismatch",
        path=("connectors", "source", "actions", "fetch_items", "outputs", "allowed"),
        message_substring=(
            "source.fetch_items outputs.allowed must include at least one supported top-level field: "
            "artifact_path, items, source_count"
        ),
    )
    matches = [
        issue
        for issue in issues
        if issue.file == "connectors.yaml"
        and issue.code == "builtin_output_contract_mismatch"
        and issue.path == ("connectors", "source", "actions", "fetch_items", "outputs", "allowed")
    ]
    assert len(matches) == 1


def test_semantic_validation_rejects_mutating_llm_node(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    llm_step = next(step for step in workflow["steps"] if step["id"] == "extract_catalysts")
    llm_step["side_effect_class"] = "send"

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="invalid_llm_side_effect",
        path=("steps", "extract_catalysts", "side_effect_class"),
        message_substring="LLM step extract_catalysts must be read-only",
    )


def test_semantic_validation_requires_policy_check_contract(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    policy_step = next(step for step in workflow["steps"] if step["id"] == "check_policy")
    policy_step["with"].pop("checks")

    issues = validate_semantics(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="missing_policy_checks",
        path=("steps", "check_policy", "with", "checks"),
        message_substring="policy.check step check_policy requires with.checks",
    )


def test_semantic_validation_rejects_domain_specific_finance_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    for filename in ("workflow.yaml", "connectors.yaml", "permissions.yaml"):
        path = package / filename
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace("source.fetch_items", "finance.find_catalysts")
            .replace("id: source", "id: finance")
            .replace("connector: source", "connector: finance")
            .replace("fetch_items", "find_catalysts"),
            encoding="utf-8",
        )

    result = validate_workflow_package(package)

    assert result.ok is False
    assert_has_issue(
        result.issues,
        file="workflow.yaml",
        code="unknown_catalog_action",
        message_substring="finance.find_catalysts is not in the built-in workflow capability catalog",
    )


def test_semantic_validation_rejects_unbounded_llm_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    for filename in ("workflow.yaml", "connectors.yaml", "permissions.yaml"):
        path = package / filename
        path.write_text(
            path.read_text(encoding="utf-8").replace("extract_structured", "do_anything"),
            encoding="utf-8",
        )

    result = validate_workflow_package(package)

    assert result.ok is False
    assert_has_issue(
        result.issues,
        file="workflow.yaml",
        code="unknown_catalog_action",
        message_substring="llm.do_anything is not in the built-in workflow capability catalog",
    )


def test_semantic_validation_rejects_collection_value_for_scalar_connector_input(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_intelligence_package(package)
    (package / "workflow.yaml").write_text(
        """
version: appv24.workflow/v1
name: telegram-financial-alerts
description: Draft and send approved financial alerts.
design:
  intent: Draft and send approved financial alerts.
  trigger: Scheduled scan.
  inputs:
    - Subscriber chat IDs.
  outputs:
    - Approved Telegram alert.
  assumptions:
    - Subscribers are fixture input.
  side_effects:
    - Send Telegram alert after approval.
  approval_gates:
    - approve_broadcast
  failure_modes:
    - Broadcast target is invalid.
  rollback:
    - Send a correction manually if needed.
triggers:
  - id: scheduled
    type: schedule
    schedule: "0 9 * * 1-5"
entrypoint: send_broadcast
steps:
  - id: send_broadcast
    uses: telegram.send_message
    side_effect_class: send
    approval: approve_broadcast
    with:
      chat_id: "{{ inputs.subscribers }}"
      text: Market catalyst alert.
    next: done
  - id: done
    type: terminal
""".lstrip(),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors:
  - id: telegram
    adapter: direct_api
    actions:
      - id: send_message
        side_effect: true
        side_effect_class: send
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        idempotency:
          key_fields:
            - chat_id
            - text
        outputs:
          allowed:
            - message_id
            - chat_id
        required_scopes:
          - telegram:send
""".lstrip(),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """
version: appv24.permissions/v1
permissions:
  - connector: telegram
    actions:
      - send_message
    scopes:
      - telegram:send
approvals:
  - id: approve_broadcast
    mode: explicit
    required_for:
      - send_broadcast
""".lstrip(),
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text(
        "inputs:\n  subscribers:\n    - 1000000001\napproval:\n  approve_broadcast: approved\n",
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        code="connector_scalar_input_collection",
        location="workflow.yaml:steps.send_broadcast.with.chat_id",
        message_substring="telegram.send_message chat_id must resolve to a scalar value",
    )


def test_validate_workflow_package_rejects_fixture_only_brave_search_query(tmp_path: Path) -> None:
    package = tmp_path / "ai-news-missing-query-binding"
    package.mkdir()
    (package / "workflow.yaml").write_text(
        """
version: appv24.workflow/v1
name: ai-news-missing-query-binding
description: Search recent AI news.
design:
  intent: Search recent AI news.
  trigger: Manual run.
  inputs:
    - Search query.
  outputs:
    - Search results.
  assumptions:
    - Brave Search is configured.
  side_effects: []
  approval_gates: []
  failure_modes:
    - Search fails.
  rollback:
    - No external mutation occurs.
triggers:
  - id: manual
    type: manual
entrypoint: search_ai_news
steps:
  - id: search_ai_news
    uses: brave.search
    next: done
  - id: done
    type: terminal
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors:
  - id: brave
    adapter: direct_api
    actions:
      - id: search
        side_effect: false
        required_scopes: [brave:search]
        outputs:
          allowed: [items, source_count, query]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """
version: appv24.permissions/v1
permissions:
  - connector: brave
    actions: [search]
    scopes: [brave:search]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text(
        """
version: appv24.fixture/v1
name: happy_path
inputs:
  brave.search:
    query: AI news today
outputs:
  brave.search:
    items: []
    source_count: 0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")

    result = validate_workflow_package(package)

    assert_has_issue(
        result.issues,
        file="workflow.yaml",
        code="missing_connector_required_input",
        location="workflow.yaml:steps.search_ai_news.with.query",
        message_substring="brave.search requires workflow.yaml step search_ai_news to bind query",
    )


def test_ai_news_telegram_registered_users_package_uses_connector_interfaces(tmp_path: Path) -> None:
    package = tmp_path / "ai-news-telegram"
    package.mkdir()
    (package / "workflow.yaml").write_text(
        """
version: appv24.workflow/v1
name: ai-news-telegram-digest
description: Search recent AI news, summarize it, and broadcast to Telegram subscribers.
design:
  intent: Send a Telegram digest of recent AI news to registered subscribers.
  trigger: Manual run.
  inputs:
    - Telegram subscriber connector audience.
  outputs:
    - Telegram broadcast result.
  assumptions:
    - Subscriber connector is configured.
  side_effects:
    - Send Telegram broadcast after approval.
  approval_gates:
    - approve_telegram_broadcast
  failure_modes:
    - Search or broadcast fails.
  rollback:
    - Review Telegram broadcast manually.
triggers:
  - id: manual
    type: manual
entrypoint: search_ai_news
steps:
  - id: search_ai_news
    uses: brave.search
    with:
      query: artificial intelligence news today
      freshness: day
      count: 10
    next: list_subscribers
  - id: list_subscribers
    uses: telegram.list_subscribers
    with:
      audience: default
    next: summarize_digest
  - id: summarize_digest
    uses: llm.summarize
    with:
      text: "{{ steps.search_ai_news.outputs.items }}"
      output_contract: summary_v1
    next: send_digest
  - id: send_digest
    uses: telegram.broadcast_message
    side_effect_class: send
    approval: approve_telegram_broadcast
    with:
      recipients: "{{ steps.list_subscribers.outputs.recipients }}"
      audience_hash: "{{ steps.list_subscribers.outputs.audience_hash }}"
      text: "{{ steps.summarize_digest.outputs.summary }}"
    next: done
  - id: done
    type: terminal
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors:
  - id: brave
    adapter: direct_api
    actions:
      - id: search
        side_effect: false
        required_scopes: [brave:search]
        outputs:
          allowed: [items, source_count, query]
  - id: llm
    adapter: llm
    actions:
      - id: summarize
        side_effect: false
        outputs:
          allowed: [summary, raw_response]
  - id: telegram
    adapter: direct_api
    actions:
      - id: list_subscribers
        side_effect: false
        required_scopes: [telegram:subscribers:read]
        outputs:
          allowed: [recipients, recipient_count, audience_hash]
      - id: broadcast_message
        side_effect: true
        side_effect_class: send
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        required_scopes: [telegram:send]
        idempotency:
          key_fields: [audience_hash, text]
        outputs:
          allowed: [sent_count, message_ids, chat_ids]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """
version: appv24.permissions/v1
permissions:
  - connector: brave
    actions: [search]
    scopes: [brave:search]
  - connector: llm
    actions: [summarize]
    scopes: []
  - connector: telegram
    actions: [list_subscribers, broadcast_message]
    scopes: [telegram:subscribers:read, telegram:send]
approvals:
  - id: approve_telegram_broadcast
    mode: explicit
    required_for: [send_digest]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    fixture = "input:\n  audience: default\n"
    (fixtures / "happy_path.yaml").write_text(fixture, encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text(fixture, encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nReview Telegram broadcast manually.\n", encoding="utf-8")

    result = validate_workflow_package(package)

    assert result.ok, result.messages()


def test_validate_workflow_package_returns_schema_issue_for_structurally_invalid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors: null
""".lstrip(),
        encoding="utf-8",
    )

    try:
        result = validate_workflow_package(package)
    except Exception as error:
        pytest.fail(f"validate_workflow_package raised {error!r}")

    assert isinstance(result, WorkflowValidationResult)
    assert result.ok is False
    assert any(issue.code == "schema_type" for issue in result.issues)
    assert all(issue.code.startswith("schema") for issue in result.issues)


def test_validate_workflow_package_load_error_uses_resolved_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = validate_workflow_package(Path("missing-package"))

    assert result.ok is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.code == "load_error"
    assert issue.file == str(result.root)


def test_example_dependency_security_review_package_is_valid() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/dependency-security-review"

    result = validate_workflow_package(package)

    assert result.ok is True, result.messages()


def test_example_financial_catalyst_alerts_package_is_valid() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/financial-catalyst-alerts"

    result = validate_workflow_package(package)

    assert result.ok is True, result.messages()


def test_example_public_news_digest_package_is_valid() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/public-news-digest"

    result = validate_workflow_package(package)

    assert result.ok is True, result.messages()


def test_example_telegram_support_ticket_package_is_valid() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/telegram-support-ticket"

    result = validate_workflow_package(package)

    assert result.ok is True, result.messages()
